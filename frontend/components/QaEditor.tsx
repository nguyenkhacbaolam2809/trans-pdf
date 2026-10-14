"use client";

import { useMemo, useState } from "react";
import type { QaBlock, QaSummary } from "../types";

interface Props {
  blocks: QaBlock[];
  summary: QaSummary | null;
  saving: boolean;
  note: string;
  onSave: (edits: Record<string, string>) => void;
}

const ISSUE_LABELS: Record<string, string> = {
  overflow: "tràn khung",
  small_font: "chữ nhỏ",
  expansion: "dài hơn gốc nhiều",
  fallback_to_source: "giữ nguyên gốc",
};

function issueLabel(issue: string): string {
  return ISSUE_LABELS[issue] || issue;
}

function severity(block: QaBlock): number {
  if (block.issues.includes("overflow")) return 0;
  if (block.issues.length > 0) return 1;
  return 2;
}

export function QaEditor({ blocks, summary, saving, note, onSave }: Props) {
  const [onlyIssues, setOnlyIssues] = useState(true);
  const [query, setQuery] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return blocks
      .filter((b) => (onlyIssues ? b.issues.length > 0 : true))
      .filter((b) => (q ? b.final_text.toLowerCase().includes(q) : true))
      .sort((a, b) => severity(a) - severity(b) || a.page - b.page);
  }, [blocks, onlyIssues, query]);

  const groups = useMemo(() => {
    const map = new Map<number, QaBlock[]>();
    for (const b of filtered.slice(0, 120)) {
      const list = map.get(b.page) || [];
      list.push(b);
      map.set(b.page, list);
    }
    return Array.from(map.entries()).sort((a, b) => a[0] - b[0]);
  }, [filtered]);

  if (blocks.length === 0) return null;
  const issueCount = blocks.filter((b) => b.issues.length > 0).length;

  return (
    <section
      aria-label="Kiểm tra bố cục"
      className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900"
    >
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            Kiểm tra bố cục
          </h2>
          <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
            {issueCount}/{blocks.length} đoạn cần xem
            {typeof summary?.quality_score === "number"
              ? ` · điểm ${summary.quality_score}/100`
              : ""}
            {typeof summary?.ocr_pages === "number" && summary.ocr_pages > 0
              ? ` · ${summary.ocr_pages} trang OCR`
              : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="qa-search" className="sr-only">
            Tìm theo nội dung
          </label>
          <input
            id="qa-search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Tìm theo nội dung..."
            className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
          />
          <button
            type="button"
            aria-pressed={onlyIssues}
            onClick={() => setOnlyIssues((v) => !v)}
            className={`rounded-md border px-2 py-1.5 text-xs ${
              onlyIssues
                ? "border-teal-700 bg-teal-50 text-teal-900 dark:border-teal-300 dark:bg-teal-300/10 dark:text-teal-200"
                : "border-slate-300 text-slate-600 dark:border-slate-700 dark:text-slate-300"
            }`}
          >
            {onlyIssues ? "Đang lọc lỗi" : "Hiện tất cả"}
          </button>
        </div>
      </div>

      {groups.length === 0 ? (
        <p role="status" className="py-6 text-center text-sm text-slate-500">
          {onlyIssues
            ? "Không còn đoạn lỗi. Tắt lọc để xem toàn bộ."
            : "Không còn đoạn nào khớp tìm kiếm."}
        </p>
      ) : (
        <div className="mt-3 space-y-3">
          {groups.map(([page, items]) => (
            <details
              key={page}
              open={page === groups[0][0]}
              className="overflow-hidden rounded-md border border-slate-200 dark:border-slate-800"
            >
              <summary className="cursor-pointer bg-slate-50 px-3 py-2 text-xs font-medium text-slate-700 dark:bg-slate-950 dark:text-slate-300">
                Trang {page + 1} · {items.length} đoạn
                {items.some((b) => b.issues.includes("overflow"))
                  ? " · có tràn khung"
                  : ""}
              </summary>
              <ul className="max-h-80 space-y-2 overflow-auto p-2">
                {items.map((b) => (
                  <li
                    key={b.id}
                    className={`rounded-md border p-3 ${
                      b.issues.includes("overflow")
                        ? "border-red-300 bg-red-50 dark:border-red-800 dark:bg-red-950/40"
                        : b.issues.length > 0
                          ? "border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/30"
                          : "border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950"
                    }`}
                  >
                    <div className="flex flex-wrap items-center gap-1.5 text-xs">
                      <span className="rounded bg-slate-200 px-1.5 py-0.5 font-mono text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                        {b.id}
                      </span>
                      {b.source === "ocr" && (
                        <span className="rounded bg-slate-200 px-1.5 py-0.5 text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                          OCR
                        </span>
                      )}
                      {b.issues.map((issue) => (
                        <span
                          key={issue}
                          className="rounded bg-white px-1.5 py-0.5 text-slate-700 ring-1 ring-inset ring-slate-300 dark:bg-transparent dark:text-amber-200 dark:ring-amber-800"
                        >
                          {issueLabel(issue)}
                        </span>
                      ))}
                      {b.issues.length === 0 && (
                        <span className="text-slate-500">Ổn</span>
                      )}
                    </div>
                    <p className="mt-1 line-clamp-2 text-xs text-slate-600 dark:text-slate-400">
                      {b.final_text}
                    </p>
                    <label
                      htmlFor={`edit-${b.id}`}
                      className="mt-2 block text-xs text-slate-600 dark:text-slate-500"
                    >
                      Sửa đoạn {b.id}
                    </label>
                    <textarea
                      id={`edit-${b.id}`}
                      rows={2}
                      defaultValue={b.final_text}
                      onChange={(e) =>
                        setDrafts((prev) => ({
                          ...prev,
                          [b.id]: e.target.value,
                        }))
                      }
                      className="mt-1 w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                    />
                  </li>
                ))}
              </ul>
            </details>
          ))}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={saving || Object.keys(drafts).length === 0}
          onClick={() => {
            const changed: Record<string, string> = {};
            for (const [k, v] of Object.entries(drafts)) {
              if (v.trim()) changed[k] = v.trim();
            }
            onSave(changed);
            setDrafts({});
          }}
          className="rounded-md bg-teal-700 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-600 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-teal-300 dark:text-slate-950 dark:hover:bg-teal-200"
        >
          {saving
            ? "Đang vẽ lại..."
            : `Lưu ${Object.keys(drafts).length} chỉnh sửa`}
        </button>
        {note ? (
          <p role="status" className="text-xs text-slate-600 dark:text-slate-400">
            {note}
          </p>
        ) : null}
      </div>
    </section>
  );
}
