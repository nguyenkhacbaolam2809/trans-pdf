import { ThemeToggle } from "./ThemeToggle";

interface Props {
  health: string;
}

export function Header({ health }: Props) {
  return (
    <header className="sticky top-0 z-20 border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950">
      <div className="mx-auto flex max-w-5xl items-center justify-between gap-3 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-3">
          <span
            aria-hidden="true"
            className="flex h-9 w-9 items-center justify-center rounded-lg bg-teal-700 text-white dark:bg-teal-300 dark:text-slate-950"
          >
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
            >
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="9" y1="13" x2="15" y2="13" />
              <line x1="9" y1="17" x2="15" y2="17" />
            </svg>
          </span>
          <span>
            <span className="block text-sm font-semibold leading-tight text-slate-900 dark:text-slate-100">
              trans-pdf
            </span>
            <span className="block text-xs text-slate-600 dark:text-slate-400">
              Dịch PDF, giữ nguyên trình bày
            </span>
          </span>
        </div>
        <div className="flex items-center gap-3">
          <p
            className="hidden max-w-xs truncate text-xs text-slate-600 dark:text-slate-400 sm:block"
            role="status"
            title={health}
          >
            {health}
          </p>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
