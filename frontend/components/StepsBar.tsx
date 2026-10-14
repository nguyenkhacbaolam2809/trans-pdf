interface Props {
  hasFile: boolean;
  hasResult: boolean;
}

const STEPS = ["Chọn file", "Ngôn ngữ và cách dịch", "Kết quả và sửa lỗi"];

export function StepsBar({ hasFile, hasResult }: Props) {
  const current = hasResult ? 3 : hasFile ? 2 : 1;
  return (
    <ol
      aria-label="Tiến độ các bước"
      className="flex items-center gap-2 text-xs"
    >
      {STEPS.map((label, i) => {
        const n = i + 1;
        const done = n < current;
        const active = n === current;
        return (
          <li key={label} className="flex flex-1 items-center gap-2">
            <span
              aria-current={active ? "step" : undefined}
              className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs font-semibold ${
                done
                  ? "border-teal-700 bg-teal-700 text-white dark:border-teal-300 dark:bg-teal-300 dark:text-slate-950"
                  : active
                    ? "border-teal-700 text-teal-800 dark:border-teal-300 dark:text-teal-200"
                    : "border-slate-300 text-slate-500 dark:border-slate-700 dark:text-slate-400"
              }`}
            >
              {done ? "✓" : n}
            </span>
            <span
              className={
                active
                  ? "font-medium text-slate-900 dark:text-slate-100"
                  : "text-slate-600 dark:text-slate-400"
              }
            >
              {label}
            </span>
            {n < STEPS.length && (
              <span
                aria-hidden="true"
                className="mx-1 hidden h-px flex-1 bg-slate-200 dark:bg-slate-800 sm:block"
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}
