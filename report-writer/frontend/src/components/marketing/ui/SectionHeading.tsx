import { ReactNode } from "react";
import { Eyebrow } from "./Eyebrow";

export function SectionHeading({
  eyebrow,
  title,
  description,
  align = "left",
  tone = "dark",
}: {
  eyebrow?: string;
  title: ReactNode;
  description?: ReactNode;
  align?: "left" | "center";
  tone?: "dark" | "light";
}) {
  return (
    <div
      className={`flex flex-col gap-5 ${align === "center" ? "items-center text-center" : "items-start text-left"}`}
    >
      {eyebrow && <Eyebrow>{eyebrow}</Eyebrow>}
      <h2
        className={`font-display max-w-2xl text-3xl leading-[1.1] tracking-tight md:text-5xl ${tone === "dark" ? "text-canvas" : "text-mkt-ink"}`}
      >
        {title}
      </h2>
      {description && (
        <p
          className={`max-w-xl text-base leading-relaxed md:text-lg ${tone === "dark" ? "text-on-mkt-ink-muted" : "text-on-canvas-muted"}`}
        >
          {description}
        </p>
      )}
    </div>
  );
}
