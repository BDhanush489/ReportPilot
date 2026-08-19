"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Container } from "@/components/marketing/ui/Container";
import { SectionHeading } from "@/components/marketing/ui/SectionHeading";
import { Button } from "@/components/marketing/ui/Button";
import { APP_LOGIN_URL } from "@/lib/marketing/links";

const EASE = [0.16, 1, 0.3, 1] as const;

const PATHS = [
  {
    id: "agency",
    tab: "Boutique agencies",
    meta: "5–50 clients",
    headline: "Kill reporting day.",
    body: "Connect each client's data source once. Every recurring report regenerates itself on your schedule, branded per client, without anyone opening a spreadsheet the week it's due.",
    points: [
      "Per-client branding, saved once",
      "Recurring reports on a schedule",
      "One QA badge per report, every time",
    ],
    cta: "See it for agencies",
  },
  {
    id: "solo",
    tab: "Solo & fractional consultants",
    meta: "One person, many clients",
    headline: "Hand it over without re-checking it.",
    body: "You already know the story the data tells — you shouldn't have to re-verify every cell before a client sees it. Ship the report with a badge that says the numbers already checked out.",
    points: [
      "No manual number-checking pass",
      "Client-ready in minutes, not an evening",
      "Looks like a $50k deliverable, costs a coffee",
    ],
    cta: "See it for consultants",
  },
  {
    id: "inhouse",
    tab: "In-house growth teams",
    meta: "Speaks SQL, not just CSV",
    headline: "Point it at the warehouse.",
    body: "No exporting to CSV and re-importing every week. Connect the report directly to your warehouse and let it pull live, so the dashboard your team checks Monday is never a day stale.",
    points: [
      "Direct SQL warehouse connection",
      "Live dashboards, not static exports",
      "Same trust badge your stakeholders expect",
    ],
    cta: "See it for growth teams",
  },
];

export function AudiencePaths() {
  const [active, setActive] = useState(0);
  const path = PATHS[active];

  return (
    <section id="audiences" className="relative bg-paper py-28 md:py-36">
      <Container>
        <SectionHeading
          tone="light"
          eyebrow="Built for how you work"
          title="Three teams, one pipeline."
          description="Same deterministic engine underneath — the workflow around it adapts to you."
        />

        <div className="mt-12 flex flex-wrap gap-2" role="tablist" aria-label="Audience">
          {PATHS.map((p, i) => (
            <button
              key={p.id}
              id={`tab-${p.id}`}
              role="tab"
              aria-selected={active === i}
              aria-controls={`panel-${p.id}`}
              onClick={() => setActive(i)}
              className={`rounded-[3px] border px-5 py-2.5 text-sm font-medium transition-colors duration-300 ease-(--ease-luxury) ${
                active === i
                  ? "border-verify bg-verify text-canvas"
                  : "border-paper-line text-paper-muted hover:border-verify/50 hover:text-paper-text"
              }`}
            >
              {p.tab}
            </button>
          ))}
        </div>

        <div className="relative mt-10 min-h-90 overflow-hidden rounded-[3px] border border-paper-line bg-paper-raised">
          <AnimatePresence mode="wait">
            <motion.div
              key={path.id}
              id={`panel-${path.id}`}
              role="tabpanel"
              aria-labelledby={`tab-${path.id}`}
              tabIndex={0}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -12 }}
              transition={{ duration: 0.45, ease: EASE }}
              className="grid gap-10 p-8 md:grid-cols-2 md:p-12"
            >
              <div className="flex flex-col gap-5">
                <span className="font-mkt-mono text-xs uppercase tracking-[0.1em] text-verify">
                  {path.meta}
                </span>
                <h3 className="font-mkt-display text-3xl leading-tight tracking-tight text-paper-text md:text-4xl">
                  {path.headline}
                </h3>
                <p className="text-base leading-relaxed text-paper-muted">
                  {path.body}
                </p>
                <Button href={APP_LOGIN_URL} tone="light" variant="secondary" className="mt-2 self-start">
                  {path.cta}
                </Button>
              </div>

              <ul className="flex flex-col justify-center gap-4 border-t border-paper-line pt-6 md:border-t-0 md:border-l md:pl-10 md:pt-0">
                {path.points.map((point) => (
                  <li key={point} className="flex items-start gap-3">
                    <span className="mkt-verify-mark mt-0.5" aria-hidden="true">
                      <svg viewBox="0 0 12 12" className="h-[0.65em] w-[0.65em]" fill="none">
                        <path d="M2.5 6.2 5 8.7 9.5 3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </span>
                    <span className="text-sm leading-relaxed text-paper-text/85">{point}</span>
                  </li>
                ))}
              </ul>
            </motion.div>
          </AnimatePresence>
        </div>
      </Container>
    </section>
  );
}
