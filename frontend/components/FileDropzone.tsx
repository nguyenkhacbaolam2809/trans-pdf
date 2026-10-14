"use client";

import { useEffect, useRef, useState } from "react";
import { formatSize } from "../lib/format";
import { MAX_UPLOAD_BYTES } from "../types";

interface Props {
  file: File | null;
  jobId: string | null;
  disabled: boolean;
  onPick: (file: File) => void;
  onError: (message: string) => void;
  onClear: () => void;
}

export function FileDropzone({
  file,
  jobId,
  disabled,
  onPick,
  onError,
  onClear,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  function validate(f: File): boolean {
    if (!f.name.toLowerCase().endsWith(".pdf")) {
      onError("File cần có đuôi .pdf.");
      return false;
    }
    if (f.size > MAX_UPLOAD_BYTES) {
      onError("File quá 50MB, bạn chia nhỏ file rồi thử lại.");
      return false;
    }
    return true;
  }

  useEffect(() => {
    if (file) return;
    const onPaste = (e: ClipboardEvent) => {
      const f = e.clipboardData?.files?.[0];
      if (f && validate(f)) onPick(f);
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  });

  if (file) {
    const tooBig = file.size > MAX_UPLOAD_BYTES;
    return (
      <div
        role="status"
        className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900"
      >
        <div className="flex min-w-0 items-center gap-3">
          <span
            aria-hidden="true"
            className="flex h-11 w-11 shrink-0 flex-col items-center justify-center rounded-md bg-teal-700 text-white dark:bg-teal-300 dark:text-slate-950"
          >
            <span className="text-xs font-bold leading-none">PDF</span>
            <span className="text-xs leading-none opacity-80">
              {formatSize(file.size).split(" ")[1] || ""}
            </span>
          </span>
          <span className="min-w-0">
            <span className="block truncate text-sm font-medium text-slate-900 dark:text-slate-100">
              {file.name}
            </span>
            <span className="block text-xs text-slate-600 dark:text-slate-400">
              {formatSize(file.size)}
              {tooBig ? " · quá giới hạn 50MB" : " · đã sẵn sàng"}
              {jobId ? ` · job ${jobId}` : ""}
            </span>
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {!disabled && (
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              className="rounded-md border border-slate-300 px-2 py-2 text-xs text-slate-700 hover:bg-slate-200 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              Đổi file
            </button>
          )}
          {!disabled && (
            <button
              type="button"
              onClick={onClear}
              aria-label={`Gỡ file ${file.name}`}
              className="rounded-md px-2 py-2 text-xs text-slate-600 hover:bg-slate-200 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-200"
            >
              Gỡ
            </button>
          )}
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,application/pdf"
          className="sr-only"
          aria-label="Đổi file PDF khác"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f && validate(f)) onPick(f);
            e.target.value = "";
          }}
        />
      </div>
    );
  }

  return (
    <div>
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,application/pdf"
        className="sr-only"
        aria-label="Chọn file PDF"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f && validate(f)) onPick(f);
          e.target.value = "";
        }}
      />
      <div
        onDragEnter={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={(e) => {
          e.preventDefault();
          setDragging(false);
        }}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const f = e.dataTransfer.files?.[0];
          if (f && validate(f)) onPick(f);
        }}
        className={`rounded-lg border border-dashed px-6 py-7 text-center transition ${
          dragging
            ? "border-teal-700 bg-teal-50 dark:border-teal-300 dark:bg-teal-300/10"
            : "border-slate-300 bg-slate-50 hover:border-slate-400 dark:border-slate-700 dark:bg-slate-900 dark:hover:border-slate-500"
        }`}
      >
        <p
          aria-hidden="true"
          className="mx-auto flex h-10 w-10 items-center justify-center rounded-md bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
            />
          </svg>
        </p>
        <p className="mt-2 text-sm font-medium text-slate-900 dark:text-slate-100">
          {dragging ? "Thả file ra để chọn" : "Kéo file PDF vào đây"}
        </p>
        <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
          Tối đa 50MB. File scan vẫn dịch được nhờ OCR. Bạn cũng có thể dán
          file từ clipboard.
        </p>
        <div className="mt-3 flex items-center justify-center gap-2">
          <button
            type="button"
            disabled={disabled}
            onClick={() => inputRef.current?.click()}
            className="rounded-md bg-teal-700 px-3 py-2 text-sm font-medium text-white hover:bg-teal-600 disabled:opacity-50 dark:bg-teal-300 dark:text-slate-950 dark:hover:bg-teal-200"
          >
            Chọn từ máy
          </button>
          <span className="text-xs text-slate-500 dark:text-slate-500">
            hoặc dán (Ctrl+V)
          </span>
        </div>
      </div>
    </div>
  );
}
