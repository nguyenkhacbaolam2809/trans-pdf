"""Đợt C: hồi quy health/500/lock/cache/evict (không cần mạng/key thật)."""

import io
import time
import unittest


class HealthAndErrorsTest(unittest.TestCase):
    def test_health_basc_thieu_path_va_dem_processing(self):
        # Đợt C: health chỉ lộ tên file font, và đếm cả job processing.
        import os
        os.environ.setdefault("ENV", "development")
        from fastapi.testclient import TestClient
        import main as app_main
        from services.job_queue import Job, STATUS_PROCESSING

        with TestClient(app_main.app, raise_server_exceptions=False) as client:
            r = client.get("/api/health")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            # Không lộ đường dẫn tuyệt đối (không chứa :/ hoặc :\).
            self.assertNotIn(":/", body["font_path"])
            self.assertNotIn(":\\", body["font_path"])
            base = body["concurrency"]["active_translations"]
            fake = Job(id="healthprobe", filename="x.pdf", target_lang="Vietnamese",
                       source_lang="Auto", status=STATUS_PROCESSING)
            app_main.job_manager.jobs["healthprobe"] = fake
            try:
                body2 = client.get("/api/health").json()
                self.assertEqual(
                    body2["concurrency"]["active_translations"], base + 1
                )
            finally:
                app_main.job_manager.jobs.pop("healthprobe", None)

    def test_500_co_request_id(self):
        # Đợt C: crash chưa được xử lý vẫn trả X-Request-ID + body chung.
        import os
        os.environ.setdefault("ENV", "development")
        os.environ.setdefault("ALLOW_MOCK_FALLBACK", "true")
        from fastapi.testclient import TestClient
        import main as app_main

        def make_pdf():
            import fitz
            doc = fitz.open()
            pg = doc.new_page(width=595, height=842)
            pg.insert_text((72, 100), "Hello", fontsize=12)
            data = doc.tobytes()
            doc.close()
            return data

        with TestClient(app_main.app, raise_server_exceptions=False) as client:
            r = client.post(
                "/api/jobs",
                files={"file": ("t.pdf", io.BytesIO(make_pdf()), "application/pdf")},
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
            # QA khi layout còn lành phải 200 (đọc fit_results là chính).
            qa = client.get(f"/api/jobs/{jid}/qa")
            self.assertEqual(qa.status_code, 200)
            # Làm hỏng layout để endpoint crash ở chỗ không có try.
            job = app_main.job_manager.get(jid)
            assert job is not None
            job.pages_layout = [{"page_index": 0, "blocks": None}]
            r = client.post(
                f"/api/jobs/{jid}/edit",
                json={"edits": {list(job.fit_results.keys())[0]: "sua"}})
            # pages_layout hỏng -> TypeError không được xử lý trong endpoint.
            self.assertEqual(r.status_code, 500)
            self.assertIn("x-request-id", {k.lower() for k in r.headers})
            self.assertIn("Mã tra cứu", r.json().get("detail", ""))
            client.delete(f"/api/jobs/{jid}")

    def test_download_status_theo_loai_loi(self):
        # Đợt C: 503 hết LLM, 413 quá khổ, 422 lỗi dữ liệu (không còn 500 chung).
        import os
        os.environ.setdefault("ENV", "development")
        from fastapi.testclient import TestClient
        import main as app_main
        from services.job_queue import Job, STATUS_ERROR

        with TestClient(app_main.app, raise_server_exceptions=False) as client:
            cases = [
                ("llm_unavailable", 503),
                ("too_large", 413),
                ("internal", 422),
                ("", 422),
            ]
            for code, want in cases:
                job = Job(id=f"fail-{code or 'none'}", filename="x.pdf",
                          target_lang="Vietnamese", source_lang="Auto",
                          status=STATUS_ERROR, error="Lỗi mẫu", error_code=code)
                app_main.job_manager.jobs[job.id] = job
                try:
                    r = client.get(f"/api/jobs/{job.id}/download")
                    self.assertEqual(r.status_code, want, code)
                finally:
                    app_main.job_manager.jobs.pop(job.id, None)


class EditLockTest(unittest.TestCase):
    def test_lock_rieng_tung_job(self):
        import asyncio
        from services.job_queue import JobManager

        async def run():
            jm = JobManager(translator=object())
            self.assertIs(jm.edit_lock("a"), jm.edit_lock("a"))
            self.assertIsNot(jm.edit_lock("a"), jm.edit_lock("b"))
            # acquire/release không kẹt
            await jm.edit_lock("a").acquire()
            jm.edit_lock("a").release()

        asyncio.run(run())


class NoCacheFallbackTest(unittest.TestCase):
    def test_khong_cache_cau_fallback(self):
        # Đợt C: LLM làm mất mask -> fallback giữ câu gốc -> không lưu cache,
        # lần sau vẫn thử LLM lại thay vì cố định câu hỏng.
        import asyncio
        import os
        os.environ["ENV"] = "development"
        os.environ["ALLOW_MOCK_FALLBACK"] = "false"
        os.environ["CACHE_ENABLED"] = "true"
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)
        from services.llm_service import LLMService

        async def run():
            svc = LLMService()
            # Giả provider trả thiếu mask cho câu có mask.
            async def bad_provider(user_content, items):
                return {it["id"]: "ban dich mat mask" for it in items}

            items = [{"id": "b1", "text": "Cong thuc [MATH_MASK_0] day"}]
            out = await svc._call_provider_with_repair(
                bad_provider, "{}", items, "Vietnamese", "Auto", provider_name="t")
            self.assertEqual(out["b1"], items[0]["text"])  # fallback câu gốc
            # Mô phỏng _store_to_cache: câu fallback không được lưu.
            from services.llm_service import MATH_MASK_RE
            good = {k: v for k, v in out.items()
                    if v != items[0]["text"] or not MATH_MASK_RE.search(items[0]["text"])}
            self.assertEqual(good, {})
            self.assertEqual(svc.cache_stats()["entries"], 0)

        asyncio.run(run())


class EvictTest(unittest.TestCase):
    def test_khong_xoa_job_pending(self):
        # Đợt C: đầy mà toàn pending thì báo đầy, không xóa job đang chờ.
        import asyncio
        from services.job_queue import JobManager, Job, STATUS_QUEUED, QueueFullError

        async def run():
            jm = JobManager(translator=object(), max_queue_size=10, max_jobs=2)
            for i in range(2):
                jm.jobs[f"q{i}"] = Job(id=f"q{i}", filename="x.pdf",
                                       target_lang="Vietnamese", source_lang="Auto",
                                       status=STATUS_QUEUED)
            with self.assertRaises(QueueFullError):
                await jm.submit(b"%PDF-1.4 fake", "n.pdf", "Vietnamese", "Auto")
            self.assertIn("q0", jm.jobs)
            self.assertIn("q1", jm.jobs)

        asyncio.run(run())


class FontAndIouTest(unittest.TestCase):
    def test_font_xau_bi_loai(self):
        from services.pdf_translator import _font_usable, _iou

        self.assertFalse(_font_usable(""))
        self.assertFalse(_font_usable("C:/khong/ton/tai.ttf"))
        self.assertFalse(_font_usable("C:/Windows/Fonts/Helvetica.ttc"))
        self.assertAlmostEqual(_iou([0, 0, 10, 10], [5, 5, 15, 15]), 25 / 175)
        self.assertEqual(_iou([0, 0, 10, 10], [20, 20, 30, 30]), 0.0)


class AdminTokenTest(unittest.TestCase):
    def test_clear_cache_can_token(self):
        # Đợt C: đặt ADMIN_TOKEN thì thiếu/sai token bị 403, đúng mới 200.
        import os
        os.environ["ADMIN_TOKEN"] = "secret-test"
        try:
            from fastapi.testclient import TestClient
            import main as app_main

            with TestClient(app_main.app, raise_server_exceptions=False) as client:
                self.assertEqual(client.post("/api/cache/clear").status_code, 403)
                r = client.post("/api/cache/clear",
                                headers={"X-Admin-Token": "sai"})
                self.assertEqual(r.status_code, 403)
                r = client.post("/api/cache/clear",
                                headers={"X-Admin-Token": "secret-test"})
                self.assertEqual(r.status_code, 200)
        finally:
            os.environ.pop("ADMIN_TOKEN", None)


if __name__ == "__main__":
    unittest.main()
