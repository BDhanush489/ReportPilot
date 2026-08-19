import { ReactNode } from "react";

const toneClasses = {
  dark: "border-mkt-ink-line text-on-mkt-ink-muted",
  light: "border-paper-line text-paper-muted",
};

// A stamp, not a glass pill: sharp corners, a hairline border, the same
// verification-tick device used everywhere else something is claimed to
// be checked (see marketing.css's .mkt-verify-mark and app/qa.py's real
// PASS/WARN/FAIL badge, which this is a marketing-page echo of).
export function Badge({
  children,
  tone = "dark",
}: {
  children: ReactNode;
  tone?: "dark" | "light";
}) {
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-[2px] border px-3 py-1.5 font-mkt-mono text-xs tracking-tight ${toneClasses[tone]}`}
    >
      <span className="mkt-verify-mark" aria-hidden="true">
        <svg viewBox="0 0 12 12" className="h-[0.65em] w-[0.65em]" fill="none">
          <path d="M2.5 6.2 5 8.7 9.5 3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
      {children}
    </span>
  );
}
