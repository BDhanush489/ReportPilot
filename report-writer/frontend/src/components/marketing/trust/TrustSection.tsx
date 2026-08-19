"use client";

import { motion } from "framer-motion";
import { Container } from "@/components/marketing/ui/Container";
import { Eyebrow } from "@/components/marketing/ui/Eyebrow";
import { BadgeMoment } from "./BadgeMoment";

const EASE = [0.16, 1, 0.3, 1] as const;

const PILLARS = [
  {
    title: "Number traceability",
    body: "Every figure in the report traces back to the exact computation that produced it — no number appears that the pipeline can't re-derive on demand.",
  },
  {
    title: "Aggregation sanity",
    body: "Totals, averages, and rollups are recomputed independently and diffed against what's printed, so a formatting bug can never quietly become a wrong number.",
  },
  {
    title: "Unsupported-claim scan",
    body: "Narrative language is checked against the underlying data before it ships — the model can describe a trend, never invent one.",
  },
];

export function TrustSection() {
  return (
    <section id="trust" className="relative bg-mkt-ink py-28 md:py-36">
      <Container>
        <div className="grid items-center gap-16 lg:grid-cols-[1.05fr_0.95fr] lg:gap-8">
          <motion.div
            initial={{ opacity: 0, y: 24 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-80px" }}
            transition={{ duration: 0.7, ease: EASE }}
            className="flex flex-col gap-7"
          >
            <Eyebrow>The trust mechanism</Eyebrow>
            <h2 className="font-mkt-display max-w-xl text-3xl leading-[1.12] tracking-tight text-canvas md:text-5xl">
              The AI writes the words.
              <br />
              <span className="italic text-verify-light">The math writes the numbers.</span>
            </h2>
            <p className="max-w-lg text-base leading-relaxed text-on-mkt-ink-muted md:text-lg">
              Every ReportPilot output ships with a QA badge, because those two
              jobs are handled by two different systems. Language generation
              never touches a figure — it narrates what a deterministic
              pipeline already computed and verified. That separation is the
              whole reason a report can go to a client without you
              re-checking every cell first.
            </p>

            <dl className="mt-4 flex flex-col gap-6 border-t border-mkt-ink-line pt-8">
              {PILLARS.map((pillar, i) => (
                <motion.div
                  key={pillar.title}
                  initial={{ opacity: 0, y: 14 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true, margin: "-60px" }}
                  transition={{ duration: 0.6, ease: EASE, delay: 0.08 * i }}
                  className="flex gap-4"
                >
                  <span
                    aria-hidden
                    className="mt-1.5 h-1.5 w-1.5 flex-none rounded-full bg-verify"
                  />
                  <div>
                    <dt className="font-mkt-display text-lg text-canvas">
                      {pillar.title}
                    </dt>
                    <dd className="mt-1.5 text-sm leading-relaxed text-on-mkt-ink-muted">
                      {pillar.body}
                    </dd>
                  </div>
                </motion.div>
              ))}
            </dl>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, scale: 0.94 }}
            whileInView={{ opacity: 1, scale: 1 }}
            viewport={{ once: true, margin: "-100px" }}
            transition={{ duration: 0.8, ease: EASE }}
          >
            <BadgeMoment />
            <p className="mt-6 text-center text-xs uppercase tracking-[0.18em] text-on-mkt-ink-muted/70">
              PASS · Every figure verified
            </p>
          </motion.div>
        </div>
      </Container>
    </section>
  );
}
