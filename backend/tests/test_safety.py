"""Đợt 2: test sanitize key + mã lỗi an toàn (stdlib unittest, không cần mạng)."""

import unittest

from services.safety import sanitize_error, JobCancelledError, PdfTooLargeError


class SafetyTest(unittest.TestCase):
    def test_che_key_va_bearer(self):
        key = "AIzaSyDUMMYKEY123456"
        msg = f"call https://x?key={key} failed with Bearer {key}"
        out = sanitize_error(msg, [key])
        self.assertNotIn(key, out)
        self.assertIn("key=***", out)
        self.assertIn("Bearer ***", out)

    def test_cat_chuoi_dai(self):
        out = sanitize_error("x" * 500)
        self.assertLessEqual(len(out), 201)

    def test_loai_loi_ton_tai(self):
        self.assertTrue(issubclass(JobCancelledError, RuntimeError))
        self.assertTrue(issubclass(PdfTooLargeError, ValueError))


if __name__ == "__main__":
    unittest.main()
