interface Props {
  value: number;
  onChange: (page: number) => void;
  disabled: boolean;
}

export function PageInput({ value, onChange, disabled }: Props) {
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900">
      <label htmlFor="preview-page" className="text-xs text-slate-700 dark:text-slate-300">
        Trang muốn xem trước
      </label>
      <input
        id="preview-page"
        type="number"
        min={1}
        max={5000}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Math.max(1, Number(e.target.value) || 1))}
        className="w-24 rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
      />
      <span className="text-xs text-slate-600 dark:text-slate-400">
        Đánh số từ 1. Chỉ dịch trang này nên xong nhanh.
      </span>
    </div>
  );
}
