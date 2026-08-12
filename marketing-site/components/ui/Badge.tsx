import { ReactNode } from "react";

const toneClasses = {
  dark: "border-ink-line bg-white/3 text-on-ink-muted",
  light: "border-canvas-line bg-ink/3 text-on-canvas-muted",
};

export function Badge({
  children,
  tone = "dark",
}: {
  children: ReactNode;
  tone?: "dark" | "light";
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium backdrop-blur-sm ${toneClasses[tone]}`}
    >
      {children}
    </span>
  );
}
