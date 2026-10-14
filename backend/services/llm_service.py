"""
LLM Service Module: High-reliability Prompt Engineering & JSON Enforcer.
Ponytail Dev Note:
    - Zero-dependency resilient: uses httpx if installed, with stdlib urllib fallback.
    - Never trust raw LLM output to be pure JSON without fence stripping.
    - Validate that ALL [MATH_MASK_n] tokens present in input survive in output.
"""

import os
import json
import re
import asyncio
import logging
from typing import List, Dict, Any, Optional, Tuple
import urllib.request
import urllib.error

from .translation_cache import TranslationCache
from .safety import sanitize_error, ERR_LLM_UNAVAILABLE

logger = logging.getLogger("trans_pdf.llm")

# Đợt 1: model/prompt cố định version để key cache không lẫn bản cũ.
# Đổi model hay SYSTEM_PROMPT thì tăng PROMPT_VERSION để cache cũ tự rớt.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
PROMPT_VERSION = "v2-safety1"

# FIX P0: regex chuẩn để kiểm tra math token thật sự (không chỉ đếm chuỗi con).
MATH_MASK_RE = re.compile(r"\[MATH_MASK_\d+\]")

# System prompt optimized for Layout & Math preservation
SYSTEM_PROMPT = """You are a specialized Academic & Technical Document Translator.
Your job is to translate text blocks extracted from a PDF while strictly adhering to these rules:

1. PROTECTED TOKENS:
   - You will see placeholders formatted strictly as `[MATH_MASK_0]`, `[MATH_MASK_1]`, etc.
   - These represent mathematical formulas, LaTeX symbols, or special layout shields.
   - DO NOT translate, change, remove, duplicate, or re-order these tokens!
   - Keep each `[MATH_MASK_X]` EXACTLY as it appears in the input text block.
   - The COUNT and the exact IDs of these tokens in your output MUST equal the input.

2. CONCISENESS & NATURAL LANGUAGE:
   - Keep the translation natural and concise in the target language.
   - Do NOT be verbose. PDF bounding boxes are fixed; unnecessarily wordy translations risk shrinking font size too much.

3. UNTRANSLATABLE CONTENT:
   - Keep code snippets, URLs, citations (e.g., [1], [2]), author names, and LaTeX notations untouched.

4. STRICT OUTPUT FORMAT:
   - You MUST respond with ONLY a single valid JSON object.
   - NO markdown fences (no ```json or ```), NO commentary, NO preambles.
   - Format:
     {
       "translations": [
         { "id": "<same_block_id>", "text": "<translated_text_preserving_math_masks>" }
       ]
     }
"""

# FIX P0: prompt sửa lỗi khi LLM làm mất mask — dùng cho retry đúng 1 lần.
REPAIR_PROMPT_SUFFIX = (
    "\n\nCRITICAL REPAIR INSTRUCTION: Your previous output LOST or ALTERED "
    "[MATH_MASK_n] tokens. Retry ONLY the blocks listed below and copy every "
    "[MATH_MASK_n] token EXACTLY (same IDs, same count). If unsure, return the "
    "original text unchanged rather than dropping a mask."
)


class LLMService:
    def __init__(self, allow_mock: Optional[bool] = None, cache: Optional[TranslationCache] = None):
        self.gemini_api_key = os.getenv("GEMINI_API_KEY")
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        # FIX P2: cache dùng chung — preview/edit/job retry không tốn LLM lại.
        # Cho phép inject cache từ ngoài (tests, job manager) để thống kê tập trung.
        self.cache = cache or TranslationCache.from_env()
        # FIX P2: công tắc cache qua ENV (CACHE_ENABLED=false để debug/đo chất lượng thật).
        self._cache_enabled = os.getenv("CACHE_ENABLED", "true").strip().lower() in (
            "1", "true", "yes", "on",
        )
        # FIX P0: phát hiện production để CẤM mock fallback.
        # ENV=production|prod  => production. Mặc định development.
        self._env = os.getenv("ENV", "development").strip().lower()
        self._is_production = self._env in ("production", "prod")
        if allow_mock is None:
            if self._is_production:
                # FIX P0: production KHÔNG BAO GIỜ dùng mock, kể cả khi thiếu key.
                self._allow_mock = False
            else:
                # Dev/test: cho phép mock theo cờ, mặc định True để giữ DX cũ.
                self._allow_mock = os.getenv("ALLOW_MOCK_FALLBACK", "true").strip().lower() in (
                    "1", "true", "yes", "on",
                )
        else:
            # FIX P0: dù caller truyền allow_mock=True, production vẫn ép False.
            self._allow_mock = bool(allow_mock) and not self._is_production

    def _secrets(self) -> list[str]:
        return [s for s in (self.gemini_api_key, self.openai_api_key) if s]

    def _cache_namespace(self) -> str:
        # Đợt 1: key cache gắn model + prompt version để đổi prompt/model
        # không đọc nhầm bản dịch cũ.
        return f"{GEMINI_MODEL}|{OPENAI_MODEL}|{PROMPT_VERSION}"

    # ==================================================================
    # FIX P0: MATH TOKEN VALIDATION — kiểm tra thật sự số lượng + ID mask
    # ==================================================================
    @staticmethod
    def extract_masks(text: str) -> List[str]:
        """Trích xuất mọi token [MATH_MASK_n] theo đúng regex."""
        if not text:
            return []
        return MATH_MASK_RE.findall(text)

    @classmethod
    def masks_preserved(cls, original: str, translated: str) -> bool:
        """
        So sánh multiset mask gốc vs dịch.
        - sorted() cho phép LLM đổi thứ tự từ nhưng vẫn bắt buộc đủ số lượng + đúng ID.
        - Trả False nếu thiếu, thừa, sai ID, hoặc biến dạng token.
        """
        return sorted(cls.extract_masks(original)) == sorted(cls.extract_masks(translated))

    def enforce_math_masks(
        self,
        items: List[Dict[str, str]],
        result_map: Dict[str, str],
    ) -> Tuple[Dict[str, str], List[str]]:
        """
        FIX P0: rà soát từng block sau khi LLM trả về.
        - Block nào mất/sai mask => fallback giữ nguyên câu gốc (không để nát công thức).
        - Trả về (fixed_map, mismatched_ids để retry).
        """
        original_by_id = {item["id"]: item["text"] for item in items}
        fixed: Dict[str, str] = {}
        mismatched: List[str] = []
        for item in items:
            b_id = item["id"]
            original = original_by_id[b_id]
            translated = result_map.get(b_id, original)
            if not isinstance(translated, str) or not translated.strip():
                logger.warning(
                    "[MATH_MASK] Block %s trả về rỗng — fallback giữ câu gốc.", b_id
                )
                fixed[b_id] = original
                mismatched.append(b_id)
                continue
            if not self.masks_preserved(original, translated):
                logger.warning(
                    "[MATH_MASK] Block %s mất mask: gốc=%s | dịch=%s — fallback giữ câu gốc.",
                    b_id,
                    self.extract_masks(original),
                    self.extract_masks(translated),
                )
                fixed[b_id] = original
                mismatched.append(b_id)
            else:
                fixed[b_id] = translated
        return fixed, mismatched

    async def translate_blocks(
        self,
        blocks: List[Dict[str, Any]],
        target_lang: str = "Vietnamese",
        source_lang: str = "Auto"
    ) -> Dict[str, str]:
        """
        Translates a batch of text blocks while preserving [MATH_MASK_i] tokens.
        Returns a dict: {block_id: translated_text}

        FIX P0: production không còn mock fallback — thiếu key hoặc lỗi cả 2
        provider sẽ raise RuntimeError để API trả HTTP 503/500 tử tế.
        FIX P2: check TranslationCache trước khi gọi LLM; lưu kết quả sau khi dịch
        để preview/edit/job retry không tốn token lại.
        """
        if not blocks:
            return {}

        payload_items = [
            {"id": b["id"], "text": b["text"]}
            for b in blocks
            if b["text"].strip()
        ]

        if not payload_items:
            return {b["id"]: b["text"] for b in blocks}

        # FIX P2: tách câu đã có trong cache — chỉ gọi LLM cho câu thiếu.
        originals: Dict[str, str] = {it["id"]: it["text"] for it in payload_items}
        cached: Dict[str, str] = {}
        to_translate: List[Dict[str, str]] = payload_items
        namespace = self._cache_namespace()
        if self._cache_enabled:
            try:
                cached, missing = await self.cache.get_many(originals, source_lang, target_lang, namespace)
                if missing:
                    to_translate = [{"id": k, "text": v} for k, v in missing.items()]
                    logger.info(
                        "[FIX P2] Cache hit %d/%d blocks (%s->%s).",
                        len(cached), len(payload_items), source_lang, target_lang,
                    )
                else:
                    logger.info("[FIX P2] Cache hit toàn bộ %d blocks.", len(payload_items))
                    return {b["id"]: cached.get(b["id"], b["text"]) for b in blocks}
            except Exception as e:
                logger.warning("[FIX P2] Lỗi đọc cache (bỏ qua, gọi LLM): %s", e)
                cached = {}
                to_translate = payload_items

        async def _store_to_cache(mapping: Dict[str, str]) -> None:
            # Đợt 1: KHÔNG cache bản mock ở dev để tránh lẫn kết quả giả vào cache thật.
            # Đợt C: cũng KHÔNG cache block fallback (dịch == gốc mà gốc còn mask,
            # tức LLM làm mất mask). Cache nó là cố định câu hỏng vĩnh viễn —
            # lần sau trúng cache, không bao giờ thử LLM lại.
            if not self._cache_enabled or not mapping:
                return
            try:
                good = {
                    b_id: txt for b_id, txt in mapping.items()
                    if txt != originals.get(b_id) or not MATH_MASK_RE.search(originals.get(b_id, ""))
                }
                await self.cache.put_many(good, originals, source_lang, target_lang, namespace)
            except Exception as e:
                logger.warning("[FIX P2] Lỗi ghi cache (bỏ qua): %s", e)

        def _merge(fresh: Dict[str, str]) -> Dict[str, str]:
            merged = dict(cached)
            merged.update(fresh)
            # Bù block rỗng ban đầu bằng câu gốc.
            for b in blocks:
                if b["id"] not in merged:
                    merged[b["id"]] = b["text"]
            return merged

        base_payload = {
            "source_language": source_lang,
            "target_language": target_lang,
            "blocks_to_translate": to_translate,
        }
        user_content = json.dumps(base_payload, ensure_ascii=False, indent=2)

        provider_errors: List[str] = []

        # 1. Try Gemini if configured (kèm 1 lần retry sửa mask)
        if self.gemini_api_key:
            try:
                fresh = await self._call_provider_with_repair(
                    self._call_gemini, user_content, to_translate,
                    target_lang, source_lang, provider_name="gemini",
                )
                await _store_to_cache(fresh)
                return _merge(fresh)
            except Exception as e:
                # Đợt 1: che key trước khi gom lỗi/log — tránh lộ `key=...` ra ngoài.
                clean = sanitize_error(e, self._secrets())
                provider_errors.append(f"gemini: {clean}")
                logger.warning("[Ponytail Alert] Gemini translation failed: %s", clean)

        # 2. Try OpenAI if configured (kèm 1 lần retry sửa mask)
        if self.openai_api_key:
            try:
                fresh = await self._call_provider_with_repair(
                    self._call_openai, user_content, to_translate,
                    target_lang, source_lang, provider_name="openai",
                )
                await _store_to_cache(fresh)
                return _merge(fresh)
            except Exception as e:
                clean = sanitize_error(e, self._secrets())
                provider_errors.append(f"openai: {clean}")
                logger.warning("[Ponytail Alert] OpenAI translation failed: %s", clean)

        # 3. FIX P0: Mock chỉ cho dev khi được phép. Production raise lỗi rõ ràng.
        if self._allow_mock:
            logger.warning(
                "[DEV-MOCK] No active API key or all providers failed. "
                "Using deterministic mock (ENV=%s). Production sẽ KHÔNG đi nhánh này.",
                self._env,
            )
            mock_fresh = self._mock_translate(to_translate, target_lang)
            # Đợt 1: không lưu mock vào cache để lần sau có key thật sẽ dịch thật.
            return _merge(mock_fresh)

        # FIX P0: ném lỗi thân thiện để main.py map thành HTTP 503/500 cho Frontend.
        # Đợt 1: chi tiết provider chỉ ghi log server (đã che key), client nhận mã chung.
        if provider_errors:
            logger.error("LLM providers failed: %s", "; ".join(provider_errors))
        raise RuntimeError(
            f"Dịch thuật tạm thời không khả dụng [{ERR_LLM_UNAVAILABLE}]. "
            "Vui lòng thử lại sau hoặc liên hệ quản trị viên."
        )

    async def _call_provider_with_repair(
        self,
        provider_fn,
        user_content: str,
        items: List[Dict[str, str]],
        target_lang: str,
        source_lang: str,
        provider_name: str,
    ) -> Dict[str, str]:
        """
        FIX P0: gọi provider -> validate mask -> retry đúng 1 lần cho các block lỗi
        -> fallback câu gốc nếu vẫn lỗi. Không bao giờ để mất công thức lọt ra ngoài.
        """
        first_map = await provider_fn(user_content, items)
        fixed, mismatched = self.enforce_math_masks(items, first_map)
        if not mismatched:
            return fixed

        logger.warning(
            "[MATH_MASK] Provider %s làm mất mask ở %d block %s — retry 1 lần với repair prompt.",
            provider_name, len(mismatched), mismatched,
        )
        # Chỉ retry đúng các block lỗi để tiết kiệm token.
        retry_items = [it for it in items if it["id"] in mismatched]
        retry_payload = {
            "source_language": source_lang,
            "target_language": target_lang,
            "blocks_to_translate": retry_items,
            "repair_instruction": REPAIR_PROMPT_SUFFIX,
        }
        retry_content = json.dumps(retry_payload, ensure_ascii=False, indent=2)
        try:
            retry_map = await provider_fn(retry_content, retry_items)
        except Exception as e:
            logger.warning(
                "[MATH_MASK] Retry %s thất bại: %s — giữ fallback câu gốc cho %s.",
                provider_name, sanitize_error(e, self._secrets()), mismatched,
            )
            return fixed  # fixed đã fallback câu gốc cho mismatched

        # Merge retry rồi validate lần cuối, vẫn lỗi thì giữ câu gốc.
        merged = dict(fixed)
        for b_id in mismatched:
            if b_id in retry_map:
                orig = next(it["text"] for it in items if it["id"] == b_id)
                cand = retry_map[b_id]
                if isinstance(cand, str) and cand.strip() and self.masks_preserved(orig, cand):
                    merged[b_id] = cand
                    logger.info("[MATH_MASK] Retry %s phục hồi thành công block %s.", provider_name, b_id)
                else:
                    logger.warning(
                        "[MATH_MASK] Retry %s vẫn sai block %s — giữ nguyên câu gốc.", provider_name, b_id
                    )
        return merged

    async def _call_gemini(self, user_content: str, items: List[Dict[str, str]]) -> Dict[str, str]:
        """Call Gemini REST API with JSON response schema"""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={self.gemini_api_key}"
        payload = {
            "system_instruction": {
                "parts": [{"text": SYSTEM_PROMPT}]
            },
            "contents": [
                {
                    "parts": [{"text": user_content}]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.2
            }
        }

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        data = await asyncio.to_thread(self._request_json, req)
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
        return self._parse_json_result(raw_text, items)

    async def _call_openai(self, user_content: str, items: List[Dict[str, str]]) -> Dict[str, str]:
        """Call OpenAI GPT-4o-mini with json_object response format"""
        url = "https://api.openai.com/v1/chat/completions"
        payload = {
            "model": OPENAI_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2
        }

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={
                "Authorization": f"Bearer {self.openai_api_key}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        data = await asyncio.to_thread(self._request_json, req)
        raw_text = data["choices"][0]["message"]["content"]
        return self._parse_json_result(raw_text, items)

    @staticmethod
    def _request_json(req: urllib.request.Request) -> Dict[str, Any]:
        with urllib.request.urlopen(req, timeout=60.0) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _parse_json_result(self, raw_text: str, items: List[Dict[str, str]]) -> Dict[str, str]:
        """
        Cleans markdown wrappers and parses JSON strictly.
        Ensures all input block IDs are mapped.
        FIX P0: không tự ý bịa text — block thiếu sẽ được enforce_math_masks
        fallback về câu gốc ở tầng trên.
        """
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)

        parsed = json.loads(cleaned)
        translations = parsed.get("translations", [])

        expected_ids = {item["id"] for item in items}
        result_map: Dict[str, str] = {}
        for entry in translations:
            b_id = entry.get("id")
            b_text = entry.get("text", "")
            if b_id in expected_ids and isinstance(b_text, str) and b_id not in result_map:
                result_map[b_id] = b_text

        for item in items:
            if item["id"] not in result_map:
                result_map[item["id"]] = item["text"]

        return result_map

    def _mock_translate(self, items: List[Dict[str, str]], target_lang: str) -> Dict[str, str]:
        """
        Deterministic mock translation that retains all [MATH_MASK_n] tags.
        FIX P0: CHỈ dùng cho dev/test khi ALLOW_MOCK_FALLBACK=true và ENV!=production.
        Production không bao giờ gọi tới đây (đã raise ở translate_blocks).
        """
        result = {}
        for item in items:
            text = item["text"]
            # Mock translates text while rigorously preserving [MATH_MASK_n]
            result[item["id"]] = f"[{target_lang[:2].upper()}] {text}"
        return result

    # FIX P2: expose cache stats cho /api/health và debug.
    def cache_stats(self) -> Dict[str, Any]:
        try:
            return self.cache.stats()
        except Exception:
            return {"entries": 0, "hits": 0, "misses": 0, "hit_rate": 0.0}

    async def clear_cache(self) -> None:
        await self.cache.clear()
