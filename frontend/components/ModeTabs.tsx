import type { Mode } from "../types";

interface Props {
  mode: Mode;
  onChange: (mode: Mode) => void;
  disabled: boolean;
}

const TABS: { id: Mode; name: string; hint: string }[] = [
  { id: "job", name: "Theo dõi tiến trình", hint: "Nên dùng cho file lớn" },
  { id: "sync", name: "Dịch nhanh", hint: "" },
  { id: "preview", name: "Xem trước 1 trang", hint: "" },
];

export function ModeTabs({ mode, onChange, disabled }: Props) {
  return (
    <div role="tablist" aria-label="Cách dịch" className="flex flex-wrap gap-2">
      {TABS.map((t) => {
        const selected = mode === t.id;
        return (
          <button
            key={t.id}
            role="tab"
            aria-selected={selected}
            type="button"
            disabled={disabled}
            onClick={() => onChange(t.id)}
            className={`rounded-lg border px-3 py-2 text-left text-sm transition ${
              selected
                ? "border-teal-700 bg-teal-50 text-slate-900 dark:border-teal-300 dark:bg-teal-300/10 dark:text-slate-100"
                : "border-slate-300 bg-transparent text-slate-600 hover:border-slate-400 hover:text-slate-900 dark:border-slate-800 dark:text-slate-400 dark:hover:border-slate-600 dark:hover:text-slate-200"
            } disabled:opacity-50`}
          >
            <span className="block font-medium">{t.name}</span>
            {t.hint ? (
              <span className="block text-xs text-slate-500 dark:text-slate-500">{t.hint}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
