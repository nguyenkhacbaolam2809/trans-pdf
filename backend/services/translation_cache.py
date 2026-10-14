"""
services/translation_cache.py
============================
FIX P2: Cache bản dịch cấp block để:
- Preview 1 trang nhanh (lần 2 không tốn LLM).
- Job retry / edit không dịch lại những câu đã dịch.
- Tiết kiệm token khi nhiều user dịch cùng tài liệu mẫu.

Key = sha256(normalized_text | source_lang | target_lang).
LRU + TTL, thread-safe cho asyncio (dùng Lock).
Không cần Redis — in-memory đủ cho 1 instance; muốn scale đa instance
thì thay backend này bằng Redis mà không đổi interface.
"""

import asyncio
import hashlib
import logging
import os
import time
from collections import OrderedDict
from typing import Dict, Optional, Tuple

logger = logging.getLogger("trans_pdf.cache")


def _norm_text(s: str) -> str:
    # FIX P2: chuẩn hoá khoảng trắng để "Hello  world" và "Hello world" trúng cache chung.
    return " ".join((s or "").strip().split())


def make_cache_key(text: str, source_lang: str, target_lang: str, namespace: str = "") -> str:
    norm = _norm_text(text)
    raw = f"{namespace}\x00{norm}\x00{source_lang}\x00{target_lang}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TranslationCache:
    """LRU + TTL cache đơn giản, an toàn cho asyncio."""

    def __init__(self, max_entries: int = 2000, ttl_sec: int = 86400):
        self.max_entries = max(1, max_entries)
        self.ttl_sec = max(60, ttl_sec)
        self._store: "OrderedDict[str, Tuple[str, float]]" = OrderedDict()
        self._lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0

    @classmethod
    def from_env(cls) -> "TranslationCache":
        def _int(name: str, default: int) -> int:
            try:
                return max(1, int(os.getenv(name, str(default))))
            except (TypeError, ValueError):
                return default

        return cls(
            max_entries=_int("CACHE_MAX_ENTRIES", 2000),
            ttl_sec=_int("CACHE_TTL_SEC", 86400),
        )

    def _is_expired(self, ts: float) -> bool:
        return (time.monotonic() - ts) > self.ttl_sec

    async def get(self, text: str, source_lang: str, target_lang: str, namespace: str = "") -> Optional[str]:
        key = make_cache_key(text, source_lang, target_lang, namespace)
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            value, ts = entry
            if self._is_expired(ts):
                try:
                    del self._store[key]
                except KeyError:
                    pass
                self.misses += 1
                return None
            # LRU bump
            self._store.move_to_end(key)
            self.hits += 1
            return value

    async def set(self, text: str, source_lang: str, target_lang: str, translated: str, namespace: str = "") -> None:
        if not text or not isinstance(translated, str):
            return
        key = make_cache_key(text, source_lang, target_lang, namespace)
        async with self._lock:
            self._store[key] = (translated, time.monotonic())
            self._store.move_to_end(key)
            # Evict cũ nhất khi vượt容量
            while len(self._store) > self.max_entries:
                old_key, _ = self._store.popitem(last=False)
                logger.debug("[FIX P2] Cache evict key %s", old_key[:8])

    async def get_many(
        self, items: Dict[str, str], source_lang: str, target_lang: str, namespace: str = ""
    ) -> Tuple[Dict[str, str], Dict[str, str]]:
        """
        Chia items {block_id: masked_text} thành (cached, missing).
        Dùng ở đầu translate_blocks để bỏ qua LLM cho câu đã dịch.
        """
        cached: Dict[str, str] = {}
        missing: Dict[str, str] = {}
        for b_id, txt in items.items():
            hit = await self.get(txt, source_lang, target_lang, namespace)
            if hit is not None:
                cached[b_id] = hit
            else:
                missing[b_id] = txt
        return cached, missing

    async def put_many(
        self, mapping: Dict[str, str], originals: Dict[str, str],
        source_lang: str, target_lang: str, namespace: str = "",
    ) -> None:
        """Lưu kết quả LLM vào cache (chỉ lưu block dịch thành công, khác câu gốc hoặc giữ mask)."""
        for b_id, translated in mapping.items():
            orig = originals.get(b_id)
            if orig is None:
                continue
            await self.set(orig, source_lang, target_lang, translated, namespace)

    def stats(self) -> Dict[str, int]:
        total = self.hits + self.misses
        rate = round(self.hits / total, 3) if total else 0.0
        return {
            "entries": len(self._store),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": rate,
            "max_entries": self.max_entries,
            "ttl_sec": self.ttl_sec,
        }

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()
            self.hits = 0
            self.misses = 0
