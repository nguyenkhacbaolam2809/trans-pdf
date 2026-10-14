# trans-pdf

Translate technical PDFs into another language while keeping the original layout: text stays where it was, images and diagrams stay in place, math formulas come back intact.

## How it works

1. The backend reads each text block with its bounding box (PyMuPDF).
2. Math formulas are masked as `[MATH_MASK_n]` so the translator cannot mangle them.
3. An LLM translates the masked text in batches, one page at a time.
4. The old text is redacted (images are preserved) and the translation is drawn back into the same boxes, shrinking the font only when needed.
5. Scanned pages with no text layer go through OCR when Tesseract is available; otherwise they pass through untouched.

## Repo layout

```text
trans-pdf/
  backend/    FastAPI service (translate, jobs, preview, QA, edit)
  frontend/   Next.js UI (upload, progress, preview, QA editor)
```

## Quickstart

You need Python 3.12+ and Node 18+.

**Backend:**

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
# put a real key in .env, otherwise dev mode returns mock translations
uvicorn main:app --host 127.0.0.1 --port 8000
```

Check `http://127.0.0.1:8000/api/health`.

**Frontend:**

```bash
cd frontend
npm install
# point at the backend
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > .env.local
npm run dev
```

Open `http://localhost:3000`.

**Docker (backend only):**

```bash
cd backend
docker build -t trans-pdf-backend .
docker run -p 8000:8000 -e GEMINI_API_KEY=... trans-pdf-backend
```

The image includes Tesseract and Noto fonts so OCR works out of the box.

## API overview

| Method | Path | What it does |
|---|---|---|
| GET | `/api/health` | Liveness, font, providers, queue and cache stats |
| POST | `/api/translate` | Translate a PDF synchronously, returns the PDF |
| POST | `/api/jobs` | Queue a translation, returns `job_id` (202) |
| GET | `/api/jobs/{id}` | Poll job status and progress |
| GET | `/api/jobs/{id}/download` | Download the finished PDF |
| DELETE | `/api/jobs/{id}` | Cancel a running job, or delete a finished one |
| GET | `/api/jobs/{id}/qa` | Per-block layout quality report |
| POST | `/api/jobs/{id}/edit` | Fix translated texts, re-render without calling the LLM |
| POST | `/api/preview` | Translate a single page (1-based `page` field) |
| POST | `/api/cache/clear` | Clear the translation cache |
| WS | `/ws/jobs/{id}` | Live progress; falls back to polling if unavailable |

Full details live in [`backend/README.md`](backend/README.md). Frontend notes live in [`frontend/README.md`](frontend/README.md).

## Configuration

All settings are environment variables, see [`backend/.env.example`](backend/.env.example). The important ones:

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | empty | Preferred LLM provider |
| `OPENAI_API_KEY` | empty | Fallback LLM provider |
| `ENV` | `development` | `production` disables mock translations entirely |
| `MAX_CONCURRENT_TRANSLATIONS` | `2` | Shared cap for sync requests and job workers |
| `MAX_PDF_PAGES` | `300` | Larger PDFs are rejected with 413 |
| `JOB_TIMEOUT_SEC` | `1800` | Max time per job |

## Testing

```bash
cd backend
python -m unittest discover -s tests -v
cd ../frontend
npm run build
```

## Things to know before going public

- Without an API key the backend returns clearly marked mock translations in dev. In production it returns 503 instead.
- There is no auth or rate limiting yet, so do not expose the API directly.
- Translated PDFs are larger than the originals because fonts get embedded.
- Scanned PDFs need Tesseract installed for OCR; without it those pages are skipped gracefully.
