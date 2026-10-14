# trans-pdf backend

FastAPI service that translates PDFs while preserving layout. See the repo root [`README.md`](../README.md) for the big picture.

## Setup

Requires Python 3.12+.

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env and add at least one key:
#   GEMINI_API_KEY=...  (preferred)
#   OPENAI_API_KEY=...  (fallback)
uvicorn main:app --host 127.0.0.1 --port 8000
```

Health check: `GET http://127.0.0.1:8000/api/health`.

Without any key the service runs in dev mock mode and marks translations accordingly. With `ENV=production` mock mode is disabled and untranslatable requests return 503.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | empty | Gemini provider key |
| `OPENAI_API_KEY` | empty | OpenAI provider key |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model name |
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI model name |
| `ENV` | `development` | `production` turns off all mocks |
| `ALLOW_MOCK_FALLBACK` | `true` | Dev-only mock when no key is set |
| `FRONTEND_ORIGINS` | `http://localhost:3000` | Allowed CORS origins |
| `MAX_CONCURRENT_TRANSLATIONS` | `2` | Shared cap for sync requests and job workers |
| `TRANSLATE_ACQUIRE_TIMEOUT_SEC` | `30` | Wait for a slot before returning 429 |
| `MAX_PDF_PAGES` | `300` | Page cap, larger files get 413 |
| `MAX_BLOCKS_PER_CALL` | `60` | Blocks per LLM request |
| `JOB_MAX_QUEUE` | `20` | Max queued jobs |
| `JOB_MAX_JOBS` | `20` | Max stored jobs |
| `JOB_TTL_SEC` | `1800` | Job retention |
| `JOB_TIMEOUT_SEC` | `1800` | Max time per job |
| `EDIT_TIMEOUT_SEC` | `120` | Max time for edit re-render |
| `ENABLE_OCR` | `true` | OCR fallback for scanned pages |
| `OCR_DPI` / `OCR_MIN_CONF` / `OCR_LANG` | `200` / `30` / `eng` | OCR tuning |
| `CACHE_ENABLED` | `true` | Block-level translation cache |
| `CACHE_MAX_ENTRIES` / `CACHE_TTL_SEC` | `2000` / `86400` | Cache size and TTL |
| `OCR_SPARSE_CHARS` | `50` | Pages with less text than this (and images) also get OCR |
| `ADMIN_TOKEN` | empty | If set, `POST /api/cache/clear` needs `X-Admin-Token` header |

## Endpoints

Sync translate:

```bash
curl -X POST http://127.0.0.1:8000/api/translate \
  -F "file=@paper.pdf" -F "target_lang=Vietnamese" -F "source_lang=Auto" \
  -o translated.pdf
```

Async job flow:

```bash
# 1. queue (returns job_id)
curl -X POST http://127.0.0.1:8000/api/jobs \
  -F "file=@paper.pdf" -F "target_lang=Vietnamese"

# 2. poll
curl http://127.0.0.1:8000/api/jobs/<job_id>

# 3. download / QA / edit / cancel
curl http://127.0.0.1:8000/api/jobs/<job_id>/download -o out.pdf
curl http://127.0.0.1:8000/api/jobs/<job_id>/qa
curl -X POST http://127.0.0.1:8000/api/jobs/<job_id>/edit \
  -H "Content-Type: application/json" \
  -d '{"edits": {"p0_b3": "fixed text"}}'
curl -X DELETE http://127.0.0.1:8000/api/jobs/<job_id>
```

Single page preview (page is 1-based):

```bash
curl -X POST http://127.0.0.1:8000/api/preview \
  -F "file=@paper.pdf" -F "target_lang=Vietnamese" -F "page=1" \
  -o preview.pdf
```

WebSocket progress: connect to `/ws/jobs/<job_id>`. You get the current snapshot first, then updates as `{job_id, status, progress, step, detail}`. A heartbeat with the same shape plus `"type": "heartbeat"` keeps idle connections alive (safe to ignore if you key off `status`). Polling `GET /api/jobs/<id>` works as a fallback.

`DELETE /api/jobs/<id>` returns `{ok, job_id, action, status}` where `action` is `cancelled` (was running, stops after the current page) or `deleted` (already finished). Failed downloads use 503 (LLM down), 413 (too large) or 422 (bad input) instead of a bare 500. Password-protected PDFs are rejected with 400. `only_pages` in edit calls is 0-based; `page` in preview calls is 1-based.

Supported `target_lang` values: Vietnamese, English, Japanese, Korean, Chinese, French, German, Spanish.

## Project layout

```text
backend/
  main.py                 routes, concurrency cap, request IDs, error mapping
  services/
    pdf_translator.py     layout extraction, redaction, redraw, preview, edit
    llm_service.py        provider calls, math-mask validation and retry
    job_queue.py          background workers, progress broadcast, TTL cleanup
    ocr_service.py        scanned-page OCR (graceful when Tesseract is missing)
    translation_cache.py  block-level LRU cache with model/prompt namespace
    layout_qa.py          overflow and font-size quality report
    safety.py             secret redaction, request IDs, error types
  tests/                  unittest suite, no network needed
  Dockerfile              production image with Tesseract and Noto fonts
```

## Tests

```bash
python -m unittest discover -s tests -v
```

## Docker

```bash
docker build -t trans-pdf-backend .
docker run -p 8000:8000 \
  -e GEMINI_API_KEY=... \
  -e FRONTEND_ORIGINS=https://your-frontend \
  trans-pdf-backend
```

## Notes

- Errors returned to clients are generic with a lookup code (`Mã tra cứu`). Details stay in server logs, and API keys are redacted from every log line.
- Sync endpoints and job workers share one concurrency semaphore, so peak memory stays bounded.
- Cancel takes effect after the current page finishes, not mid-sentence.
