"""Đợt 2: test trần trang + chia mẻ LLM + hủy job (dùng translator giả, không mạng)."""

import asyncio
import unittest

from services.safety import PdfTooLargeError, JobCancelledError
from services.job_queue import JobManager, STATUS_CANCELLED, STATUS_DONE


class FakeLLM:
    def __init__(self):
        self.batch_sizes: list[int] = []

    async def translate_blocks(self, blocks, target_lang="Vietnamese", source_lang="Auto"):
        self.batch_sizes.append(len(blocks))
        return {b["id"]: b["text"] for b in blocks}


def _make_pdf(num_pages: int = 2) -> bytes:
    import fitz

    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Hello page {i}", fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


class TranslatorCapsTest(unittest.TestCase):
    def test_vuot_tran_trang(self):
        from services.pdf_translator import PDFTranslator

        tr = PDFTranslator(llm_service=FakeLLM(), max_pages=1)
        pdf = _make_pdf(2)
        with self.assertRaises(PdfTooLargeError):
            asyncio.run(tr.translate_pdf_with_report(pdf))

    def test_chia_me_nho(self):
        from services.pdf_translator import PDFTranslator

        llm = FakeLLM()
        tr = PDFTranslator(llm_service=llm, max_blocks_per_call=1)
        pdf = _make_pdf(1)
        out, _ = asyncio.run(tr.translate_pdf_with_report(pdf))
        self.assertTrue(out.startswith(b"%PDF-"))
        # Mỗi mẻ tối đa 1 block nên số lần gọi >= số block của trang.
        self.assertTrue(all(n <= 1 for n in llm.batch_sizes))
        self.assertGreaterEqual(len(llm.batch_sizes), 1)


class JobCancelTest(unittest.TestCase):
    def test_huy_that(self):
        from services.pdf_translator import PDFTranslator

        async def run():
            tr = PDFTranslator(llm_service=FakeLLM())
            jm = JobManager(translator=tr, max_workers=1, ttl_sec=300, max_jobs=10)
            await jm.start()
            try:
                job = await jm.submit(_make_pdf(3), "f.pdf", "Vietnamese", "Auto")
                # Đặt cờ hủy ngay khi job còn xếp hàng/đang chạy.
                await jm.cancel(job.id)
                for _ in range(200):
                    await asyncio.sleep(0.05)
                    cur = jm.get(job.id)
                    if cur is not None and cur.status in (STATUS_CANCELLED, STATUS_DONE):
                        break
                cur = jm.get(job.id)
                self.assertIsNotNone(cur)
                assert cur is not None
                self.assertEqual(cur.status, STATUS_CANCELLED)
            finally:
                await jm.stop()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
