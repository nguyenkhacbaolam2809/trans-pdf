"""
services/job_queue.py
=====================
FIX P1: pipeline job queue + WebSocket progress.

- POST /api/jobs nhận PDF rồi trả ngay {job_id} (không giữ HTTP connection dài).
- N worker nền (mặc định = MAX_CONCURRENT_TRANSLATIONS) lấy job từ asyncio.Queue,
  gọi PDFTranslator.translate_pdf_with_report với progress_callback để đẩy % real-time.
- WS /ws/jobs/{id} subscribe nhận JSON {status, progress, step, detail} mỗi khi job cập nhật.
- GET /api/jobs/{id} poll thay WS nếu client không hỗ trợ WebSocket.
- TTL + MAX_JOBS + cleanup nền để không leak RAM (xoá cả original + result bytes).

Không cần Redis/Celery — đủ cho 1 instance. Muốn scale ngang thì thay lớp này
bằng Redis Streams / RQ mà giữ nguyên interface submit/get/subscribe.
"""

import asyncio
import gc
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .safety import sanitize_error, JobCancelledError, PdfTooLargeError

logger = logging.getLogger("trans_pdf.jobs")

STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"


class QueueFullError(RuntimeError):
    pass


@dataclass
class Job:
    id: str
    filename: str
    target_lang: str
    source_lang: str
    status: str = STATUS_QUEUED
    progress: int = 0
    step: str = "queued"
    detail: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # Kết quả (giữ RAM, có TTL + cleanup):
    result_bytes: Optional[bytes] = None
    error: str = ""
    # Đợt C: mã lỗi máy đọc được để API map đúng status (503 hết LLM, 422 file hỏng).
    error_code: str = ""
    # Đợt 2: cờ hủy — DELETE đặt cờ, worker kiểm tra sau mỗi trang.
    cancel_requested: bool = False
    # FIX P2: layout + QA để preview/edit không cần dịch lại.
    original_bytes: Optional[bytes] = None
    pages_layout: Optional[List[Dict[str, Any]]] = None
    fit_results: Optional[Dict[str, Dict[str, Any]]] = None
    qa: Optional[Dict[str, Any]] = None
    ocr_pages: int = 0
    output_filename: str = ""

    def touch(self) -> None:
        self.updated_at = time.time()

    def to_public_dict(self, include_layout: bool = False) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "job_id": self.id,
            "filename": self.filename,
            "target_lang": self.target_lang,
            "source_lang": self.source_lang,
            "status": self.status,
            "progress": self.progress,
            "step": self.step,
            "detail": self.detail,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "ocr_pages": self.ocr_pages,
            "output_filename": self.output_filename,
            "result_size": len(self.result_bytes) if self.result_bytes else 0,
        }
        if self.status == STATUS_ERROR and self.error:
            d["error"] = self.error
        if self.qa is not None:
            d["qa_summary"] = self.qa.get("summary", {})
        if include_layout:
            d["pages_layout"] = self.pages_layout
            d["fit_results"] = self.fit_results
            d["qa"] = self.qa
        return d


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


class JobManager:
    def __init__(
        self,
        translator,
        max_workers: int = 2,
        max_queue_size: int = 20,
        ttl_sec: int = 1800,
        max_jobs: int = 20,
        heavy_slot: Optional[asyncio.Semaphore] = None,
    ):
        self.translator = translator
        self.max_workers = max(1, max_workers)
        self.ttl_sec = max(60, ttl_sec)
        self.max_jobs = max(1, max_jobs)
        self._queue_maxsize = max(1, max_queue_size)
        # Đợt C: hàng đợi tạo trong start(), KHÔNG ở __init__. asyncio.Queue gắn
        # với event loop đầu tiên chạm vào nó; tạo ở import mà chạy trên loop
        # khác (uvicorn --reload, nhiều TestClient, nhúng module) là worker quay
        # nóng "bound to a different event loop", job kẹt mãi. Đã kiểm chứng
        # Lock/Semaphore thì an toàn qua loop, chỉ Queue là không.
        self.queue: Optional[asyncio.Queue[str]] = None
        self.jobs: Dict[str, Job] = {}
        self._lock = asyncio.Lock()
        self._workers: List[asyncio.Task] = []
        self._cleaner: Optional[asyncio.Task] = None
        self._running = False
        # Đợt 1: semaphore dùng chung với endpoint sync để tổng số PDF nặng
        # xử lý cùng lúc không vượt trần RAM. None = chỉ giới hạn bằng workers.
        self.heavy_slot = heavy_slot
        # Đợt C: khóa riêng từng job cho endpoint edit — hai lần sửa song song
        # không ghi đè lẫn nhau, đọc download/QA không xé lẻ giữa chừng.
        self._edit_locks: Dict[str, asyncio.Lock] = {}
        # FIX P1: WS subscribers theo job.
        self._subscribers: Dict[str, Set[Any]] = {}

    @classmethod
    def from_env(cls, translator, heavy_slot: Optional[asyncio.Semaphore] = None) -> "JobManager":
        return cls(
            translator=translator,
            max_workers=_int_env("MAX_CONCURRENT_TRANSLATIONS", 2),
            max_queue_size=_int_env("JOB_MAX_QUEUE", 20),
            ttl_sec=_int_env("JOB_TTL_SEC", 1800),
            max_jobs=_int_env("JOB_MAX_JOBS", 20),
            heavy_slot=heavy_slot,
        )

    # --------------------------------------------------------------
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        # Hàng đợi mới gắn với loop hiện tại; job dở dang từ loop cũ
        # (reload/test) cho xếp hàng lại thay vì kẹt ở processing mãi.
        self.queue = asyncio.Queue(maxsize=self._queue_maxsize)
        requeued = 0
        for job in self.jobs.values():
            if job.status in (STATUS_QUEUED, STATUS_PROCESSING):
                job.status = STATUS_QUEUED
                try:
                    self.queue.put_nowait(job.id)
                    requeued += 1
                except asyncio.QueueFull:
                    logger.warning("[Đợt C] Hàng đợi đầy khi khởi động, job %s chờ TTL.", job.id)
        self._workers = []
        for i in range(self.max_workers):
            self._workers.append(asyncio.create_task(self._worker_loop(i), name=f"trans-job-worker-{i}"))
        self._cleaner = asyncio.create_task(self._cleanup_loop(), name="trans-job-cleaner")
        logger.info(
            "[FIX P1] JobManager started: workers=%d queue=%d ttl=%ds max_jobs=%d requeued=%d",
            self.max_workers, self._queue_maxsize, self.ttl_sec, self.max_jobs, requeued,
        )

    async def stop(self) -> None:
        self._running = False
        for t in self._workers:
            t.cancel()
        if self._cleaner is not None:
            self._cleaner.cancel()
        # Đợt C: chờ task tắt thật thay vì bỏ mặc (tránh "Task was destroyed
        # but pending"), có trần để một task kẹt không giữ shutdown mãi.
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._workers, *( [self._cleaner] if self._cleaner else [] ),
                               return_exceptions=True),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            logger.warning("[Đợt C] Một số worker chưa tắt kịp khi shutdown.")
        # Đóng WS còn treo, mỗi socket có timeout riêng.
        for subs in list(self._subscribers.values()):
            for ws in list(subs):
                try:
                    await asyncio.wait_for(ws.close(), timeout=3.0)
                except Exception:
                    pass
        self._subscribers.clear()
        logger.info("[FIX P1] JobManager stopped.")

    # --------------------------------------------------------------
    async def submit(
        self, original_bytes: bytes, filename: str,
        target_lang: str, source_lang: str = "Auto",
    ) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job = Job(
            id=job_id, filename=filename,
            target_lang=target_lang, source_lang=source_lang,
            original_bytes=original_bytes,
        )
        async with self._lock:
            # FIX P2: giới hạn số job giữ RAM — evict job cũ nhất đã xong/hỏng trước.
            # Đợt C: KHÔNG bao giờ xóa job đang chờ/chạy để nhường chỗ. Trước đây
            # pool rỗng thì rơi sang xóa job pending — client vừa nhận 202 quay
            # lại poll thì ăn 404. Giờ hết chỗ mà toàn job pending thì báo đầy.
            if len(self.jobs) >= self.max_jobs:
                if not self._evict_oldest_locked():
                    raise QueueFullError(
                        "Hàng đợi đang đầy, vui lòng thử lại sau ít phút."
                    )
            self.jobs[job_id] = job
        queue = self._require_queue()
        try:
            queue.put_nowait(job_id)
        except asyncio.QueueFull:
            async with self._lock:
                # Rollback: xoá job + giải phóng bytes ngay.
                self.jobs.pop(job_id, None)
            gc.collect()
            raise QueueFullError(
                "Hàng đợi đang đầy, vui lòng thử lại sau ít phút."
            )
        job.touch()
        await self._broadcast(job)
        logger.info("[FIX P1] Job %s queued (%s -> %s, %dB).", job_id, filename, target_lang, len(original_bytes))
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def edit_lock(self, job_id: str) -> asyncio.Lock:
        """Đợt C: khóa mutex theo job cho endpoint edit (không khóa toàn cục)."""
        lock = self._edit_locks.get(job_id)
        if lock is None:
            lock = asyncio.Lock()
            self._edit_locks[job_id] = lock
        return lock

    def processing_count(self) -> int:
        """Đợt C: số job đang giữ slot nặng (processing) để /health báo đúng."""
        return sum(1 for j in self.jobs.values() if j.status == STATUS_PROCESSING)

    async def cancel(self, job_id: str) -> Optional[Job]:
        """Đợt 2: yêu cầu hủy. Job queued/processing thì đặt cờ để worker
        dừng sau trang hiện tại; job xong/hỏng thì xóa record giải phóng RAM."""
        async with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            if job.status in (STATUS_QUEUED, STATUS_PROCESSING):
                job.cancel_requested = True
                job.touch()
                return job
            self._free_job_locked(job_id)
            return None

    def _require_queue(self) -> "asyncio.Queue[str]":
        queue = self.queue
        if queue is None:
            raise RuntimeError("JobManager chưa start() — hàng đợi chưa gắn event loop.")
        return queue

    # --------------------------------------------------------------
    # FIX P1: WS pub/sub — mỗi job có 1 room riêng.
    async def subscribe(self, job_id: str, ws) -> None:
        async with self._lock:
            self._subscribers.setdefault(job_id, set()).add(ws)

    async def unsubscribe(self, job_id: str, ws) -> None:
        async with self._lock:
            subs = self._subscribers.get(job_id)
            if subs is not None:
                subs.discard(ws)
                if not subs:
                    self._subscribers.pop(job_id, None)

    async def _broadcast(self, job: Job) -> None:
        subs = self._subscribers.get(job.id)
        if not subs:
            return
        payload = job.to_public_dict()
        # Đợt B: gửi có timeout — một client nối mà không đọc (tắc TCP) không được
        # treo cả worker. Trước đây await trần từng socket nên một consumer chậm
        # là job kẹt ở processing mãi, timeout tổng cũng không cứu vì kẹt trong
        # callback tiến trình chứ không phải chỗ wait_for ngoài.
        dead = []
        for ws in list(subs):
            try:
                await asyncio.wait_for(ws.send_json(payload), timeout=5.0)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                cur = self._subscribers.get(job.id, set())
                for ws in dead:
                    cur.discard(ws)
            # Đóng ngoài lock để socket treo không giữ lock của manager.
            for ws in dead:
                try:
                    await ws.close()
                except Exception:
                    pass

    async def _set_progress(self, job: Job, progress: int, step: str, detail: str = "") -> None:
        job.progress = max(0, min(100, int(progress)))
        job.step = step
        job.detail = detail
        job.touch()
        await self._broadcast(job)

    # --------------------------------------------------------------
    async def _worker_loop(self, worker_id: int) -> None:
        logger.info("[FIX P1] worker-%d started.", worker_id)
        queue = self._require_queue()
        while self._running:
            try:
                job_id = await queue.get()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("[FIX P1] worker queue.get lỗi: %s", e)
                # Đợt C: nghỉ 1s thay vì quay nóng — lỗi lặp (vd loop lạ) không
                # đốt CPU và spam log.
                try:
                    await asyncio.sleep(1)
                except asyncio.CancelledError:
                    break
                continue
            try:
                await self._process_job(job_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("[FIX P1] worker-%d job %s lỗi ngoài dự kiến: %s", worker_id, job_id, sanitize_error(e))
            finally:
                try:
                    queue.task_done()
                except Exception:
                    pass

    async def _process_job(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            return
        # Đợt 2: job bị hủy khi còn xếp hàng thì bỏ qua luôn, không tốn worker.
        if job.cancel_requested:
            job.status = STATUS_CANCELLED
            job.error = "Job đã bị hủy."
            job.original_bytes = None
            job.touch()
            await self._set_progress(job, job.progress, "Đã hủy", "")
            return
        job.status = STATUS_PROCESSING
        job.touch()
        await self._set_progress(job, 3, "processing", "Bắt đầu xử lý...")

        # FIX P1: progress_callback ánh xạ (done/total) -> % 5..95 để WS mượt.
        # Đợt 2: callback cũng là điểm kiểm tra hủy — đặt cờ thì dừng sau trang hiện tại.
        async def _cb(done: int, total: int, stage: str, detail: str = "") -> None:
            if job.cancel_requested:
                raise JobCancelledError("Job đã bị hủy bởi người dùng.")
            total = max(1, total)
            pct = 5 + int((done / total) * 90)
            label = {
                "started": "Bóc tách layout",
                "translating": "Dịch thuật LLM",
                "redrawing": "Vẽ lại bố cục",
                "page_done": "Đang xử lý trang",
                "page_error": "Bỏ qua trang lỗi",
                "done": "Hoàn tất",
            }.get(stage, stage)
            await self._set_progress(job, pct, label, detail or f"{done}/{total}")

        # Đợt 2: timeout tổng cho một job để PDF kẹt không giữ worker mãi.
        try:
            timeout_sec = float(os.getenv("JOB_TIMEOUT_SEC", "1800"))
        except (TypeError, ValueError):
            timeout_sec = 1800.0

        try:
            assert job.original_bytes is not None
            # Đợt 1: job cũng xếp hàng qua semaphore chung với /translate, /preview
            # để tổng tải nặng không vượt trần. Không có semaphore thì chạy ngay.
            if self.heavy_slot is not None:
                await self.heavy_slot.acquire()
            try:
                out, report = await asyncio.wait_for(
                    self.translator.translate_pdf_with_report(
                        job.original_bytes,
                        target_lang=job.target_lang,
                        source_lang=job.source_lang,
                        progress_callback=_cb,
                        should_cancel=lambda: job.cancel_requested,
                    ),
                    timeout=timeout_sec,
                )
            finally:
                if self.heavy_slot is not None:
                    try:
                        self.heavy_slot.release()
                    except Exception:
                        pass
            job.result_bytes = out
            job.pages_layout = report.get("pages_layout")
            job.fit_results = report.get("fit_results")
            job.qa = report.get("qa")
            job.ocr_pages = int(report.get("ocr_pages", 0))
            stem = job.filename.rsplit(".", 1)[0] if "." in job.filename else job.filename
            import re as _re
            safe = _re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "translated"
            job.output_filename = f"[{job.target_lang[:2].upper()}]_{safe}.pdf"
            job.status = STATUS_DONE
            job.touch()
            await self._set_progress(job, 100, "Hoàn tất", f"ocr_pages={job.ocr_pages}")
            logger.info(
                "[FIX P1] Job %s done (%dB -> %dB, ocr=%d).",
                job.id, len(job.original_bytes or b""), len(out), job.ocr_pages,
            )
        except JobCancelledError:
            # Đợt 2: user hủy — giữ record để client poll thấy trạng thái cuối,
            # giải phóng bytes nặng, TTL dọn sau.
            job.status = STATUS_CANCELLED
            job.error = "Job đã bị hủy."
            job.error_code = "cancelled"
            job.result_bytes = None
            job.pages_layout = None
            job.fit_results = None
            job.qa = None
            job.touch()
            await self._set_progress(job, job.progress, "Đã hủy", "")
            logger.info("[Đợt 2] Job %s cancelled.", job.id)
        except asyncio.TimeoutError:
            job.status = STATUS_ERROR
            job.error = "Job vượt quá thời gian cho phép. Bạn chia nhỏ file rồi thử lại."
            job.error_code = "timeout"
            job.touch()
            await self._set_progress(job, job.progress, "Quá thời gian", job.error[:120])
            logger.error("[Đợt 2] Job %s timeout.", job.id)
        except PdfTooLargeError as e:
            # Đợt C: giữ hướng dẫn chia file (đồng bộ đã có 413) thay vì lỗi chung.
            job.status = STATUS_ERROR
            job.error = str(e)
            job.error_code = "too_large"
            job.touch()
            await self._set_progress(job, job.progress, "File quá lớn", job.error[:120])
        except RuntimeError as e:
            # Thiếu key LLM / provider chết — client nhận mã lỗi chung,
            # chi tiết đã che key chỉ nằm ở log server.
            job.status = STATUS_ERROR
            job.error = "Dịch vụ dịch thuật tạm thời không khả dụng. Vui lòng thử lại sau."
            job.error_code = "llm_unavailable"
            job.touch()
            await self._set_progress(job, job.progress, "Lỗi dịch vụ", job.error[:120])
            logger.error("[FIX P1] Job %s RuntimeError: %s", job.id, sanitize_error(e))
        except Exception as e:
            job.status = STATUS_ERROR
            job.error = "Lỗi xử lý PDF nội bộ. Vui lòng thử lại sau."
            job.error_code = "internal"
            job.touch()
            await self._set_progress(job, job.progress, "Lỗi xử lý", job.error[:120])
            logger.exception("[FIX P1] Job %s failed: %s", job.id, sanitize_error(e))
        finally:
            gc.collect()

    # --------------------------------------------------------------
    def _evict_oldest_locked(self) -> bool:
        """Gọi khi đã giữ _lock. Xoá job done/error/cancelled cũ nhất để giữ RAM.
        Trả False khi không có job nào evict được (toàn pending) để caller báo đầy."""
        done_jobs = [j for j in self.jobs.values() if j.status in (STATUS_DONE, STATUS_ERROR, STATUS_CANCELLED)]
        if not done_jobs:
            return False
        oldest = min(done_jobs, key=lambda j: j.updated_at)
        self._free_job_locked(oldest.id)
        return True

    def _free_job_locked(self, job_id: str) -> None:
        job = self.jobs.pop(job_id, None)
        self._edit_locks.pop(job_id, None)
        if job is None:
            return
        job.original_bytes = None
        job.result_bytes = None
        job.pages_layout = None
        job.fit_results = None
        job.qa = None
        subs = self._subscribers.pop(job_id, None)
        # Đóng WS subscriber của job bị evict (client sẽ thấy close + poll 404).
        if subs:
            for ws in list(subs):
                try:
                    coro = ws.close()
                    if asyncio.iscoroutine(coro):
                        asyncio.create_task(coro)
                except Exception:
                    pass
        logger.info("[FIX P1] Evict job %s để giữ RAM.", job_id)

    async def _cleanup_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(60)
                now = time.time()
                async with self._lock:
                    expired = [
                        jid for jid, j in self.jobs.items()
                        if (now - j.updated_at) > self.ttl_sec
                    ]
                    for jid in expired:
                        self._free_job_locked(jid)
                    # Enforce max_jobs phòng khi submit burst. Dừng khi chỉ còn
                    # job pending (xóa nữa là mất job đang chờ của user).
                    while len(self.jobs) > self.max_jobs:
                        if not self._evict_oldest_locked():
                            break
                if expired:
                    logger.info("[FIX P1] Cleanup %d jobs hết TTL.", len(expired))
                    gc.collect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("[FIX P1] cleanup loop lỗi: %s", e)

    def queue_stats(self) -> Dict[str, Any]:
        queue = self.queue
        return {
            "queued": queue.qsize() if queue is not None else 0,
            "max_queue": self._queue_maxsize,
            "stored_jobs": len(self.jobs),
            "max_jobs": self.max_jobs,
            "workers": self.max_workers,
            "ttl_sec": self.ttl_sec,
        }
