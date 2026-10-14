"""Đợt B: hồi quy PDF mã hóa + validate only_pages (không cần mạng/key thật)."""

import io
import time
import unittest


def _make_pdf(num_pages=1):
    import fitz

    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Hello page {i}", fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def _make_encrypted_pdf():
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Secret content", fontsize=12)
    buf = io.BytesIO()
    doc.save(buf, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    data = buf.getvalue()
    doc.close()
    return data


class EncryptedPdfTest(unittest.TestCase):
    def test_pdf_ma_hoa_bi_tu_choi(self):
        # Đợt B: trước đây file mã hóa đi qua, báo done + điểm QA 100 giả.
        import asyncio
        from services.pdf_translator import PDFTranslator

        class FakeLLM:
            async def translate_blocks(self, blocks, target_lang="Vietnamese", source_lang="Auto"):
                return {b["id"]: b["text"] for b in blocks}

        tr = PDFTranslator(llm_service=FakeLLM())
        with self.assertRaises(ValueError) as ctx:
            asyncio.run(tr.translate_pdf_with_report(_make_encrypted_pdf()))
        self.assertIn("mật khẩu", str(ctx.exception))

    def test_preview_pdf_ma_hoa_bi_tu_choi(self):
        import asyncio
        from services.pdf_translator import PDFTranslator

        class FakeLLM:
            async def translate_blocks(self, blocks, target_lang="Vietnamese", source_lang="Auto"):
                return {b["id"]: b["text"] for b in blocks}

        tr = PDFTranslator(llm_service=FakeLLM())
        with self.assertRaises(ValueError):
            asyncio.run(tr.translate_single_page(_make_encrypted_pdf(), page_number=0))


class EditOnlyPagesTest(unittest.TestCase):
    def _done_job(self, client, pdf):
        r = client.post(
            "/api/jobs",
            files={"file": ("t.pdf", io.BytesIO(pdf), "application/pdf")},
            data={"target_lang": "Vietnamese", "source_lang": "Auto"},
        )
        self.assertEqual(r.status_code, 202)
        jid = r.json()["job_id"]
        for _ in range(120):
            time.sleep(0.25)
            st = client.get(f"/api/jobs/{jid}").json()
            if st["status"] in ("done", "error", "cancelled"):
                break
        self.assertEqual(st["status"], "done")
        return jid

    def test_only_pages_validation_va_rendered_pages(self):
        # Đợt B: bool/rỗng/ngoài khoảng phải 400; subset hợp lệ trả
        # rendered_pages + pending_edits để PDF và QA không lệch nhau.
        import os
        os.environ.setdefault("ENV", "development")
        os.environ.setdefault("ALLOW_MOCK_FALLBACK", "true")
        from fastapi.testclient import TestClient
        import main as app_main

        with TestClient(app_main.app) as client:
            pdf = _make_pdf(2)
            jid = self._done_job(client, pdf)
            qa = client.get(f"/api/jobs/{jid}/qa").json()
            self.assertGreater(len(qa["blocks"]), 0)
            page1_block = next(b["id"] for b in qa["blocks"] if b["page"] == 1)

            for bad in ([True], [], [9999]):
                r = client.post(
                    f"/api/jobs/{jid}/edit",
                    json={"edits": {page1_block: "sua"}, "only_pages": bad},
                )
                self.assertEqual(r.status_code, 400, bad)

            r = client.post(
                f"/api/jobs/{jid}/edit",
                json={"edits": {page1_block: "sua trang hai"}, "only_pages": [0]},
            )
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual(body["rendered_pages"], [0])
            # Edit thuộc trang 1 không được vẽ ở lần này -> pending, QA giữ cũ.
            self.assertIn(page1_block, body["pending_edits"])
            self.assertEqual(body["edited_blocks"], 0)
            qa2 = client.get(f"/api/jobs/{jid}/qa").json()
            blk = next(b for b in qa2["blocks"] if b["id"] == page1_block)
            self.assertNotEqual(blk["final_text"], "sua trang hai")


if __name__ == "__main__":
    unittest.main()
