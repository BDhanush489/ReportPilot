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
        className={`font-mkt-display max-w-2xl text-3xl leading-[1.1] tracking-tight md:text-5xl ${tone === "dark" ? "text-canvas" : "text-paper-text"}`}
      >
        {title}
      </h2>
      {description && (
        <p
          className={`max-w-xl text-base leading-relaxed md:text-lg ${tone === "dark" ? "text-on-mkt-ink-muted" : "text-paper-muted"}`}
        >
          {description}
        </p>
      )}
    </div>
  );
}
