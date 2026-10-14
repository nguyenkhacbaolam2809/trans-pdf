import { LANGUAGES } from "../types";

interface Props {
  value: string;
  onChange: (code: string) => void;
  disabled: boolean;
}

export function LanguagePicker({ value, onChange, disabled }: Props) {
  return (
    <fieldset>
      <div className="mb-2 flex items-center justify-between">
        <legend className="text-xs font-medium uppercase tracking-wide text-slate-700 dark:text-slate-300">
          Ngôn ngữ đích
        </legend>
        <span className="text-xs text-slate-500 dark:text-slate-500">
          Tự nhận diện ngôn ngữ gốc
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {LANGUAGES.map((l) => {
          const active = value === l.code;
          return (
            <button
              key={l.code}
              type="button"
              disabled={disabled}
              aria-pressed={active}
              onClick={() => onChange(l.code)}
              className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-left transition disabled:opacity-50 ${
                active
                  ? "border-teal-700 bg-teal-50 text-slate-900 dark:border-teal-300 dark:bg-teal-300/10 dark:text-slate-100"
                  : "border-slate-300 text-slate-600 hover:border-slate-400 hover:text-slate-900 dark:border-slate-800 dark:text-slate-400 dark:hover:border-slate-600 dark:hover:text-slate-200"
              }`}
            >
              <span aria-hidden="true" className="text-base leading-none">
                {l.flag}
              </span>
              <span className="min-w-0">
                <span className="block truncate text-xs font-medium">
                  {l.name}
                </span>
                <span className="block truncate text-xs text-slate-500">
                  {l.native}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}
