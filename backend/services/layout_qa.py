"""
services/layout_qa.py
=====================
FIX P2: Layout QA — báo cáo hậu kiểm sau mỗi lần dịch để editor sửa.

Mỗi block được chấm:
- mask_ok: mask gốc vs dịch có khớp (sau validate + fallback thì luôn True,
  nhưng vẫn ghi để editor thấy block nào đã bị fallback giữ câu gốc).
- overflow: final_font_size <= min_font_size + eps => text vẫn tràn ở min size.
- small_font: final < 7pt => khó đọc, editor nên rút gọn câu.
- expansion: len(dịch)/len(gốc) => phát hiện câu dịch phình quá to.
- fell_back: True nếu bản dịch == câu gốc masked (LLM retry vẫn lỗi / cache miss lỗi).

build_qa_report() tổng hợp cấp trang + toàn tài liệu + quality_score 0-100.
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger("trans_pdf.qa")

OVERFLOW_EPS = 0.01
SMALL_FONT_THRESHOLD = 7.0
EXPANSION_WARN_RATIO = 1.6


def _is_fallback(original_masked: str, translated_masked: str) -> bool:
    return (translated_masked or "") == (original_masked or "")


def score_block(
    block_id: str,
    original_masked: str,
    translated_masked: str,
    final_text: str,
    initial_size: float,
    final_size: float,
    min_size: float = 4.0,
) -> Dict[str, Any]:
    overflow = bool(final_size <= min_size + OVERFLOW_EPS)
    small_font = bool(final_size < SMALL_FONT_THRESHOLD)
    orig_len = len(original_masked or "")
    trans_len = len(translated_masked or "")
    expansion = round(trans_len / orig_len, 2) if orig_len else 1.0
    expansion_warn = bool(expansion >= EXPANSION_WARN_RATIO)
    fell_back = _is_fallback(original_masked, translated_masked)
    issues: List[str] = []
    if overflow:
        issues.append("overflow")
    if small_font:
        issues.append("small_font")
    if expansion_warn:
        issues.append("expansion")
    if fell_back and orig_len > 0:
        issues.append("fallback_to_source")
    return {
        "id": block_id,
        "initial_size": round(float(initial_size), 2),
        "final_size": round(float(final_size), 2),
        "overflow": overflow,
        "small_font": small_font,
        "expansion_ratio": expansion,
        "fell_back": fell_back,
        "orig_chars": orig_len,
        "trans_chars": trans_len,
        "final_chars": len(final_text or ""),
        "issues": issues,
    }


def build_qa_report(
    pages_layout: List[Dict[str, Any]],
    fit_results: Dict[str, Dict[str, Any]],
    min_size: float = 4.0,
) -> Dict[str, Any]:
    """
    pages_layout: [{page_index, blocks:[{id,text,bbox,font_size,source?}], mask_map_size, ocr_used}]
    fit_results: {block_id: {final_size, final_text, translated_masked, original_masked, initial_size}}
    """
    blocks_out: List[Dict[str, Any]] = []
    overflow_n = small_n = fallback_n = 0
    total = 0
    for pg in pages_layout:
        for b in pg.get("blocks", []):
            b_id = b["id"]
            fr = fit_results.get(b_id, {})
            total += 1
            row = score_block(
                block_id=b_id,
                original_masked=b.get("text", ""),
                translated_masked=fr.get("translated_masked", b.get("text", "")),
                final_text=fr.get("final_text", ""),
                initial_size=fr.get("initial_size", b.get("font_size", 10.0)),
                final_size=fr.get("final_size", b.get("font_size", 10.0)),
                min_size=min_size,
            )
            row["page"] = pg.get("page_index", 0)
            row["source"] = b.get("source", "pdf")
            blocks_out.append(row)
            if row["overflow"]:
                overflow_n += 1
            if row["small_font"]:
                small_n += 1
            if row["fell_back"]:
                fallback_n += 1

    # Điểm chất lượng: trừ điểm theo tỉ lệ lỗi (overflow nặng nhất).
    if total == 0:
        score = 100
    else:
        score = 100 - round(
            (overflow_n / total) * 50 + (small_n / total) * 20 + (fallback_n / total) * 30
        )
        score = max(0, min(100, score))

    ocr_pages = sum(1 for pg in pages_layout if pg.get("ocr_used"))
    return {
        "summary": {
            "total_blocks": total,
            "total_pages": len(pages_layout),
            "ocr_pages": ocr_pages,
            "overflow_blocks": overflow_n,
            "small_font_blocks": small_n,
            "fallback_blocks": fallback_n,
            "quality_score": score,
        },
        "blocks": blocks_out,
    }
