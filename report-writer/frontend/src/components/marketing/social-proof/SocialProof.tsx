"use client";

import { motion } from "framer-motion";
import { Container } from "@/components/marketing/ui/Container";

const EASE = [0.16, 1, 0.3, 1] as const;

const STATS = [
  { value: "100%", label: "Of figures trace to source" },
  { value: "3", label: "Deliverable formats, one pipeline" },
  { value: "0", label: "Numbers the AI is allowed to invent" },
  { value: "3-tier", label: "PASS / WARN / FAIL badge on every report" },
];

/** Abstract, non-attributed marks -- a real logo wall gets dropped in here
 * once there are client logos to show; this stays honest in the meantime. */
function LogoSlot({ i }: { i: number }) {
  return (
    <div className="flex h-14 items-center justify-center rounded-lg border border-dashed border-canvas-line">
      <svg width="72" height="20" viewBox="0 0 72 20" aria-hidden="true">
        <rect x="0" y="6" width="20" height="8" rx="2" fill="currentColor" opacity="0.18" />
        <circle cx="36" cy="10" r="7" fill="currentColor" opacity="0.14" />
        <rect x="50" y="4" width="22" height="12" rx="3" fill="currentColor" opacity="0.1" />
      </svg>
      <span className="sr-only">Client logo placeholder {i + 1}</span>
    </div>
  );
}

export function SocialProof() {
  return (
    <section className="relative bg-canvas-soft py-24 md:py-28">
      <Container>
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-80px" }}
          transition={{ duration: 0.6, ease: EASE }}
          className="grid gap-8 border-b border-canvas-line pb-16 sm:grid-cols-2 lg:grid-cols-4"
        >
          {STATS.map((stat) => (
            <div key={stat.label}>
              <div className="font-display text-4xl tracking-tight text-mkt-ink md:text-5xl">
                {stat.value}
              </div>
              <div className="mt-2 text-sm text-on-canvas-muted">{stat.label}</div>
            </div>
          ))}
        </motion.div>

        <div className="mt-16 grid gap-12 lg:grid-cols-[1.1fr_0.9fr] lg:gap-16">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-on-canvas-muted">
              Where reports like this already ship
            </p>
            <div className="mt-6 grid grid-cols-2 gap-3 text-mkt-ink/40 sm:grid-cols-3">
              {Array.from({ length: 6 }, (_, i) => (
                <LogoSlot key={i} i={i} />
              ))}
            </div>
          </div>

          <div className="rounded-2xl border border-dashed border-canvas-line p-7">
            <svg width="28" height="22" viewBox="0 0 28 22" className="text-gold" fill="currentColor" aria-hidden="true">
              <path d="M0 22V13.2C0 8.4 1.4 4.9 4.2 2.7 7 0.5 9.9 -0.4 12.9 0v4.9c-2 0-3.6 0.6-4.8 1.9-1.2 1.2-1.8 3-1.8 5.2h5.1V22H0Zm14.1 0V13.2c0-4.8 1.4-8.3 4.2-10.5C21.1 0.5 24 -0.4 27 0v4.9c-2 0-3.6 0.6-4.8 1.9-1.2 1.2-1.8 3-1.8 5.2h5.1V22h-11.4Z" />
            </svg>
            <div className="mt-5 space-y-2.5">
              <div className="h-2.5 w-full rounded-full bg-mkt-ink/10" />
              <div className="h-2.5 w-11/12 rounded-full bg-mkt-ink/10" />
              <div className="h-2.5 w-2/3 rounded-full bg-mkt-ink/10" />
            </div>
            <div className="mt-6 flex items-center gap-3">
              <div className="h-9 w-9 rounded-full bg-mkt-ink/10" />
              <div className="space-y-1.5">
                <div className="h-2 w-24 rounded-full bg-mkt-ink/15" />
                <div className="h-2 w-16 rounded-full bg-mkt-ink/10" />
              </div>
            </div>
            <p className="mt-5 text-xs text-on-canvas-muted">
              Client testimonial — reserved
            </p>
          </div>
        </div>
      </Container>
    </section>
  );
}
