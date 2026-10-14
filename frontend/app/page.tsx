"use client";

import { Header } from "../components/Header";
import { ModeTabs } from "../components/ModeTabs";
import { LanguagePicker } from "../components/LanguagePicker";
import { FileDropzone } from "../components/FileDropzone";
import { JobProgress } from "../components/JobProgress";
import { ErrorBanner } from "../components/ErrorBanner";
import { ResultCard } from "../components/ResultCard";
import { PreviewPanel } from "../components/PreviewPanel";
import { QaEditor } from "../components/QaEditor";
import { FeatureList } from "../components/FeatureList";
import { StepsBar } from "../components/StepsBar";
import { PageInput } from "../components/PageInput";
import { useHealth } from "../hooks/useHealth";
import { useTranslatePage } from "../hooks/useTranslatePage";

export default function TransPDFPage() {
  const health = useHealth();
  const t = useTranslatePage();
  const busy = t.busy;
  const hasFile = t.file !== null;
  const hasResult = t.downloadUrl !== null || t.previewUrl !== null;

  const action =
    t.mode === "job"
      ? t.submitJob
      : t.mode === "sync"
        ? t.syncTranslate
        : t.runPreview;
  const actionLabel =
    t.mode === "preview" ? "Dịch thử trang này" : "Bắt đầu dịch";

  return (
    <div className="flex min-h-screen flex-col bg-slate-100 text-slate-900 dark:bg-slate-950 dark:text-slate-200">
      <Header health={health} />
      <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-5 px-4 py-6 sm:px-6 sm:py-8">
        <section className="max-w-2xl">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100 sm:text-3xl">
            Dịch PDF mà không vỡ bố cục
          </h1>
          <p className="mt-2 text-sm leading-relaxed text-slate-600 dark:text-slate-400">
            Tải bài báo hoặc tài liệu kỹ thuật lên. Bản dịch đặt vào đúng chỗ
            chữ cũ, ảnh vẫn ở đúng vị trí.
          </p>
        </section>

        <StepsBar hasFile={hasFile} hasResult={hasResult} />

        <section
          aria-label="Bước 1: chọn file"
          className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900 sm:p-5"
        >
          <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            1. Chọn file PDF
          </h2>
          <div className="mt-3">
            <FileDropzone
              file={t.file}
              jobId={t.jobId}
              disabled={busy}
              onPick={t.pickFile}
              onError={t.setError}
              onClear={t.resetAll}
            />
          </div>
        </section>

        {hasFile && (
          <section
            aria-label="Bước 2: ngôn ngữ và cách dịch"
            className="space-y-5 rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900 sm:p-5"
          >
            <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              2. Ngôn ngữ và cách dịch
            </h2>
            <ModeTabs mode={t.mode} onChange={t.setMode} disabled={busy} />
            <LanguagePicker
              value={t.targetLang}
              onChange={t.setTargetLang}
              disabled={busy}
            />

            {t.mode === "preview" && (
              <PageInput
                value={t.previewPage}
                onChange={t.setPreviewPage}
                disabled={busy}
              />
            )}

            <ErrorBanner message={t.error} />

            <button
              type="button"
              disabled={!t.file || busy}
              onClick={action}
              className="w-full rounded-md bg-teal-700 py-3 text-sm font-semibold text-white hover:bg-teal-600 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-500 dark:bg-teal-300 dark:text-slate-950 dark:hover:bg-teal-200 dark:disabled:bg-slate-800 dark:disabled:text-slate-500"
            >
              {busy ? "Đang xử lý..." : actionLabel}
            </button>

            {(busy || t.runner.progress > 0) && t.mode !== "preview" && (
              <JobProgress
                progress={t.runner.progress}
                step={t.runner.step}
                detail={t.runner.detail}
                elapsed={t.elapsed}
                showCancel={busy && t.mode === "job"}
                onCancel={t.cancelJob}
              />
            )}
          </section>
        )}

        {hasResult && (
          <section aria-label="Bước 3: kết quả" className="space-y-4">
            <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              3. Kết quả và sửa lỗi
            </h2>
            {t.downloadUrl && (
              <ResultCard
                url={t.downloadUrl}
                fileName={t.downloadName}
                onReset={t.resetAll}
              />
            )}
            <PreviewPanel url={t.previewUrl} meta={t.previewMeta} />
            <QaEditor
              blocks={t.qaBlocks}
              summary={t.qaSummary}
              saving={t.editBusy}
              note={t.editNote}
              onSave={t.saveEdits}
            />
          </section>
        )}
        <FeatureList />
      </main>
      <footer className="border-t border-slate-200 py-4 text-center text-xs text-slate-600 dark:border-slate-800 dark:text-slate-500">
        trans-pdf · luôn kiểm tra số liệu và công thức trước khi dùng
      </footer>
    </div>
  );
}
