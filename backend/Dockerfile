# Đợt 2: image chạy backend trans-pdf (single instance).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ENV=production

WORKDIR /app

# Tesseract để OCR hybrid chạy thật + font Unicode để không lỗi tofu.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    fonts-noto \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .env.example ./
COPY services/ ./services/
COPY fonts/ ./fonts/

EXPOSE 8000

# Lưu ý: truyền GEMINI_API_KEY/OPENAI_API_KEY và FRONTEND_ORIGINS qua env lúc chạy.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
