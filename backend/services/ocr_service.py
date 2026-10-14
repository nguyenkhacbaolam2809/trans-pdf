"""
services/ocr_service.py
=======================
FIX P1: OCR hybrid cho PDF scan (không có text layer).

Chiến lược:
- Trang có text blocks (get_text("dict")) => đi pipeline chuẩn, KHÔNG OCR (nhanh).
- Trang trắng text (scan) => render pixmap -> Pillow -> pytesseract.image_to_data
  => gom words thành blocks có bbox (quy đổi về toạ độ PDF) => đi tiếp pipeline
  dịch + overlay (vẽ nền trắng + chữ dịch đè lên ảnh, KHÔNG xoá ảnh gốc).

Thiết kế graceful-degradation:
- Thiếu Pillow / pytesseract / tesseract binary => is_available()==False,
  translator bỏ qua OCR và log rõ (không crash).
- Bật/tắt qua ENV: ENABLE_OCR=true/false, OCR_MIN_CONF, OCR_DPI.
"""

import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger("trans_pdf.ocr")

try:
    import fitz  # type: ignore
except Exception:  # pragma: no cover
    fitz = None  # type: ignore

try:
    from PIL import Image  # type: ignore
    _PIL_OK = True
except Exception:
    Image = None  # type: ignore
    _PIL_OK = False

try:
    import pytesseract  # type: ignore
    _TESS_PY_OK = True
except Exception:
    pytesseract = None  # type: ignore
    _TESS_PY_OK = False


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


class OCRService:
    def __init__(
        self,
        enabled: bool | None = None,
        dpi: int | None = None,
        min_conf: int | None = None,
        lang: str | None = None,
    ):
        self.enabled = _get_bool("ENABLE_OCR", True) if enabled is None else bool(enabled)
        self.dpi = _get_int("OCR_DPI", 200) if dpi is None else int(dpi)
        self.min_conf = _get_int("OCR_MIN_CONF", 30) if min_conf is None else int(min_conf)
        self.lang = (os.getenv("OCR_LANG", "eng") if lang is None else lang)
        self._binary_ok: bool | None = None

    # ------------------------------------------------------------------
    def _check_binary(self) -> bool:
        """Kiểm tra tesseract binary 1 lần duy nhất (cache kết quả)."""
        if self._binary_ok is not None:
            return self._binary_ok
        if not _TESS_PY_OK:
            self._binary_ok = False
            return False
        try:
            assert pytesseract is not None
            pytesseract.get_tesseract_version()
            self._binary_ok = True
        except Exception as e:
            logger.warning("[FIX P1] Không tìm thấy tesseract binary: %s", e)
            self._binary_ok = False
        return self._binary_ok

    def is_available(self) -> bool:
        """OCR sẵn sàng thật sự? (công tắc + Pillow + pytesseract + binary)."""
        if not self.enabled:
            return False
        if fitz is None or not _PIL_OK:
            return False
        return self._check_binary()

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "pillow": _PIL_OK,
            "pytesseract_py": _TESS_PY_OK,
            "binary": self._check_binary() if (_TESS_PY_OK and _PIL_OK) else False,
            "available": self.is_available(),
            "dpi": self.dpi,
            "min_conf": self.min_conf,
            "lang": self.lang,
        }

    # ------------------------------------------------------------------
    def render_page_image(self, page):
        """
        Đợt B: render 1 trang thành ảnh PIL + thông tin hình học.
        PHẢI gọi trên luồng chính (dùng đối tượng fitz.Page, PyMuPDF không
        an toàn luồng). Trả (img, rect_tuple, pix_w, pix_h) hoặc None khi lỗi.
        """
        try:
            zoom = max(1.0, min(4.0, self.dpi / 72.0))
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            rect = page.rect
            return img, (rect.x0, rect.y0, rect.x1, rect.y1), pix.width, pix.height
        except Exception as e:
            logger.warning("[FIX P1] Render pixmap cho OCR thất bại: %s", e)
            return None

    def extract_blocks_from_image(self, img, rect_tuple, pix_w: int, pix_h: int,
                                  page_number: Any = "?") -> List[Dict[str, Any]]:
        """
        Đợt B: OCR từ ảnh đã render (PIL + tesseract, không chạm fitz) nên an
        toàn gọi trong thread worker. rect_tuple = (x0, y0, x1, y1) của trang.
        """
        if not self.is_available():
            return []
        try:
            assert pytesseract is not None
            data = pytesseract.image_to_data(img, lang=self.lang, output_type=pytesseract.Output.DICT)
        except Exception as e:
            logger.warning("[FIX P1] pytesseract.image_to_data thất bại: %s", e)
            return []

        n = len(data.get("text", []))
        # Gom words theo (block_num, par_num, line_num) thành dòng/block.
        groups: Dict[tuple, List[int]] = {}
        for i in range(n):
            try:
                conf = float(data.get("conf", ["-1"])[i])
            except (ValueError, TypeError):
                conf = -1
            word = (data.get("text", [""])[i] or "").strip()
            if not word or conf < self.min_conf:
                continue
            key = (
                data.get("block_num", [0])[i],
                data.get("par_num", [0])[i],
                data.get("line_num", [0])[i],
            )
            groups.setdefault(key, []).append(i)

        if not groups:
            return []

        # Tỉ lệ quy đổi ảnh -> PDF.
        rx0, ry0, rx1, ry1 = rect_tuple
        sx = (rx1 - rx0) / float(pix_w) if pix_w else 1.0
        sy = (ry1 - ry0) / float(pix_h) if pix_h else 1.0

        blocks: List[Dict[str, Any]] = []
        for idx, (key, ids) in enumerate(sorted(groups.items())):
            words = [(data["text"][i] or "").strip() for i in ids]
            text = " ".join(w for w in words if w).strip()
            if not text:
                continue
            xs = [data["left"][i] for i in ids]
            ys = [data["top"][i] for i in ids]
            ws = [data["width"][i] for i in ids]
            hs = [data["height"][i] for i in ids]
            x0 = min(xs) * sx + rx0
            y0 = min(ys) * sy + ry0
            x1 = (max(x0_s + w for x0_s, w in zip(xs, ws))) * sx + rx0
            y1 = (max(y0_s + h for y0_s, h in zip(ys, hs))) * sy + ry0
            # Padding nhẹ để text dịch (thường dài hơn) có chỗ thở.
            pad = 2.0
            x0 = max(rx0, x0 - pad)
            y0 = max(ry0, y0 - pad)
            x1 = min(rx1, x1 + pad)
            y1 = min(ry1, y1 + pad)
            # Ước lượng font-size từ chiều cao dòng (heuristic, fit-loop sẽ chỉnh tiếp).
            avg_h_px = sum(hs) / max(1, len(hs))
            est_size = max(6.0, min(14.0, avg_h_px * sy * 0.75))
            blocks.append({
                "text": text,  # text thô, caller sẽ mask LaTeX sau (OCR hiếm có LaTeX)
                "bbox": [float(x0), float(y0), float(x1), float(y1)],
                "font_size": float(est_size),
                "color": (0.0, 0.0, 0.0),
                "source": "ocr",
            })
        logger.info("[FIX P1] OCR trang %s: %d words -> %d blocks.", page_number, n, len(blocks))
        return blocks

    def extract_blocks_from_page(self, page) -> List[Dict[str, Any]]:
        """
        Lối tắt đồng bộ (render + OCR trên cùng luồng gọi). Chỉ dùng trong
        test hoặc khi chắc chắn đang ở luồng chính; pipeline dịch dùng
        render_page_image + extract_blocks_from_image để tách thread an toàn.
        """
        if not self.is_available():
            return []
        rendered = self.render_page_image(page)
        if rendered is None:
            return []
        img, rect_tuple, pix_w, pix_h = rendered
        return self.extract_blocks_from_image(
            img, rect_tuple, pix_w, pix_h, getattr(page, "number", "?")
        )
