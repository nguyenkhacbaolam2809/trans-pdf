interface Props {
  url: string | null;
  meta: string;
}

export function PreviewPanel({ url, meta }: Props) {
  if (!url) return null;
  return (
    <section
      aria-label="Bản xem trước"
      className="space-y-2 rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Bản xem trước</h2>
        {meta ? <p className="text-xs text-slate-600 dark:text-slate-400">{meta}</p> : null}
      </div>
      <div className="flex flex-wrap gap-2">
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          className="text-xs text-teal-800 underline underline-offset-4 hover:text-teal-600 dark:text-teal-200 dark:hover:text-teal-100"
        >
          Mở to trong tab mới
        </a>
      </div>
      <iframe
        title="Xem trước bản dịch 1 trang"
        src={url}
        className="h-96 w-full rounded-md border border-slate-300 bg-white dark:border-slate-700"
      />
    </section>
  );
}
