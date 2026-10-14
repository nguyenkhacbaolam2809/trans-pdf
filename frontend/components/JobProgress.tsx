import { formatTime } from "../lib/format";

interface Props {
  progress: number;
  step: string;
  detail: string;
  elapsed: number;
  showCancel: boolean;
  onCancel: () => void;
}

export function JobProgress({
  progress,
  step,
  detail,
  elapsed,
  showCancel,
  onCancel,
}: Props) {
  const pct = Math.min(100, Math.max(0, progress));
  return (
    <div aria-live="polite" className="space-y-2">
      <div className="flex items-center justify-between gap-3 text-xs">
        <p className="min-w-0 truncate text-slate-700 dark:text-slate-300">
          {step || "Chuẩn bị"}
          {detail ? <span className="text-slate-500 dark:text-slate-500">, {detail}</span> : null}
        </p>
        <p className="shrink-0 tabular-nums text-slate-600 dark:text-slate-400">
          {pct}% · {formatTime(elapsed)}
        </p>
      </div>
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-label="Tiến trình dịch"
        className="h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800"
      >
        <div
          className="h-full rounded-full bg-teal-700 transition-all duration-500 dark:bg-teal-300"
          style={{ width: `${pct}%` }}
        />
      </div>
      {showCancel && (
        <button
          type="button"
          onClick={onCancel}
          className="text-xs text-slate-600 underline underline-offset-4 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200"
        >
          Hủy job
        </button>
      )}
    </div>
  );
}
