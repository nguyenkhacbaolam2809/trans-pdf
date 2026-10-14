"""
services/pdf_translator.py
==========================
Trái tim của hệ thống trans-pdf (Ponytail Senior Dev Architecture).

Kỹ năng áp dụng:
1. <Skill: Layout-Retainer-Pro>:
   - Bóc tách tọa độ (x0, y0, x1, y1) chính xác của từng block text.
   - Thuật toán lặp (while-loop) tự động co giãn font-size để text dịch KHÔNG BAO GIỜ tràn bbox.
   - Sử dụng in-memory scratch page để dry-run đo đạc, không làm bẩn target page stream.

2. <Skill: LaTeX-Image-Shield>:
   - Áp dụng redaction với images=fitz.PDF_REDACT_IMAGE_NONE (bảo vệ 100% vector/raster images).
   - Regex đóng băng công thức Toán học (block $$...$$ và inline $...$) thành [MATH_MASK_n].
   - Unmasking chuẩn xác sau khi LLM dịch xong.

3. <Skill: Font-Embed-Master>:
   - Nhúng TrueType font (.ttf) chuẩn Unicode (Identity-H Type0 font).
   - Tuyệt đối loại bỏ lỗi font ô vuông tofu (□) đối với tiếng Việt, Nhật, Hàn, v.v.

FIX P1: OCR hybrid + progress callback cho job queue / WebSocket.
FIX P2: thu thập pages_layout + fit_results để build QA report + preview 1 trang + editor re-render.
"""

import asyncio
import gc
import inspect
import os
import re
import fitz  # PyMuPDF
import logging
import urllib.request
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from .llm_service import LLMService, MATH_MASK_RE
from .ocr_service import OCRService
from .layout_qa import build_qa_report
from .safety import JobCancelledError, PdfTooLargeError

logger = logging.getLogger("trans_pdf.engine")


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _font_usable(font_path: str) -> bool:
    """Đợt C: thử nhúng thật một lần lúc khởi động. File .ttc (font collection)
    qua mặt được kiểm tra tồn tại nhưng insert_textbox không dùng được (cần
    index) — trước đây lỗi lặp lại ở mọi cỡ chữ rồi để lại chỗ trống im lặng."""
    if not font_path or not os.path.exists(font_path):
        return False
    if os.path.splitext(font_path)[1].lower() == ".ttc":
        return False
    tmp: Optional[fitz.Document] = None
    try:
        tmp = fitz.open()
        pg = tmp.new_page(width=200, height=200)
        rc = pg.insert_textbox(
            fitz.Rect(10, 10, 190, 190), "Kiểm tra Unicode tiếng Việt",
            fontsize=12, fontfile=font_path, fontname="ProbeFont",
        )
        return rc >= 0
    except Exception:
        return False
    finally:
        try:
            if tmp is not None:
                tmp.close()
        except Exception:
            pass


def _iou(a: List[float], b: List[float]) -> float:
    """Diện tích giao / hợp của 2 bbox [x0,y0,x1,y1]."""
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    if inter <= 0:
        return 0.0
    aa = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    ab = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = aa + ab - inter
    return inter / union if union > 0 else 0.0

# FIX P1: kiểu callback tiến độ — job queue / WebSocket hook vào đây.
# cb(done_pages, total_pages, stage, detail) — hỗ trợ cả async và sync.
ProgressCallback = Optional[Callable[..., Any]]


async def _emit_progress(cb: ProgressCallback, done: int, total: int, stage: str, detail: str = "") -> None:
    # FIX P1: không để callback lỗi làm chết cả pipeline dịch.
    if cb is None:
        return
    try:
        res = cb(done, total, stage, detail)
        if inspect.isawaitable(res):
            await res
    except Exception as e:
        logger.warning("[FIX P1] progress_callback lỗi (bỏ qua): %s", e)


# ==============================================================================
# 1. LATEX-IMAGE-SHIELD: MASKING & UNMASKING ENGINE
# ==============================================================================

class LaTeXMasker:
    """
    Regex engine bảo vệ công thức toán và ký hiệu khoa học khỏi bị LLM dịch méo mó.
    """
    # Thứ tự regex quan trọng: Block math ($$, \[, \]) phải match trước Inline math ($)
    MATH_PATTERNS = [
        (r"\$\$(.*?)\$\$", "BLOCK_DOUBLE_DOLLAR"),
        (r"\\\[(.*?)\\\]", "BLOCK_BRACKET"),
        # Inline math: Kẹp giữa hai dấu $ nhưng không có khoảng trắng sát dấu $ (tránh ăn nhầm $100)
        (r"(?<!\\)\$(?!\s)(.+?)(?<!\s)(?<!\\)\$", "INLINE_DOLLAR"),
        (r"\\\((.*?)\\\)", "INLINE_PAREN"),
    ]

    @classmethod
    def mask(cls, text: str, start_index: int = 0) -> Tuple[str, Dict[str, str], int]:
        """
        Quét và thay thế tất cả công thức toán học bằng token [MATH_MASK_n].
        Trả về (masked_text, mask_map, next_index).
        """
        mask_map: Dict[str, str] = {}
        current_idx = start_index

        result = text
        for pattern, ptype in cls.MATH_PATTERNS:
            flags = re.DOTALL if "BLOCK" in ptype else 0

            def replacer(match: re.Match) -> str:
                nonlocal current_idx
                token = f"[MATH_MASK_{current_idx}]"
                mask_map[token] = match.group(0)
                current_idx += 1
                return token

            result = re.sub(pattern, replacer, result, flags=flags)

        return result, mask_map, current_idx

    @classmethod
    def unmask(cls, text: str, mask_map: Dict[str, str]) -> str:
        """
        Khôi phục nguyên vẹn các công thức toán từ token [MATH_MASK_n].
        """
        result = text
        for token, original_math in mask_map.items():
            result = result.replace(token, original_math)
        return result


# ==============================================================================
# FIX P0: MATH TOKEN VALIDATION — kiểm tra overflow logic + mask thật sự
# ==============================================================================
def count_math_masks(text: str) -> Tuple[int, List[str]]:
    """Đếm thật sự số lượng [MATH_MASK_n] bằng regex (không đếm chuỗi con thủ công)."""
    found = MATH_MASK_RE.findall(text or "")
    return len(found), found


def validate_and_repair_translations(
    extracted_blocks: List[Dict[str, Any]],
    translated_dict: Dict[str, str],
) -> Dict[str, str]:
    """
    FIX P0: phòng thủ tầng 2 (tầng 1 đã có trong LLMService.enforce_math_masks).
    - So sánh số lượng + ID mask giữa text gốc (masked) và text dịch (masked).
    - Block nào LLM làm mất/thừa/biến dạng mask => fallback giữ nguyên câu gốc masked.
    - Không bao giờ để công thức nát lọt xuống khâu unmask/redraw.
    """
    repaired: Dict[str, str] = {}
    for item in extracted_blocks:
        b_id = item["id"]
        original_masked = item.get("text", "")
        candidate = translated_dict.get(b_id, original_masked)
        if not isinstance(candidate, str) or not candidate.strip():
            logger.warning(
                "[FIX P0] Block %s dịch rỗng — fallback giữ câu gốc.", b_id
            )
            repaired[b_id] = original_masked
            continue
        orig_count, orig_tokens = count_math_masks(original_masked)
        cand_count, cand_tokens = count_math_masks(candidate)
        if sorted(orig_tokens) != sorted(cand_tokens):
            logger.warning(
                "[FIX P0] Block %s mask mismatch (gốc=%d %s vs dịch=%d %s) — fallback giữ câu gốc.",
                b_id, orig_count, orig_tokens, cand_count, cand_tokens,
            )
            repaired[b_id] = original_masked
        else:
            repaired[b_id] = candidate
    return repaired


# ==============================================================================
# 2. FONT-EMBED-MASTER: FONT DISCOVERY & FALLBACK SYSTEM
# ==============================================================================

class FontEmbedMaster:
    """
    Quản lý việc nhúng font TTF vào PyMuPDF.
    Quy tắc số 1 của Ponytail Dev: 'Lỗi font tofu (□) là một sự sỉ nhục'.
    """
    _cached_font_path: Optional[str] = None

    @classmethod
    def get_font_path(cls) -> str:
        if cls._cached_font_path and os.path.exists(cls._cached_font_path):
            return cls._cached_font_path

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        fonts_dir = os.path.join(base_dir, "fonts")
        os.makedirs(fonts_dir, exist_ok=True)

        # 1. Kiểm tra font local trong backend/fonts/
        candidates = [
            os.path.join(fonts_dir, "NotoSans-Regular.ttf"),
            os.path.join(fonts_dir, "Roboto-Regular.ttf"),
        ]
        for c in candidates:
            if os.path.exists(c):
                cls._cached_font_path = c
                return c

        # 2. Kiểm tra System Fonts (Windows, Linux, MacOS)
        sys_candidates = [
            # Windows
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/tahoma.ttf",
            # Linux (Debian/Ubuntu/Alpine)
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            # MacOS
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
        for sc in sys_candidates:
            if os.path.exists(sc):
                logger.info(f"[Font-Embed-Master] Sử dụng System Font: {sc}")
                cls._cached_font_path = sc
                return sc

        # 3. Tự động tải NotoSans nếu không tìm thấy font cục bộ nào
        target_path = os.path.join(fonts_dir, "NotoSans-Regular.ttf")
        download_url = "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSans/NotoSans-Regular.ttf"
        try:
            logger.info(f"[Font-Embed-Master] Đang tải NotoSans từ {download_url}...")
            urllib.request.urlretrieve(download_url, target_path)
            if os.path.exists(target_path):
                cls._cached_font_path = target_path
                return target_path
        except Exception as e:
            logger.warning(f"[Font-Embed-Master] Không thể tải font online: {e}")

        # Trường hợp xấu nhất (chỉ dùng nếu rớt mạng hoàn toàn)
        return ""


# ==============================================================================
# 3. LAYOUT-RETAINER-PRO: FONT FITTING & TEXT INSERTION
# ==============================================================================

def insert_text_with_fit(
    page: fitz.Page,
    scratch_page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    initial_font_size: float,
    font_path: str,
    color: Tuple[float, float, float] = (0.0, 0.0, 0.0),
    align: int = 0,
    min_font_size: float = 4.0,
    step: float = 0.5,
) -> float:
    """
    <Skill: Layout-Retainer-Pro>
    FIX P0: Thuật toán lặp (while loop) tính toán co giãn font-size tự động —
    viết rõ ràng + xử lý overflow thật sự (không chỉ đo cho có).

    Quy tắc của Ponytail Dev:
    - Bounding Box trong PDF là 'bất khả xâm phạm'. Text mới dài hơn tiếng Anh
      thường từ 20% đến 40% (nhất là dịch sang tiếng Việt hoặc Đức).
    - Thử nghiệm trên `scratch_page` trước để KHÔNG BAO GIỜ ghi đè text rác lên target page.
    - rc >= 0: Text vừa vặn hoàn toàn (rc là khoảng trống còn dư ở đáy bbox).
    - rc < 0: Bị tràn (overflow), tiếp tục giảm font_size.

    Returns:
        final_font_size đã dùng để vẽ. Nếu text rỗng/rect lỗi, trả initial mà không vẽ.
    """
    # FIX P0: guard text rỗng — không vẽ, không tốn vòng lặp.
    if text is None or not str(text).strip():
        return initial_font_size

    # FIX P0: guard rect lỗi (empty / width/height <= 0) — tránh crash fitz.
    try:
        if rect is None or getattr(rect, "is_empty", False) or rect.width <= 0 or rect.height <= 0:
            logger.warning("[FIX P0] Bỏ qua block có rect rỗng/không hợp lệ: %s", rect)
            return initial_font_size
    except Exception as e:
        logger.warning("[FIX P0] Rect bất thường (%s) — bỏ qua block. Lỗi: %s", rect, e)
        return initial_font_size

    # FIX P0: chuẩn hoá font-size đầu vào, kẹp trong ngưỡng hợp lệ.
    try:
        current_size = float(initial_font_size)
    except (TypeError, ValueError):
        current_size = 10.0
    if current_size <= 0 or current_size > 72:
        current_size = min(max(current_size, min_font_size), 72.0)

    # FIX P0: font_path có thể rỗng/mất file (VD rớt mạng không tải được NotoSans).
    # Fallback về helv để không crash insert_textbox.
    effective_font_path = font_path if font_path and os.path.exists(font_path) else ""
    font_name = "EmbeddedUnicodeFont" if effective_font_path else "helv"
    if font_path and not effective_font_path:
        logger.warning("[FIX P0] font_path mất (%s) — fallback helv cho block này.", font_path)

    # FIX P0: VÒNG LẶP WHILE rõ ràng — giảm fontsize tới khi lọt thỏm trong rect.
    # Mỗi vòng dry-run trên scratch_page, KHÔNG chạm target page.
    fitted_size = min_font_size
    overflow = True
    current_size_loop = current_size
    try:
        while current_size_loop >= min_font_size:
            try:
                rc = scratch_page.insert_textbox(
                    rect,
                    text,
                    fontsize=current_size_loop,
                    fontfile=effective_font_path if effective_font_path else None,
                    fontname=font_name,
                    align=align,
                )
            except Exception as e:
                # FIX P0: font lỗi/unicode lỗi => thử giảm size tiếp, nếu tới min vẫn lỗi thì bỏ.
                logger.warning(
                    "[FIX P0] dry-run thất bại ở size %.1f (%s) — thử size nhỏ hơn.",
                    current_size_loop, e,
                )
                current_size_loop -= step
                continue
            if rc >= 0:
                # FIX P0: vừa vặn hoàn toàn — chốt size, thoát vòng lặp.
                fitted_size = current_size_loop
                overflow = False
                break
            # FIX P0: rc < 0 nghĩa là tràn — giảm size và lặp tiếp.
            current_size_loop -= step
        else:
            # FIX P0: while cạn mà không break => vẫn tràn ở min size.
            fitted_size = min_font_size
            overflow = True
    except Exception as e:
        logger.warning("[FIX P0] Lỗi bất ngờ khi fit font: %s — dùng min size.", e)
        fitted_size = min_font_size
        overflow = True

    final_font_size = max(fitted_size, min_font_size)

    # FIX P0: xử lý overflow thật sự — log cảnh báo để QA biết block này sẽ bị clip.
    if overflow:
        logger.warning(
            "[FIX P0] OVERFLOW: text dài %d ký tự vẫn tràn rect %s ở min size %.1f. "
            "Sẽ vẽ với min size (có thể bị cắt đáy). Cân nhắc tách block ở P2 layout QA.",
            len(text), tuple(round(v, 1) for v in (rect.x0, rect.y0, rect.x1, rect.y1)),
            final_font_size,
        )

    # FIX P0: chỉ ghi lên Target Page SAU KHI đã chốt size (1 lần duy nhất).
    try:
        page.insert_textbox(
            rect,
            text,
            fontsize=final_font_size,
            fontfile=effective_font_path if effective_font_path else None,
            fontname=font_name,
            color=color,
            align=align,
        )
    except Exception as e:
        logger.error("[FIX P0] Không thể vẽ text lên target page: %s", e)
        # Không raise để một block lỗi không giết cả tài liệu; caller log tiếp.

    return final_font_size


# ==============================================================================
# 4. MAIN ENGINE: PDF TRANSLATOR
# ==============================================================================

class PDFTranslator:
    def __init__(
        self,
        llm_service: Optional[LLMService] = None,
        ocr_service: Optional[OCRService] = None,
        enable_ocr: Optional[bool] = None,
        max_pages: Optional[int] = None,
        max_blocks_per_call: Optional[int] = None,
    ):
        self.llm_service = llm_service or LLMService()
        self.font_path = FontEmbedMaster.get_font_path()
        # Đợt C: font tìm được nhưng nhúng không nổi (vd .ttc) thì bỏ ngay từ
        # đầu để rơi về helv, thay vì lỗi lặp lại ở từng block.
        if self.font_path and not _font_usable(self.font_path):
            logger.warning(
                "[Đợt C] Font %s nhúng thử thất bại — dùng helv thay thế.",
                self.font_path,
            )
            self.font_path = ""
        # Đợt 2: trần tài nguyên để một PDF vector nhẹ mà hàng trăm trang
        # không thổi chi phí token hay giữ worker quá lâu.
        self.max_pages = max_pages or _int_env("MAX_PDF_PAGES", 300)
        self.max_blocks_per_call = max_blocks_per_call or _int_env("MAX_BLOCKS_PER_CALL", 60)
        # FIX P1: OCR hybrid — inject được từ ngoài (tests/job manager), mặc định đọc ENV.
        if ocr_service is not None:
            self.ocr = ocr_service
            if enable_ocr is not None:
                self.ocr.enabled = bool(enable_ocr)
        else:
            self.ocr = OCRService(enabled=enable_ocr) if enable_ocr is not None else OCRService()
        logger.info(f"[Ponytail PDFTranslator] Engine khởi động với font: {self.font_path}")
        logger.info(f"[FIX P1] OCR hybrid available={self.ocr.is_available()} ({self.ocr.status()})")

    # ------------------------------------------------------------------
    # FIX P1/P2: bóc tách 1 trang dùng chung cho cả pipeline chuẩn + OCR fallback.
    def _extract_blocks_from_text_dict(
        self, page_idx: int, text_dict: Dict[str, Any],
        mask_counter: int, page_mask_map: Dict[str, str],
    ) -> Tuple[List[Dict[str, Any]], int]:
        extracted: List[Dict[str, Any]] = []
        raw_blocks = text_dict.get("blocks", [])
        for b_idx, block in enumerate(raw_blocks):
            if block.get("type", 0) != 0:
                continue
            bbox = block.get("bbox")
            lines = block.get("lines", [])
            if not lines:
                continue
            line_texts = []
            spans_all = []
            for line in lines:
                line_str = "".join(span.get("text", "") for span in line.get("spans", []))
                if line_str.strip():
                    line_texts.append(line_str.strip())
                spans_all.extend(line.get("spans", []))
            block_raw_text = " ".join(line_texts).strip()
            if not block_raw_text:
                continue
            sizes = [s.get("size", 10.0) for s in spans_all if s.get("text", "").strip()]
            dominant_size = max(set(sizes), key=sizes.count) if sizes else 10.0
            colors = [s.get("color", 0) for s in spans_all if s.get("text", "").strip()]
            dominant_color_int = max(set(colors), key=colors.count) if colors else 0
            r = ((dominant_color_int >> 16) & 255) / 255.0
            g = ((dominant_color_int >> 8) & 255) / 255.0
            b = (dominant_color_int & 255) / 255.0
            masked_text, mask_map, mask_counter = LaTeXMasker.mask(block_raw_text, mask_counter)
            page_mask_map.update(mask_map)
            extracted.append({
                "id": f"p{page_idx}_b{b_idx}",
                "text": masked_text,
                "bbox": list(bbox) if bbox else [0, 0, 0, 0],
                "font_size": float(dominant_size),
                "color": (float(r), float(g), float(b)),
                "source": "pdf",
            })
        return extracted, mask_counter

    # ------------------------------------------------------------------
    async def translate_pdf(
        self,
        input_pdf_bytes: bytes,
        target_lang: str = "Vietnamese",
        source_lang: str = "Auto",
        progress_callback: ProgressCallback = None,
        enable_ocr: Optional[bool] = None,
    ) -> bytes:
        """
        Giữ nguyên signature cũ (P0) để /api/translate không vỡ.
        FIX P1: thêm progress_callback + enable_ocr (None = dùng cấu hình engine).
        """
        out, _report = await self.translate_pdf_with_report(
            input_pdf_bytes, target_lang, source_lang,
            progress_callback=progress_callback, enable_ocr=enable_ocr,
        )
        return out

    async def translate_pdf_with_report(
        self,
        input_pdf_bytes: bytes,
        target_lang: str = "Vietnamese",
        source_lang: str = "Auto",
        progress_callback: ProgressCallback = None,
        enable_ocr: Optional[bool] = None,
        only_pages: Optional[List[int]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Tuple[bytes, Dict[str, Any]]:
        """
        FIX P1/P2: pipeline đầy đủ có progress + OCR + QA report.
        - only_pages: giới hạn trang dịch (dùng cho preview 1 trang).
        - should_cancel: callback đồng bộ trả True khi user hủy job;
          worker kiểm tra sau mỗi trang nên hủy có hiệu lực trong ~1 trang.
        - Trả (pdf_bytes, report{pages_layout, fit_results, qa, ocr_pages, ...}).
        FIX P0: doc/scratch_doc đóng trong finally mọi trường hợp.
        """
        if not input_pdf_bytes or not input_pdf_bytes.startswith(b"%PDF-"):
            raise ValueError("Dữ liệu đầu vào không phải PDF hợp lệ (thiếu header %PDF-).")
        use_ocr = self.ocr.enabled if enable_ocr is None else bool(enable_ocr)

        doc: Optional[fitz.Document] = None
        scratch_doc: Optional[fitz.Document] = None
        scratch_page: Optional[fitz.Page] = None
        pages_layout: List[Dict[str, Any]] = []
        fit_results: Dict[str, Dict[str, Any]] = {}
        ocr_pages = 0
        try:
            try:
                doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")
            except Exception as e:
                raise ValueError(f"Không thể mở PDF (file có thể mã hoá/hỏng): {e}") from e

            # Đợt B: PDF đặt mật khẩu mở ra 0 block chữ — trước đây pipeline vẫn
            # báo done và chấm điểm 100 giả. Chặn ngay, bảo user gỡ mật khẩu.
            try:
                needs_pass = bool(doc.needs_pass)
            except Exception:
                needs_pass = False
            if needs_pass:
                raise ValueError(
                    "PDF này đặt mật khẩu bảo vệ nên không đọc được nội dung. "
                    "Bạn gỡ mật khẩu rồi gửi lại."
                )

            scratch_doc = fitz.open()
            scratch_page = scratch_doc.new_page(width=2000, height=2000)

            total_pages = len(doc)
            # Đợt 2: chặn PDF vượt trần trang ngay khi mở để không đốt token vô ích.
            if total_pages > self.max_pages and only_pages is None:
                raise PdfTooLargeError(
                    f"PDF có {total_pages} trang, vượt giới hạn {self.max_pages} trang. "
                    "Bạn chia nhỏ file rồi gửi từng phần."
                )
            if only_pages is not None:
                wanted = [p for p in only_pages if 0 <= p < total_pages]
                if not wanted:
                    raise ValueError("only_pages không chứa trang hợp lệ nào.")
            else:
                wanted = list(range(total_pages))

            logger.info(
                f"[PDFTranslator] Bắt đầu xử lý PDF {total_pages} trang "
                f"(dịch {len(wanted)} trang) -> {target_lang} ocr={use_ocr}"
            )
            await _emit_progress(progress_callback, 0, len(wanted), "started", f"total={len(wanted)}")

            mask_counter = 0
            done = 0
            for page_idx in wanted:
                # Đợt 2: điểm hủy — worker báo hủy thì dừng trước khi chạm trang mới.
                if should_cancel is not None and should_cancel():
                    raise JobCancelledError("Job đã bị hủy bởi người dùng.")
                try:
                    page = doc[page_idx]
                    # Đợt C: trang scratch mới cho mỗi trang PDF. insert_textbox
                    # dry-run ghi thật lên trang nên dùng chung cả tài liệu là
                    # tích hàng chục nghìn object (tốn RAM, càng về sau càng chậm).
                    if pages_layout:
                        try:
                            scratch_doc.delete_page(0)
                        except Exception:
                            pass
                        scratch_page = scratch_doc.new_page(width=2000, height=2000)
                    text_dict = page.get_text("dict", flags=fitz.TEXT_DEHYPHENATE)
                    page_mask_map: Dict[str, str] = {}
                    extracted, mask_counter = self._extract_blocks_from_text_dict(
                        page_idx, text_dict, mask_counter, page_mask_map
                    )
                    ocr_used = False
                    # FIX P1: OCR hybrid — chỉ khi trang không có text layer.
                    # Đợt B: render pixmap TRÊN luồng chính (fitz không an toàn
                    # luồng), chỉ ném ảnh thuần (PIL) sang thread chạy tesseract.
                    if not extracted and use_ocr:
                        if self.ocr.is_available():
                            try:
                                rendered = self.ocr.render_page_image(page)
                                if rendered is None:
                                    ocr_blocks_raw = []
                                else:
                                    img, rect_tuple, pix_w, pix_h = rendered
                                    page_no = getattr(page, "number", page_idx)
                                    ocr_blocks_raw = await asyncio.to_thread(
                                        self.ocr.extract_blocks_from_image,
                                        img, rect_tuple, pix_w, pix_h, page_no,
                                    )
                                for ob in ocr_blocks_raw:
                                    masked, mmap, mask_counter = LaTeXMasker.mask(
                                        ob["text"], mask_counter
                                    )
                                    page_mask_map.update(mmap)
                                    # Đánh lại id theo page để không đụng block pdf.
                                    ob_id = f"p{page_idx}_ocr{len(extracted)}"
                                    extracted.append({
                                        "id": ob_id,
                                        "text": masked,
                                        "bbox": list(ob["bbox"]),
                                        "font_size": float(ob.get("font_size", 10.0)),
                                        "color": tuple(ob.get("color", (0, 0, 0))),
                                        "source": "ocr",
                                    })
                                if extracted:
                                    ocr_used = True
                                    ocr_pages += 1
                                    logger.info(
                                        "[FIX P1] Trang %d scan — OCR cứu %d blocks.",
                                        page_idx, len(extracted),
                                    )
                            except Exception as e:
                                logger.warning("[FIX P1] OCR trang %d thất bại: %s", page_idx, e)
                        else:
                            logger.info(
                                "[FIX P1] Trang %d không có text và OCR unavailable — bỏ qua.",
                                page_idx,
                            )
                    # Đợt C: trang lai — có 1-2 dòng chữ (số trang, header) nhưng
                    # thân là ảnh scan. Trước đây điều kiện `not extracted` bỏ qua
                    # luôn nên thân bài giữ nguyên tiếng gốc mà không cờ hiệu gì.
                    # Giờ OCR bổ sung block nào không đè lên chữ đã có.
                    if (extracted and use_ocr and not ocr_used
                            and self.ocr.is_available()):
                        # Đợt C: lỗi OCR bổ sung không được giết cả trang (chữ
                        # text layer vẫn phải dịch bình thường).
                        try:
                            sparse_limit = _int_env("OCR_SPARSE_CHARS", 50)
                            text_len = sum(len(b.get("text", "")) for b in extracted)
                            if 0 < text_len < sparse_limit:
                                try:
                                    has_images = bool(page.get_images(full=True))
                                except Exception:
                                    has_images = False
                                if has_images:
                                    rendered_sparse = self.ocr.render_page_image(page)
                                    if rendered_sparse is not None:
                                        s_img, s_rect, s_w, s_h = rendered_sparse
                                        extra = await asyncio.to_thread(
                                            self.ocr.extract_blocks_from_image,
                                            s_img, s_rect, s_w, s_h,
                                            getattr(page, "number", page_idx),
                                        )
                                        added = 0
                                        for ob in extra:
                                            ob_box = [float(v) for v in ob["bbox"]]
                                            if any(_iou(ob_box, [float(v) for v in b["bbox"]]) >= 0.5
                                                   for b in extracted):
                                                continue
                                            masked, mmap, mask_counter = LaTeXMasker.mask(
                                                ob["text"], mask_counter
                                            )
                                            page_mask_map.update(mmap)
                                            extracted.append({
                                                "id": f"p{page_idx}_ocr{len(extracted)}",
                                                "text": masked,
                                                "bbox": ob_box,
                                                "font_size": float(ob.get("font_size", 10.0)),
                                                "color": tuple(ob.get("color", (0, 0, 0))),
                                                "source": "ocr",
                                            })
                                            added += 1
                                        if added:
                                            ocr_used = True
                                            ocr_pages += 1
                                            logger.info(
                                                "[Đợt C] Trang %d lai scan — OCR bổ sung %d blocks.",
                                                page_idx, added,
                                            )
                        except Exception as sparse_e:
                            logger.warning(
                                "[Đợt C] OCR bổ sung trang %d thất bại (giữ text layer): %s",
                                page_idx, sparse_e,
                            )

                    # Ghi layout NGAY CẢ khi trang trắng (để QA thấy trang 0 block).
                    pages_layout.append({
                        "page_index": page_idx,
                        "blocks": extracted,
                        "mask_map_size": len(page_mask_map),
                        "ocr_used": ocr_used,
                    })
                    if not extracted:
                        done += 1
                        await _emit_progress(progress_callback, done, len(wanted), "page_done", f"page={page_idx} empty")
                        continue

                    await _emit_progress(progress_callback, done, len(wanted), "translating", f"page={page_idx} n={len(extracted)}")
                    # Đợt 2: chia block thành mẻ nhỏ để một trang dày đặc
                    # không tạo một request LLM khổng lồ.
                    translated_dict: Dict[str, str] = {}
                    chunk = max(1, self.max_blocks_per_call)
                    for start in range(0, len(extracted), chunk):
                        if should_cancel is not None and should_cancel():
                            raise JobCancelledError("Job đã bị hủy bởi người dùng.")
                        part = extracted[start:start + chunk]
                        part_result = await self.llm_service.translate_blocks(
                            part, target_lang=target_lang, source_lang=source_lang,
                        )
                        translated_dict.update(part_result)
                    translated_dict = validate_and_repair_translations(extracted, translated_dict)

                    # Redact chỉ cho block pdf (OCR không có text layer để redact).
                    has_pdf_blocks = any(b.get("source", "pdf") != "ocr" for b in extracted)
                    if has_pdf_blocks:
                        for item in extracted:
                            if item.get("source", "pdf") == "ocr":
                                continue
                            try:
                                rect = fitz.Rect(item["bbox"])
                                if rect.is_empty or rect.width <= 0 or rect.height <= 0:
                                    continue
                                page.add_redact_annot(rect, fill=(1, 1, 1))
                            except Exception as e:
                                logger.warning("[FIX P0] Không thể đặt redact cho block %s: %s", item["id"], e)
                                continue
                        try:
                            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
                        except Exception as e:
                            logger.error("[FIX P0] apply_redactions thất bại ở trang %d: %s", page_idx, e)
                            raise
                    else:
                        # FIX P1: trang OCR — phủ nền trắng lên vùng chữ scan trước khi vẽ chữ dịch.
                        for item in extracted:
                            try:
                                rect = fitz.Rect(item["bbox"])
                                if rect.is_empty:
                                    continue
                                page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1), overlay=True)
                            except Exception as e:
                                logger.warning("[FIX P1] Không thể phủ trắng OCR block %s: %s", item["id"], e)

                    await _emit_progress(progress_callback, done, len(wanted), "redrawing", f"page={page_idx}")
                    for item in extracted:
                        try:
                            block_id = item["id"]
                            translated_masked = translated_dict.get(block_id, item["text"])
                            final_text = LaTeXMasker.unmask(translated_masked, page_mask_map)
                            leftover_count, leftover = count_math_masks(final_text)
                            if leftover_count > 0:
                                logger.warning(
                                    "[FIX P0] Block %s còn sót token %s sau unmask.", block_id, leftover,
                                )
                            rect = fitz.Rect(item["bbox"])
                            initial_size = item["font_size"]
                            color = tuple(item["color"])  # type: ignore
                            final_size = insert_text_with_fit(
                                page=page, scratch_page=scratch_page, rect=rect,
                                text=final_text, initial_font_size=initial_size,
                                font_path=self.font_path, color=color, align=0,
                            )
                            # FIX P2: thu thập kết quả fit cho QA + editor.
                            fit_results[block_id] = {
                                "final_size": float(final_size),
                                "initial_size": float(initial_size),
                                "final_text": final_text,
                                "translated_masked": translated_masked,
                                "original_masked": item["text"],
                            }
                        except Exception as e:
                            logger.warning("[FIX P0] Bỏ qua block %s do lỗi vẽ lại: %s", item.get("id", "?"), e)
                            continue
                    done += 1
                    await _emit_progress(progress_callback, done, len(wanted), "page_done", f"page={page_idx}")
                except RuntimeError:
                    raise
                except Exception as page_e:
                    logger.exception("[FIX P0] Lỗi ở trang %d — bỏ qua: %s", page_idx, page_e)
                    done += 1
                    await _emit_progress(progress_callback, done, len(wanted), "page_error", f"page={page_idx}")
                    continue

            try:
                output_bytes = doc.tobytes(garbage=4, deflate=True, clean=True)
            except Exception as e:
                raise RuntimeError(f"Không thể xuất PDF sau dịch: {e}") from e

            # FIX P2: build QA report từ layout + fit (kể cả trang trắng).
            try:
                qa = build_qa_report(pages_layout, fit_results)
            except Exception as e:
                logger.warning("[FIX P2] build QA thất bại (bỏ qua): %s", e)
                qa = {"summary": {"total_blocks": len(fit_results)}, "blocks": []}
            report = {
                "pages_layout": pages_layout,
                "fit_results": fit_results,
                "qa": qa,
                "ocr_pages": ocr_pages,
                "target_lang": target_lang,
                "source_lang": source_lang,
            }
            await _emit_progress(progress_callback, len(wanted), len(wanted), "done", f"ocr_pages={ocr_pages}")
            return output_bytes, report
        finally:
            for _doc, _name in ((doc, "doc"), (scratch_doc, "scratch_doc")):
                try:
                    if _doc is not None:
                        _doc.close()
                except Exception as close_e:
                    logger.warning("[FIX P0] Lỗi khi close %s: %s", _name, close_e)
            gc.collect()

    # ------------------------------------------------------------------
    async def translate_single_page(
        self,
        input_pdf_bytes: bytes,
        page_number: int,
        target_lang: str = "Vietnamese",
        source_lang: str = "Auto",
        progress_callback: ProgressCallback = None,
        enable_ocr: Optional[bool] = None,
    ) -> Tuple[bytes, Dict[str, Any]]:
        """
        FIX P2: preview 1 trang — tách trang ra doc mới rồi dịch (nhanh, rẻ token).
        page_number: 0-based. Trả (pdf_bytes_1_trang, report).
        """
        if not input_pdf_bytes or not input_pdf_bytes.startswith(b"%PDF-"):
            raise ValueError("Dữ liệu đầu vào không phải PDF hợp lệ.")
        src: Optional[fitz.Document] = None
        single: Optional[fitz.Document] = None
        try:
            try:
                src = fitz.open(stream=input_pdf_bytes, filetype="pdf")
            except Exception as e:
                raise ValueError(f"Không thể mở PDF: {e}") from e
            # Đợt B: cùng kiểm tra mã hóa như pipeline chính (tránh 500 lạ ở insert_pdf).
            try:
                src_locked = bool(src.needs_pass)
            except Exception:
                src_locked = False
            if src_locked:
                raise ValueError(
                    "PDF này đặt mật khẩu bảo vệ nên không đọc được nội dung. "
                    "Bạn gỡ mật khẩu rồi gửi lại."
                )
            if not (0 <= page_number < len(src)):
                raise ValueError(f"page_number {page_number} ngoài khoảng 0..{len(src)-1}.")
            single = fitz.open()
            single.insert_pdf(src, from_page=page_number, to_page=page_number)
            single_bytes = single.tobytes(garbage=4, deflate=True, clean=True)
        finally:
            for _d in (src, single):
                try:
                    if _d is not None:
                        _d.close()
                except Exception:
                    pass
            gc.collect()
        # Dịch doc 1 trang; remap page_index về số trang gốc để editor dùng tiếp.
        out, report = await self.translate_pdf_with_report(
            single_bytes, target_lang, source_lang,
            progress_callback=progress_callback, enable_ocr=enable_ocr,
        )
        for pg in report.get("pages_layout", []):
            # Trang duy nhất trong doc tạm có index 0 -> gán lại số gốc.
            pg["preview_of_page"] = page_number
        report["preview_page"] = page_number
        return out, report

    # ------------------------------------------------------------------
    def re_render_with_edits(
        self,
        original_pdf_bytes: bytes,
        pages_layout: List[Dict[str, Any]],
        final_texts_by_id: Dict[str, str],
        only_pages: Optional[List[int]] = None,
    ) -> bytes:
        """
        FIX P2: editor hậu kỳ — vẽ lại PDF từ layout cũ + text đã sửa, KHÔNG gọi LLM.
        - final_texts_by_id: {block_id: text_cuối_đã_unmask (editor sửa)}.
          Block nào thiếu => giữ final_text cũ trong fit_results (caller phải merge trước).
        - Trang OCR: phủ trắng + vẽ; trang thường: redact + vẽ.
        """
        if not original_pdf_bytes or not original_pdf_bytes.startswith(b"%PDF-"):
            raise ValueError("original_pdf_bytes không phải PDF hợp lệ.")
        doc: Optional[fitz.Document] = None
        scratch_doc: Optional[fitz.Document] = None
        try:
            doc = fitz.open(stream=original_pdf_bytes, filetype="pdf")
            scratch_doc = fitz.open()
            scratch_page = scratch_doc.new_page(width=2000, height=2000)
            wanted = set(only_pages) if only_pages is not None else None
            for pg in pages_layout:
                p_idx = pg.get("page_index", 0)
                if wanted is not None and p_idx not in wanted:
                    continue
                if not (0 <= p_idx < len(doc)):
                    continue
                page = doc[p_idx]
                blocks = pg.get("blocks", [])
                if not blocks:
                    continue
                # Đợt C: scratch mới mỗi trang như pipeline chính (tránh tích object).
                try:
                    scratch_doc.delete_page(0)
                except Exception:
                    pass
                scratch_page = scratch_doc.new_page(width=2000, height=2000)
                # Phase 1: redact text gốc của block pdf.
                for b in blocks:
                    if b.get("source", "pdf") == "ocr":
                        continue
                    b_id = b["id"]
                    if b_id not in final_texts_by_id:
                        continue
                    try:
                        rect = fitz.Rect(b["bbox"])
                        if rect.is_empty or rect.width <= 0 or rect.height <= 0:
                            continue
                        page.add_redact_annot(rect, fill=(1, 1, 1))
                    except Exception as e:
                        logger.warning("[FIX P2] redact edit block %s thất bại: %s", b.get("id"), e)
                try:
                    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
                except Exception as e:
                    logger.warning("[FIX P2] apply_redactions (edit) trang %d: %s", p_idx, e)
                # Phase 2: phủ trắng OCR + vẽ tất cả.
                for b in blocks:
                    b_id = b["id"]
                    if b_id not in final_texts_by_id:
                        continue
                    try:
                        rect = fitz.Rect(b["bbox"])
                        if b.get("source", "pdf") == "ocr" and not rect.is_empty:
                            try:
                                page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1), overlay=True)
                            except Exception:
                                pass
                        insert_text_with_fit(
                            page=page, scratch_page=scratch_page, rect=rect,
                            text=final_texts_by_id[b_id],
                            initial_font_size=float(b.get("font_size", 10.0)),
                            font_path=self.font_path,
                            color=tuple(b.get("color", (0, 0, 0))),
                            align=0,
                        )
                    except Exception as e:
                        logger.warning("[FIX P2] vẽ edit block %s thất bại: %s", b_id, e)
                        continue
            try:
                return doc.tobytes(garbage=4, deflate=True, clean=True)
            except Exception as e:
                raise RuntimeError(f"Không thể xuất PDF sau edit: {e}") from e
        finally:
            for _d in (doc, scratch_doc):
                try:
                    if _d is not None:
                        _d.close()
                except Exception:
                    pass
            gc.collect()
