"""
main.py
=======
FastAPI Application Entry Point for trans-pdf.
Designed & built with high-throughput streaming and clean async handling.

FIX P1: job queue + WebSocket progress (pipeline bất đồng bộ, không giữ HTTP dài).
FIX P2: cache stats + preview 1 trang + layout QA/editor hậu kỳ.
"""

import asyncio
import gc
import io
import os
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, status, Query, Body, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Load .env file if available
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("trans_pdf.api")

from services.pdf_translator import PDFTranslator
from services.llm_service import LLMService
from services.job_queue import JobManager, QueueFullError, STATUS_DONE, STATUS_ERROR, STATUS_PROCESSING, STATUS_QUEUED, STATUS_CANCELLED
from services.layout_qa import build_qa_report
from services.safety import sanitize_error, new_request_id, ERR_LLM_UNAVAILABLE, ERR_BUSY, PdfTooLargeError

# Instantiate engine singleton
llm_service = LLMService()
translator = PDFTranslator(llm_service=llm_service)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
ALLOWED_LANGUAGES = {
    "Vietnamese", "English", "Japanese", "Korean",
    "Chinese", "French", "German", "Spanish",
}

# FIX P0: CONCURRENCY LIMIT — chống OOM khi nhiều người upload cùng lúc.
# Mỗi PDF dịch giữ nguyên cả doc + scratch_doc + ảnh trong RAM (rất nặng),
# nên phải giới hạn số request xử lý ĐỒNG THỜI. Request vượt quá sẽ chờ tối đa
# ACQUIRE_TIMEOUT giây, quá hạn trả 429 để Frontend báo "hệ thống bận".
def _get_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default

MAX_CONCURRENT_TRANSLATIONS = _get_int_env("MAX_CONCURRENT_TRANSLATIONS", 2)
ACQUIRE_TIMEOUT_SEC = float(os.getenv("TRANSLATE_ACQUIRE_TIMEOUT_SEC", "30"))
# Đợt 1: MỘT semaphore chung cho cả endpoint sync (/translate, /preview)
# lẫn worker job — tổng PDF nặng cùng lúc không vượt trần RAM.
# Giữ cả hai endpoint theo yêu cầu, nhưng chung một ngân sách.
translate_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TRANSLATIONS)
# FIX P1: JobManager dùng chung translator (đã có cache + OCR + QA)
# và semaphore chung để chống OOM.
job_manager = JobManager.from_env(translator, heavy_slot=translate_semaphore)
# FIX P0: bộ đếm quan sát (hiện ở /api/health) — không dùng để khóa, chỉ để log/monitor.
active_translations = 0

# FIX P0: nhận diện production để health-check báo rõ mock đã bị tắt.
ENV_NAME = os.getenv("ENV", "development")


# FIX P1: lifespan — khởi động/tắt worker nền + cleaner đúng vòng đời app.
@asynccontextmanager
async def lifespan(app: FastAPI):
    await job_manager.start()
    logger.info("[FIX P1] lifespan startup: job workers ready.")
    yield
    await job_manager.stop()
    logger.info("[FIX P1] lifespan shutdown: job workers stopped.")


app = FastAPI(
    title="trans-pdf API",
    description="High-fidelity Layout-Preserving PDF Translation Engine (Powered by PyMuPDF & LLM)",
    version="1.1.0",
    lifespan=lifespan,
)

# Enable CORS for Next.js Frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "FRONTEND_ORIGINS", "http://localhost:3000"
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_id_middleware(request, call_next):
    """Đợt 1: gắn X-Request-ID cho mọi response để nối log với báo lỗi client.
    Đợt C: bọc cả exception chưa được xử lý — middleware này nằm trong
    ServerErrorMiddleware nên crash (KeyError, AttributeError...) đi xuyên qua
    mà không có header. try/except ở đây đảm bảo 500 nào cũng có mã tra cứu."""
    req_id = request.headers.get("X-Request-ID") or new_request_id()
    try:
        response = await call_next(request)
    except Exception as e:
        logger.exception("Unhandled error [%s]: %s", req_id, sanitize_error(e))
        response = JSONResponse(
            status_code=500,
            content={"detail": f"Lỗi hệ thống. Mã tra cứu: {req_id}."},
        )
    response.headers["X-Request-ID"] = req_id
    return response


def _validate_lang(target_lang: str, source_lang: str) -> None:
    if target_lang not in ALLOWED_LANGUAGES:
        raise HTTPException(400, "Ngôn ngữ đích không được hỗ trợ.")
    if source_lang != "Auto" and not re.fullmatch(r"[A-Za-z]{2,32}", source_lang):
        raise HTTPException(400, "Ngôn ngữ nguồn không hợp lệ.")


async def _read_upload_capped(file: UploadFile) -> bytes:
    """Đọc upload tối đa MAX_UPLOAD_BYTES, ném 413 nếu vượt."""
    try:
        chunks = []
        bytes_read = 0
        while chunk := await file.read(1024 * 1024):
            bytes_read += len(chunk)
            if bytes_read > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Kích thước tệp vượt quá giới hạn cho phép (tối đa 50MB).",
                )
            chunks.append(chunk)
        return b"".join(chunks)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lỗi khi đọc file upload: {e}")
        raise HTTPException(400, "Không thể đọc nội dung file tải lên.")


@app.get("/api/health")
async def health_check():
    """Endpoint kiểm tra tình trạng hệ thống, font nhúng và LLM providers."""
    gemini_ready = bool(os.getenv("GEMINI_API_KEY"))
    openai_ready = bool(os.getenv("OPENAI_API_KEY"))
    is_production = ENV_NAME.strip().lower() in ("production", "prod")
    # FIX P0: mock chỉ tồn tại ở dev khi ALLOW_MOCK_FALLBACK=true; production luôn tắt.
    mock_allowed = (not is_production) and os.getenv("ALLOW_MOCK_FALLBACK", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )

    # Đợt C: chỉ lộ tên file font, không lộ đường dẫn tuyệt đối (fingerprint OS).
    font_name = os.path.basename(translator.font_path) if translator.font_path else "System Default"
    # Đợt C: cộng cả job đang processing (giữ shared semaphore) chứ không chỉ sync.
    in_flight = active_translations + job_manager.processing_count()

    return {
        "status": "healthy",
        "engine": "PyMuPDF (fitz)",
        "env": ENV_NAME,
        "font_path": font_name,
        # FIX P0: lộ trạng thái concurrency để ops biết server đang nghẽn ở đâu.
        "concurrency": {
            "max_concurrent_translations": MAX_CONCURRENT_TRANSLATIONS,
            "active_translations": in_flight,
            "available_slots": max(0, MAX_CONCURRENT_TRANSLATIONS - in_flight),
        },
        # FIX P1: hàng đợi job.
        "jobs": job_manager.queue_stats(),
        # FIX P2: cache hit-rate để đo hiệu quả tiết kiệm token.
        "cache": llm_service.cache_stats(),
        # FIX P1: trạng thái OCR (có binary thật không, hay chỉ graceful-off).
        "ocr": translator.ocr.status(),
        "llm_providers": {
            "gemini": "Active" if gemini_ready else "Not Configured",
            "openai": "Active" if openai_ready else "Not Configured",
            # FIX P0: báo trung thực — production không còn fallback mock.
            "fallback_mode": (
                "Disabled (production strict)"
                if is_production
                else ("Enabled (dev mock)" if mock_allowed else "Disabled")
            ),
        },
    }


@app.post("/api/translate")
async def translate_pdf_endpoint(
    file: UploadFile = File(..., description="File PDF cần dịch"),
    target_lang: str = Form("Vietnamese", description="Ngôn ngữ đích (vd: Vietnamese, English, Japanese)"),
    source_lang: str = Form("Auto", description="Ngôn ngữ gốc (mặc định: Auto)"),
):
    """
    Nhận file PDF tải lên, tiến hành bóc tách layout, bảo vệ công thức toán LaTeX,
    dịch nội dung, tính toán bounding box và trả về file PDF đã giữ nguyên định dạng 100%.
    GIỮ LẠI cho tương thích cũ. Tài liệu lớn / cần progress real-time => dùng POST /api/jobs + WS.
    """
    global active_translations
    filename = file.filename or ""
    logger.info("[API] Nhận yêu cầu dịch file: %s -> %s", filename, target_lang)

    # 1. Kiểm tra định dạng file
    if not filename.lower().endswith(".pdf"):
        # FIX P0: đóng upload handle ngay cả khi validate fail sớm.
        try:
            await file.close()
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File tải lên bắt buộc phải có định dạng .pdf",
        )

    try:
        _validate_lang(target_lang, source_lang)
    except HTTPException as e:
        try:
            await file.close()
        except Exception:
            pass
        raise e

    # FIX P0: CONCURRENCY LIMIT — xếp hàng có timeout, quá tải trả 429 tử tế
    # thay vì nhận hết rồi OOM chết cả server.
    # Đợt A: dùng asyncio.timeout + cờ acquired thay cho wait_for(acquire()).
    # wait_for có khe hở: acquire() thành công đúng lúc hết giờ thì TimeoutError
    # vẫn ném ra nhưng slot đã bị chiếm, nhánh except không trả — rò đủ số lần
    # là mọi request treo 429 mãi. Với timeout, cờ chỉ bật sau khi acquire()
    # trả về đồng bộ (không có điểm suspend ở giữa) nên không thể vừa giữ slot
    # vừa báo timeout mà không trả.
    acquired = False
    try:
        async with asyncio.timeout(ACQUIRE_TIMEOUT_SEC):
            await translate_semaphore.acquire()
            acquired = True
    except TimeoutError:
        if acquired:
            try:
                translate_semaphore.release()
            except Exception:
                pass
        try:
            await file.close()
        except Exception:
            pass
        logger.warning("[FIX P0] Từ chối request %s: hết slot dịch (max=%d).", filename, MAX_CONCURRENT_TRANSLATIONS)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Hệ thống đang bận xử lý nhiều tài liệu cùng lúc. "
                "Vui lòng đợi vài giây rồi thử lại."
            ),
        )
    active_translations += 1
    request_start = time.monotonic()

    # FIX P0: content khởi tạo None để finally không NameError dù đọc upload nổ sớm.
    content: bytes | None = None
    translated_pdf_bytes: bytes | None = None
    try:
        # 2. Đọc có giới hạn để không giữ một upload tùy ý trong RAM.
        content = await _read_upload_capped(file)

        # 3. Kiểm tra magic bytes PDF (%PDF-)
        if not content.startswith(b"%PDF-"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Nội dung file không phải là cấu trúc PDF hợp lệ (thiếu header %PDF-).",
            )

        # 4. Chạy tiến trình dịch thuật qua PyMuPDF & LLM Engine
        try:
            translated_pdf_bytes = await translator.translate_pdf(
                input_pdf_bytes=content,
                target_lang=target_lang,
                source_lang=source_lang,
            )
        except RuntimeError as e:
            # FIX P0: TẮT MOCK FALLBACK — thiếu key/provider ở production tới đây.
            # Đợt 1: log đã che key, client nhận mã lỗi chung + mã request để tra log.
            req_id = new_request_id()
            logger.error("[FIX P0] LLM unavailable [%s]: %s", req_id, sanitize_error(e))
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Dịch vụ dịch thuật tạm thời không khả dụng. "
                    f"Mã tra cứu: {req_id}. Vui lòng thử lại sau."
                ),
            )
        except ValueError as e:
            # Đợt 2: PDF vượt trần trang/block cũng là ValueError (PdfTooLargeError)
            # thì trả 413 kèm hướng dẫn chia file thay vì 400 chung chung.
            if isinstance(e, PdfTooLargeError):
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=str(e),
                )
            # PDF hỏng/mã hoá/mở thất bại — lỗi phía client gửi file xấu.
            logger.warning("[FIX P0] PDF đầu vào không hợp lệ: %s", sanitize_error(e))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File PDF không hợp lệ hoặc không đọc được.",
            )
        except HTTPException:
            raise
        except Exception as e:
            # Đợt 1: traceback chỉ nằm ở log server, client nhận mã chung.
            req_id = new_request_id()
            logger.exception("[Ponytail Error] Quá trình dịch PDF thất bại [%s]: %s", req_id, sanitize_error(e))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Lỗi xử lý PDF nội bộ. Mã tra cứu: {req_id}.",
            )

        # 5. Đặt tên file xuất an toàn (sanitize để chống header injection/path traversal)
        original_stem = os.path.splitext(os.path.basename(filename))[0]
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", original_stem).strip("._") or "translated"
        out_filename = f"[{target_lang[:2].upper()}]_{safe_stem}.pdf"

        # FIX P0: copy bytes ra biến cục bộ rồi xoá tham chiếu gốc trước khi stream,
        # để finally có thể giải phóng upload gốc ngay.
        payload = bytes(translated_pdf_bytes)
        elapsed = time.monotonic() - request_start
        logger.info(
            "[FIX P0] Dịch xong %s -> %s (%.1fs, in=%dB out=%dB, active=%d/%d).",
            filename, out_filename, elapsed, len(content), len(payload),
            active_translations, MAX_CONCURRENT_TRANSLATIONS,
        )
        # 6. Trả về file Stream trực tiếp cho Client download
        return StreamingResponse(
            io.BytesIO(payload),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{out_filename}"',
                "Access-Control-Expose-Headers": "Content-Disposition",
            },
        )
    finally:
        # FIX P0: MEMORY LEAK & FILE CLEANUP — khối chặt chẽ, chạy MỌI trường hợp:
        # - Đóng upload handle (Starlette temp file) để không treo file descriptor.
        # - Xoá tham chiếu bytes nặng (upload gốc + bản dịch) + gc.collect() để
        #   trả RAM ngay thay vì chờ GC chu kỳ. Không ghi file tạm ra ổ cứng
        #   (in-memory hoàn toàn) nên không còn rác input/output trên disk.
        # - Nhả Semaphore + giảm bộ đếm active để slot luôn được hồi phục,
        #   kể cả khi request văng lỗi giữa chừng (tránh dead-lock hết slot).
        try:
            await file.close()
        except Exception as close_e:
            logger.warning("[FIX P0] Lỗi khi close upload file: %s", close_e)
        try:
            content = None  # type: ignore[assignment]
            translated_pdf_bytes = None  # type: ignore[assignment]
        except Exception:
            pass
        try:
            gc.collect()
        except Exception:
            pass
        try:
            translate_semaphore.release()
        except Exception as sem_e:
            logger.warning("[FIX P0] Lỗi khi release semaphore: %s", sem_e)
        active_translations = max(0, active_translations - 1)


# ==============================================================================
# FIX P1: JOB QUEUE API — pipeline bất đồng bộ + progress
# ==============================================================================

@app.post("/api/jobs", status_code=202)
async def create_job(
    file: UploadFile = File(..., description="File PDF cần dịch"),
    target_lang: str = Form("Vietnamese"),
    source_lang: str = Form("Auto"),
):
    """
    FIX P1: nhận PDF, trả ngay job_id (202). Client poll GET /api/jobs/{id}
    hoặc subscribe WS /ws/jobs/{id} để nhận progress real-time, xong thì
    GET /api/jobs/{id}/download. Không giữ HTTP connection dài như /api/translate.
    """
    filename = file.filename or "upload.pdf"
    if not filename.lower().endswith(".pdf"):
        try:
            await file.close()
        except Exception:
            pass
        raise HTTPException(400, "File tải lên bắt buộc phải có định dạng .pdf")
    try:
        _validate_lang(target_lang, source_lang)
    except HTTPException as e:
        try:
            await file.close()
        except Exception:
            pass
        raise e
    try:
        content = await _read_upload_capped(file)
    finally:
        try:
            await file.close()
        except Exception:
            pass
    if not content.startswith(b"%PDF-"):
        raise HTTPException(400, "Nội dung file không phải PDF hợp lệ (thiếu header %PDF-).")
    try:
        job = await job_manager.submit(content, filename, target_lang, source_lang)
    except QueueFullError as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
    return {
        "job_id": job.id,
        "status": job.status,
        "progress": job.progress,
        "poll_url": f"/api/jobs/{job.id}",
        "ws_url": f"/ws/jobs/{job.id}",
        "download_url": f"/api/jobs/{job.id}/download",
    }


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, include_layout: bool = Query(False, description="Kèm pages_layout cho editor (nặng)")):
    """FIX P1: poll trạng thái job (thay WS nếu client không hỗ trợ WebSocket)."""
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(404, "Không tìm thấy job (có thể đã hết TTL và bị dọn).")
    return job.to_public_dict(include_layout=include_layout)


@app.get("/api/jobs/{job_id}/download")
async def download_job(job_id: str):
    """FIX P1: tải PDF kết quả khi job done."""
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(404, "Không tìm thấy job.")
    if job.status == STATUS_QUEUED or job.status == STATUS_PROCESSING:
        raise HTTPException(409, f"Job đang {job.status} ({job.progress}%) — vui lòng đợi.")
    if job.status == STATUS_CANCELLED:
        raise HTTPException(409, "Job đã bị hủy.")
    if job.status == STATUS_ERROR or job.result_bytes is None:
        # Đợt C: phân biệt lỗi để monitoring/retry đúng — hết LLM là 503,
        # file quá khổ là 413, còn lại là 422 (lỗi từ dữ liệu vào, không phải bug).
        code = (job.error_code or "").lower()
        if code == "llm_unavailable":
            raise HTTPException(503, f"Dịch vụ dịch thuật tạm không khả dụng: {job.error or ''}".strip())
        if code == "too_large":
            raise HTTPException(413, job.error or "File vượt giới hạn.")
        raise HTTPException(422, f"Job thất bại: {job.error or 'không rõ lỗi'}")
    dl_name = job.output_filename or (job_id + ".pdf")
    return StreamingResponse(
        io.BytesIO(bytes(job.result_bytes)),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{dl_name}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    """Đợt 2: hủy thật — job đang chạy thì dừng sau trang hiện tại,
    job xong/hỏng thì xóa record giải phóng RAM ngay (không chờ TTL)."""
    if job_manager.get(job_id) is None:
        raise HTTPException(404, "Không tìm thấy job.")
    job = await job_manager.cancel(job_id)
    if job is not None:
        # Job đang queued/processing: đã đặt cờ hủy, worker sẽ dừng sau trang hiện tại.
        # Đợt C: một dạng response duy nhất {action} để client không rẽ nhánh sai.
        return {"ok": True, "job_id": job_id, "action": "cancelled", "status": job.status}
    gc.collect()
    return {"ok": True, "job_id": job_id, "action": "deleted", "status": "gone"}


@app.websocket("/ws/jobs/{job_id}")
async def ws_job_progress(ws: WebSocket, job_id: str):
    """
    FIX P1: WebSocket progress real-time.
    Client connect -> nhận ngay snapshot hiện tại -> nhận update mỗi khi worker
    gọi progress_callback. Job xong/thất bại vẫn giữ socket mở để client đọc
    trạng thái cuối (client tự close sau khi progress==100 hoặc status cuối).
    """
    await ws.accept()
    job = job_manager.get(job_id)
    if job is None:
        await ws.send_json({"job_id": job_id, "status": "not_found", "error": "Job không tồn tại."})
        await ws.close(code=4404)
        return
    await job_manager.subscribe(job_id, ws)
    try:
        # Đợt C: bọc cả snapshot đầu — client đóng ngay khi vừa nối không còn
        # gây traceback 1011, unsubscribe trong finally vẫn chạy.
        try:
            await ws.send_json(job.to_public_dict())
        except Exception:
            return
        while True:
            # Đợt 2: heartbeat 25s để proxy/load balancer không cắt kết nối
            # rảnh, đồng thời phát hiện client ngắt (receive_text raise khi close).
            # Đợt C: heartbeat kèm đủ trạng thái để client cũ (không lọc type)
            # cũng không bị reset tiến trình.
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=25.0)
            except asyncio.TimeoutError:
                try:
                    await ws.send_json({**job.to_public_dict(), "type": "heartbeat"})
                except Exception:
                    break
                continue
            except WebSocketDisconnect:
                break
            except Exception:
                break
    finally:
        await job_manager.unsubscribe(job_id, ws)


# ==============================================================================
# FIX P2: PREVIEW 1 TRANG — dịch thử rẻ token trước khi chạy cả tài liệu
# ==============================================================================

@app.post("/api/preview")
async def preview_page(
    file: UploadFile = File(...),
    target_lang: str = Form("Vietnamese"),
    source_lang: str = Form("Auto"),
    page: int = Form(1, description="Số trang 1-based (1 = trang đầu)"),
):
    """
    FIX P2: dịch duy nhất 1 trang để user xem trước chất lượng/layout.
    Dùng TranslationCache nên lần preview 2 cùng câu gần như tức thì.
    QA summary trả qua header X-QA-* để client không cần gọi thêm.
    """
    global active_translations
    filename = file.filename or "preview.pdf"
    if not filename.lower().endswith(".pdf"):
        try:
            await file.close()
        except Exception:
            pass
        raise HTTPException(400, "File phải có định dạng .pdf")
    try:
        _validate_lang(target_lang, source_lang)
    except HTTPException as e:
        try:
            await file.close()
        except Exception:
            pass
        raise e
    if page < 1 or page > 5000:
        try:
            await file.close()
        except Exception:
            pass
        raise HTTPException(400, "Số trang không hợp lệ (1..5000).")
    # Đợt A: cùng mẫu asyncio.timeout + cờ như /api/translate (xem trên) —
    # không rò slot khi hết giờ.
    acquired = False
    try:
        async with asyncio.timeout(ACQUIRE_TIMEOUT_SEC):
            await translate_semaphore.acquire()
            acquired = True
    except TimeoutError:
        if acquired:
            try:
                translate_semaphore.release()
            except Exception:
                pass
        try:
            await file.close()
        except Exception:
            pass
        raise HTTPException(429, "Hệ thống đang bận, thử lại sau.")
    active_translations += 1
    content: bytes | None = None
    try:
        content = await _read_upload_capped(file)
        if not content.startswith(b"%PDF-"):
            raise HTTPException(400, "File không phải PDF hợp lệ.")
        try:
            out, report = await translator.translate_single_page(
                content, page_number=page - 1,
                target_lang=target_lang, source_lang=source_lang,
            )
        except ValueError:
            raise HTTPException(400, "Không thể preview trang này (số trang sai hoặc PDF lỗi).")
        except RuntimeError as e:
            req_id = new_request_id()
            logger.error("[FIX P2] preview LLM unavailable [%s]: %s", req_id, sanitize_error(e))
            raise HTTPException(503, f"Dịch vụ dịch thuật tạm không khả dụng. Mã tra cứu: {req_id}.")
        except Exception as e:
            req_id = new_request_id()
            logger.exception("[FIX P2] preview thất bại [%s]: %s", req_id, sanitize_error(e))
            raise HTTPException(500, f"Lỗi preview. Mã tra cứu: {req_id}.")
        qa_sum = (report.get("qa") or {}).get("summary", {})
        stem = os.path.splitext(os.path.basename(filename))[0]
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "preview"
        out_name = f"[PV-p{page}][{target_lang[:2].upper()}]_{safe}.pdf"
        return StreamingResponse(
            io.BytesIO(bytes(out)),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{out_name}"',
                "Access-Control-Expose-Headers": "Content-Disposition, X-QA-Score, X-QA-Overflow, X-OCR-Used",
                "X-QA-Score": str(qa_sum.get("quality_score", "")),
                "X-QA-Overflow": str(qa_sum.get("overflow_blocks", "")),
                "X-OCR-Used": str(bool(report.get("ocr_pages"))),
                "X-Preview-Page": str(page),
            },
        )
    finally:
        try:
            await file.close()
        except Exception:
            pass
        try:
            content = None  # type: ignore
        except Exception:
            pass
        gc.collect()
        try:
            translate_semaphore.release()
        except Exception:
            pass
        active_translations = max(0, active_translations - 1)


# ==============================================================================
# FIX P2: LAYOUT QA + EDITOR HẬU KỲ
# ==============================================================================

@app.get("/api/jobs/{job_id}/qa")
async def get_job_qa(job_id: str):
    """
    FIX P2: báo cáo QA chi tiết từng block (overflow / small_font / fallback)
    để editor highlight câu cần sửa. Cần job đã done (đủ fit_results).
    """
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(404, "Không tìm thấy job.")
    if job.qa is None or job.fit_results is None or job.pages_layout is None:
        raise HTTPException(409, f"Job chưa có QA (status={job.status}, {job.progress}%).")
    # Kèm text gốc/dịch/final cho editor hiển thị (lấy từ fit_results + layout).
    blocks_view = []
    layout_by_id = {}
    for pg in job.pages_layout:
        # Đợt C: layout hỏng (blocks None) thì bỏ trang đó thay vì 500 cả view.
        for b in pg.get("blocks", []) or []:
            layout_by_id[b["id"]] = (pg.get("page_index", 0), b)
    for row in job.qa.get("blocks", []):
        b_id = row["id"]
        fr = (job.fit_results or {}).get(b_id, {})
        pg_idx, lb = layout_by_id.get(b_id, (row.get("page", 0), {}))
        blocks_view.append({
            **row,
            "original_masked": fr.get("original_masked", lb.get("text", "")),
            "translated_masked": fr.get("translated_masked", ""),
            "final_text": fr.get("final_text", ""),
        })
    return {
        "job_id": job_id,
        "status": job.status,
        "summary": job.qa.get("summary", {}),
        "ocr_pages": job.ocr_pages,
        "blocks": blocks_view,
    }


@app.post("/api/jobs/{job_id}/edit")
async def edit_job(job_id: str, payload: Dict[str, Any] = Body(...)):
    """
    FIX P2: editor sửa text từng block rồi vẽ lại PDF — KHÔNG gọi LLM.
    Body: {"edits": {block_id: new_final_text}, "only_pages": [0,2]?}
    - Giới hạn 500 blocks / 5000 ký tự mỗi block để chống abuse.
    - Cập nhật job.result_bytes + fit_results + qa mới, trả qa_summary.
    """
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(404, "Không tìm thấy job.")
    # Đợt C: khóa mutex theo job — hai lần sửa song song không ghi đè lẫn
    # nhau, download/QA không đọc xé lẻ giữa chừng. Khóa nhả trong finally
    # nên lỗi/timeout vẫn không kẹt.
    await job_manager.edit_lock(job_id).acquire()
    try:
        return await _apply_job_edit(job_id, job, payload or {})
    finally:
        try:
            job_manager.edit_lock(job_id).release()
        except Exception:
            pass


async def _apply_job_edit(job_id: str, job, payload: Dict[str, Any]):
    """Phần việc nặng của POST /api/jobs/{id}/edit, chạy trong khóa theo job."""
    if job.status != STATUS_DONE:
        raise HTTPException(409, f"Chỉ edit được job đã done (hiện: {job.status}).")
    if job.original_bytes is None or job.pages_layout is None or job.fit_results is None:
        raise HTTPException(409, "Job thiếu layout gốc (có thể đã bị evict một phần).")
    edits = (payload or {}).get("edits", {})
    only_pages = (payload or {}).get("only_pages")
    if not isinstance(edits, dict) or not edits:
        raise HTTPException(400, "Body cần {edits: {block_id: text}} non-empty.")
    if len(edits) > 500:
        raise HTTPException(400, "Tối đa 500 blocks mỗi lần edit.")
    # Validate IDs + độ dài.
    known_ids = set(job.fit_results.keys())
    for b_id, txt in edits.items():
        if b_id not in known_ids:
            raise HTTPException(400, f"block_id lạ: {b_id}")
        if not isinstance(txt, str) or not txt.strip():
            raise HTTPException(400, f"Text rỗng cho block {b_id}")
        if len(txt) > 5000:
            raise HTTPException(400, f"Text quá dài cho block {b_id} (max 5000).")
    if only_pages is not None:
        # Đợt B: bool là subclass của int nên phải loại riêng; list rỗng hoặc
        # toàn trang ngoài khoảng đều cho ra PDF không đổi mà vẫn báo ok —
        # chặn ngay thay vì thành công giả.
        if (not isinstance(only_pages, list) or not only_pages
                or any(isinstance(p, bool) or not isinstance(p, int) for p in only_pages)):
            raise HTTPException(400, "only_pages phải là list[int] non-empty (0-based, không dùng true/false).")

    # Merge final texts: giữ cũ + đè edits.
    merged_finals: Dict[str, str] = {
        b_id: (fr.get("final_text", "") or "") for b_id, fr in job.fit_results.items()
    }
    for b_id, txt in edits.items():
        merged_finals[b_id] = txt.strip()

    # Đợt B: chỉ áp dụng edits của trang ĐƯỢC VẼ. Trước đây fit_results/QA cập
    # nhật hết trong khi PDF chỉ vẽ trang con — tải về vẫn cũ mà báo cáo đã mới.
    page_of: Dict[str, int] = {}
    for pg in job.pages_layout:
        for b in pg.get("blocks", []):
            page_of[b["id"]] = pg.get("page_index", 0)
    if only_pages is None:
        rendered_pages = sorted({page_of.get(b_id, 0) for b_id in merged_finals})
    else:
        valid_pages = {p for p in only_pages if any(p == pg.get("page_index", 0) for pg in job.pages_layout)}
        if not valid_pages:
            raise HTTPException(400, "only_pages không khớp trang nào của tài liệu.")
        rendered_pages = sorted(valid_pages)
    rendered_set = set(rendered_pages)
    pending_edits = {
        b_id: txt for b_id, txt in edits.items()
        if page_of.get(b_id, 0) not in rendered_set
    }

    try:
        # Fitz là CPU-bound chặn event-loop => chạy thread.
        # Đợt 2: timeout cho re-render để request kẹt không treo connection mãi.
        try:
            edit_timeout = float(os.getenv("EDIT_TIMEOUT_SEC", "120"))
        except (TypeError, ValueError):
            edit_timeout = 120.0
        new_pdf = await asyncio.wait_for(
            asyncio.to_thread(
                translator.re_render_with_edits,
                bytes(job.original_bytes), job.pages_layout, merged_finals, only_pages,
            ),
            timeout=edit_timeout,
        )
    except asyncio.TimeoutError:
        # Đợt C: hết giờ vẽ lại là 504 (gateway timeout semantics) thay vì 500
        # để monitoring phân biệt với bug, client biết nên thử lại với ít sửa hơn.
        raise HTTPException(504, "Vẽ lại quá lâu. Bạn giảm số đoạn sửa mỗi lần rồi thử lại.")
    except Exception:
        req_id = new_request_id()
        logger.exception("[FIX P2] edit re-render job %s thất bại [%s]", job_id, req_id)
        raise HTTPException(500, f"Lỗi vẽ lại sau edit. Mã tra cứu: {req_id}.")

    # Cập nhật fit_results.final_text + rebuild QA (không cần LLM).
    # Đợt B: chỉ chạm block thuộc trang đã vẽ; block pending giữ nguyên để
    # QA và PDF luôn mô tả đúng file tải về.
    for b_id in merged_finals:
        if b_id in job.fit_results and page_of.get(b_id, 0) in rendered_set:
            job.fit_results[b_id]["final_text"] = merged_finals[b_id]
    try:
        new_qa = build_qa_report(job.pages_layout, {
            b_id: {
                "final_size": fr.get("final_size", 10.0),
                "initial_size": fr.get("initial_size", 10.0),
                "final_text": fr.get("final_text", ""),
                "translated_masked": fr.get("translated_masked", ""),
                "original_masked": fr.get("original_masked", ""),
            } for b_id, fr in job.fit_results.items()
        })
    except Exception as e:
        logger.warning("[FIX P2] rebuild QA sau edit thất bại: %s", e)
        new_qa = job.qa
    job.result_bytes = new_pdf
    job.qa = new_qa
    job.touch()
    await job_manager._broadcast(job)
    gc.collect()
    return {
        "ok": True,
        "job_id": job_id,
        "edited_blocks": len(edits) - len(pending_edits),
        "pending_edits": pending_edits,
        "rendered_pages": rendered_pages,
        "result_size": len(new_pdf),
        "qa_summary": (new_qa or {}).get("summary", {}),
        "download_url": f"/api/jobs/{job_id}/download",
    }


@app.post("/api/cache/clear")
async def clear_cache(request: Request):
    """FIX P2: xoá TranslationCache (ops/debug). Log rõ để audit.
    Đợt C: nếu đặt ADMIN_TOKEN thì phải kèm header X-Admin-Token đúng mới được
    xóa — tránh ai cũng bấm được làm tốn tiền LLM. Chưa đặt token thì mở như cũ."""
    admin_token = os.getenv("ADMIN_TOKEN", "")
    if admin_token:
        import hmac
        given = request.headers.get("X-Admin-Token", "")
        if not hmac.compare_digest(given, admin_token):
            raise HTTPException(403, "Thiếu hoặc sai X-Admin-Token.")
    try:
        await llm_service.clear_cache()
    except Exception as e:
        logger.warning("Không thể xoá cache: %s", sanitize_error(e))
        raise HTTPException(500, "Không thể xoá cache.")
    logger.warning("[FIX P2] TranslationCache đã bị xoá thủ công qua API.")
    return {"ok": True, "cache": llm_service.cache_stats()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
