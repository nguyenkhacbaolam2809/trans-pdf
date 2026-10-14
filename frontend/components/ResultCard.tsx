interface Props {
  url: string;
  fileName: string;
  onReset: () => void;
}

export function ResultCard({ url, fileName, onReset }: Props) {
  return (
    <section
      aria-label="Kết quả"
      className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900"
    >
      <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Đã có bản dịch</h2>
      <p className="mt-1 text-xs leading-relaxed text-slate-600 dark:text-slate-400">
        Ảnh, bảng và công thức giữ ở vị trí cũ. Bạn mở file ra soát một lượt
        trước khi dùng, nhất là số liệu.
      </p>
      <div className="mt-3 flex flex-col gap-2 sm:flex-row">
        <a
          href={url}
          download={fileName}
          className="flex-1 rounded-md bg-teal-700 px-4 py-2.5 text-center text-sm font-semibold text-white hover:bg-teal-600 dark:bg-teal-300 dark:text-slate-950 dark:hover:bg-teal-200"
        >
          Tải bản dịch{fileName ? ` (${fileName})` : ""}
        </a>
        <button
          type="button"
          onClick={onReset}
          className="rounded-md border border-slate-300 px-4 py-2.5 text-sm text-slate-700 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
        >
          Dịch file khác
        </button>
      </div>
    </section>
  );
}
