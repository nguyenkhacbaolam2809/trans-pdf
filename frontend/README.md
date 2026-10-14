# trans-pdf frontend

Next.js UI for trans-pdf: upload a PDF, follow progress, preview one page, review the layout QA report, fix texts, and download the result. See the repo root [`README.md`](../README.md) for the big picture.

## Setup

Requires Node 18+.

```bash
npm install
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > .env.local
npm run dev
```

Open `http://localhost:3000`. The backend must be running first (see [`backend/README.md`](../backend/README.md)).

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Local dev server |
| `npm run build` | Production build with type checking |
| `npm run start` | Serve the production build |
| `npm run lint` | Lint |

## How the UI is organized

The page is a three-step wizard:

1. **Pick a file.** Drag and drop, file picker, or paste from clipboard. Max 50MB, PDF only.
2. **Language and mode.** Three modes:
   - *Tracked translation* (recommended): queues a job, shows live progress over WebSocket with polling fallback.
   - *Quick translate*: one request, best for small files.
   - *Preview one page*: translates a single page so you can check quality cheaply.
3. **Result and fixes.** Download the PDF, check the QA report grouped by page, edit any block, and re-render without calling the LLM again.

There is also a light/dark toggle in the header. The choice is stored locally and defaults to the OS setting.

## Project layout

```text
frontend/
  app/
    page.tsx        thin page container, composes everything
    layout.tsx      root layout, theme init script
    globals.css     base styles, focus rings
  components/       one focused component per file
    FileDropzone, ModeTabs, LanguagePicker, JobProgress,
    ResultCard, PreviewPanel, QaEditor, StepsBar, ...
  hooks/            data and state (useTranslatePage, useJobRunner, ...)
  lib/
    api.ts          all backend calls in one place
    format.ts       file sizes, dates, URL helpers
  types.ts          shared types
```

UI state lives in `hooks/useTranslatePage.ts`. Components stay presentational: they receive props and render, they do not fetch.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend base URL |

## Notes

- The QA editor shows at most 120 blocks at a time. Use search and the "only issues" filter on long documents.
- If the WebSocket cannot connect, the UI falls back to polling the job status every 2 seconds.
