import { ReactNode } from "react";

// A section label styled like a document reference mark rather than a
// decorative hairline -- monospace, echoing marketing.css's ledger/mono
// vocabulary instead of an arbitrary accent-colored rule.
export function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-2 font-mkt-mono text-xs uppercase tracking-[0.16em] text-verify">
      <span aria-hidden>§</span>
      {children}
    </span>
  );
}
