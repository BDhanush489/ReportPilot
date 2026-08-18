import { ReactNode } from "react";
import Link from "next/link";

type Variant = "primary" | "secondary" | "ghost";
type Tone = "dark" | "light";

const base =
  "inline-flex items-center justify-center gap-2 rounded-full px-6 py-3 text-sm font-medium tracking-wide transition-all duration-300 ease-(--ease-luxury) focus-visible:outline-2 focus-visible:outline-[var(--color-gold)] focus-visible:outline-offset-4";

const primary =
  "bg-gold text-mkt-ink hover:bg-gold-light shadow-[0_1px_0_0_rgba(255,255,255,0.15)_inset] hover:-translate-y-0.5";

const variants: Record<Tone, Record<Variant, string>> = {
  dark: {
    primary,
    secondary:
      "border border-mkt-ink-line text-canvas hover:border-gold/60 hover:text-gold hover:-translate-y-0.5",
    ghost: "text-on-mkt-ink-muted hover:text-canvas",
  },
  light: {
    primary,
    secondary:
      "border border-canvas-line text-mkt-ink hover:border-gold-dark/60 hover:text-gold-dark hover:-translate-y-0.5",
    ghost: "text-on-canvas-muted hover:text-mkt-ink",
  },
};

export function Button({
  href,
  variant = "primary",
  tone = "dark",
  children,
  className = "",
  onClick,
  type = "button",
}: {
  href?: string;
  variant?: Variant;
  tone?: Tone;
  children: ReactNode;
  className?: string;
  onClick?: () => void;
  type?: "button" | "submit";
}) {
  const classes = `${base} ${variants[tone][variant]} ${className}`;
  if (href) {
    return (
      <Link href={href} className={classes}>
        {children}
      </Link>
    );
  }
  return (
    <button type={type} onClick={onClick} className={classes}>
      {children}
    </button>
  );
}
