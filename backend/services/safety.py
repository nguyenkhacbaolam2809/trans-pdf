"""
services/safety.py
==================
Đợt 1 an toàn: che khóa API trong log/lỗi + mã request để truy vết.

- Mọi lỗi provider LLM đều đi qua sanitize_error() trước khi ghi log hay
  trả về client. Không bao giờ để `key=...` hay `Bearer ...` lọt ra ngoài.
- new_request_id() cấp mã ngắn cho mỗi request HTTP để nối log backend
  với báo lỗi frontend (header X-Request-ID).
"""

import re
import uuid

_KEY_PARAM_RE = re.compile(r"(key=)[^&\s\"']+", re.IGNORECASE)
_BEARER_RE = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)

# Mã lỗi thân thiện cho client (không kèm chi tiết nội bộ).
ERR_LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
ERR_BAD_PDF = "BAD_PDF"
ERR_INTERNAL = "INTERNAL_ERROR"
ERR_BUSY = "SYSTEM_BUSY"


class JobCancelledError(RuntimeError):
    """Worker ném khi user hủy job giữa chừng (kiểm tra sau mỗi trang)."""


class PdfTooLargeError(ValueError):
    """PDF vượt trần tài nguyên (số trang / số block). Map thành HTTP 413."""


def new_request_id() -> str:
    return uuid.uuid4().hex[:8]


def sanitize_error(msg: object, secrets: list[str] | None = None) -> str:
    """Che secret trong chuỗi lỗi, cắt còn tối đa 200 ký tự."""
    text = str(msg) if msg is not None else ""
    for secret in secrets or []:
        if secret and len(secret) >= 4 and secret in text:
            text = text.replace(secret, "***")
    text = _KEY_PARAM_RE.sub(r"\1***", text)
    text = _BEARER_RE.sub(r"\1***", text)
    text = " ".join(text.split())
    if len(text) > 200:
        text = text[:200] + "…"
    return text or "unknown error"
