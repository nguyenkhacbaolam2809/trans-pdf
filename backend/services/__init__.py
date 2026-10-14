from .pdf_translator import PDFTranslator, LaTeXMasker, FontEmbedMaster, insert_text_with_fit
from .llm_service import LLMService, GEMINI_MODEL, OPENAI_MODEL, PROMPT_VERSION
# FIX P1: OCR hybrid cho PDF scan.
from .ocr_service import OCRService
# FIX P1: job queue + WebSocket progress.
from .job_queue import JobManager, Job, QueueFullError, STATUS_CANCELLED
# FIX P2: cache bản dịch + QA/editor.
from .translation_cache import TranslationCache, make_cache_key
from .layout_qa import build_qa_report, score_block
# Đợt 1 an toàn: sanitize lỗi + request ID.
from .safety import sanitize_error, new_request_id, JobCancelledError, PdfTooLargeError

__all__ = [
    "PDFTranslator",
    "LaTeXMasker",
    "FontEmbedMaster",
    "insert_text_with_fit",
    "LLMService",
    "GEMINI_MODEL",
    "OPENAI_MODEL",
    "PROMPT_VERSION",
    "OCRService",
    "JobManager",
    "Job",
    "QueueFullError",
    "STATUS_CANCELLED",
    "TranslationCache",
    "make_cache_key",
    "build_qa_report",
    "score_block",
    "sanitize_error",
    "new_request_id",
    "JobCancelledError",
    "PdfTooLargeError",
]
