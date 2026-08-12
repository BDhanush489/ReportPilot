import { ReactNode } from "react";

export function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-2 text-xs font-medium uppercase tracking-[0.2em] text-gold">
      <span aria-hidden className="h-px w-6 bg-gold/70" />
      {children}
    </span>
  );
}
