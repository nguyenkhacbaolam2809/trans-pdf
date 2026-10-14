"""Đợt 2: test cache namespace + không lưu mock (không cần mạng)."""

import asyncio
import os
import unittest


class CacheNamespaceTest(unittest.TestCase):
    def test_namespace_cach_ly(self):
        from services.translation_cache import TranslationCache

        async def run():
            c = TranslationCache(max_entries=50, ttl_sec=600)
            await c.set("Hello", "Auto", "Vietnamese", "Xin chào", namespace="ns-a")
            self.assertIsNone(
                await c.get("Hello", "Auto", "Vietnamese", namespace="ns-b")
            )
            self.assertEqual(
                await c.get("Hello", "Auto", "Vietnamese", namespace="ns-a"),
                "Xin chào",
            )

        asyncio.run(run())

    def test_khong_luu_mock(self):
        os.environ["ENV"] = "development"
        os.environ["ALLOW_MOCK_FALLBACK"] = "true"
        os.environ["CACHE_ENABLED"] = "true"
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)
        from services.llm_service import LLMService

        async def run():
            svc = LLMService()
            await svc.translate_blocks(
                [{"id": "b1", "text": "Hello world"}], "Vietnamese"
            )
            # Đợt 1: bản mock không được lưu vào cache để lần sau có key sẽ dịch thật.
            self.assertEqual(svc.cache_stats()["entries"], 0)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
