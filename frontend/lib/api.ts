import { fileNameFromDisposition } from "./format";
import type { JobState, QaBlock, QaSummary } from "../types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function readError(res: Response): Promise<string> {
  const j = await res.json().catch(() => ({}));
  const d = (j as { detail?: string }).detail;
  return d || `Lỗi HTTP ${res.status}`;
}

export async function fetchHealthText(): Promise<string> {
  const res = await fetch(`${API_BASE}/api/health`);
  if (!res.ok) throw new Error(await readError(res));
  const j = await res.json();
  const queue =
    j?.jobs != null ? `, hàng đợi ${j.jobs.queued}/${j.jobs.max_queue}` : "";
  const cache =
    j?.cache != null
      ? `, cache trúng ${Math.round((j.cache.hit_rate || 0) * 100)}%`
      : "";
  return `Máy chủ sẵn sàng (${j?.env || "dev"}${queue}${cache})`;
}

export async function createJob(
  file: File,
  targetLang: string
): Promise<{ job_id: string }> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("target_lang", targetLang);
  fd.append("source_lang", "Auto");
  const res = await fetch(`${API_BASE}/api/jobs`, { method: "POST", body: fd });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function fetchJob(jobId: string): Promise<JobState> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}`);
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function deleteJob(jobId: string): Promise<void> {
  await fetch(`${API_BASE}/api/jobs/${jobId}`, { method: "DELETE" }).catch(
    () => undefined
  );
}

export async function downloadJobBlob(
  jobId: string
): Promise<{ blob: Blob; fileName: string }> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}/download`);
  if (!res.ok) throw new Error(await readError(res));
  const blob = await res.blob();
  const fileName = fileNameFromDisposition(
    res.headers.get("Content-Disposition"),
    `${jobId}.pdf`
  );
  return { blob, fileName };
}

export async function fetchJobQa(
  jobId: string
): Promise<{ summary: QaSummary | null; blocks: QaBlock[] }> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}/qa`);
  if (!res.ok) throw new Error(await readError(res));
  const j = await res.json();
  return {
    summary: (j.summary || null) as QaSummary | null,
    blocks: (Array.isArray(j.blocks) ? j.blocks : []) as QaBlock[],
  };
}

export async function submitJobEdits(
  jobId: string,
  edits: Record<string, string>
): Promise<{ edited_blocks: number; qa_summary: QaSummary }> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}/edit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ edits }),
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(j.detail || `Lỗi HTTP ${res.status}`);
  return j;
}

export async function syncTranslateBlob(
  file: File,
  targetLang: string
): Promise<{ blob: Blob; fileName: string }> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("target_lang", targetLang);
  fd.append("source_lang", "Auto");
  const res = await fetch(`${API_BASE}/api/translate`, {
    method: "POST",
    body: fd,
  });
  if (!res.ok) throw new Error(await readError(res));
  const blob = await res.blob();
  const fileName = fileNameFromDisposition(
    res.headers.get("Content-Disposition"),
    `[${targetLang.slice(0, 2).toUpperCase()}]_${file.name}`
  );
  return { blob, fileName };
}

export async function previewPageBlob(
  file: File,
  targetLang: string,
  page: number
): Promise<{ blob: Blob; meta: string }> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("target_lang", targetLang);
  fd.append("source_lang", "Auto");
  fd.append("page", String(page));
  const res = await fetch(`${API_BASE}/api/preview`, {
    method: "POST",
    body: fd,
  });
  if (!res.ok) throw new Error(await readError(res));
  const blob = await res.blob();
  const score = res.headers.get("X-QA-Score") || "";
  const overflow = res.headers.get("X-QA-Overflow") || "";
  const ocr = res.headers.get("X-OCR-Used") || "";
  const parts = [`Trang ${page}`];
  if (score) parts.push(`điểm bố cục ${score}/100`);
  if (overflow) parts.push(`${overflow} đoạn tràn khung`);
  if (ocr === "True") parts.push("có dùng OCR");
  return { blob, meta: parts.join(", ") };
}
