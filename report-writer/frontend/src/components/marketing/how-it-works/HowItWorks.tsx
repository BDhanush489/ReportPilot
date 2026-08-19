"use client";

import { motion } from "framer-motion";
import { Container } from "@/components/marketing/ui/Container";
import { SectionHeading } from "@/components/marketing/ui/SectionHeading";
import { CoalesceCanvas } from "./CoalesceCanvas";

const EASE = [0.16, 1, 0.3, 1] as const;

const STEPS = [
  {
    n: "01",
    title: "Connect your data",
    body: "Drop in a CSV or Excel export, or point it at a live SQL warehouse. No manual mapping, no transform scripts to babysit.",
  },
  {
    n: "02",
    title: "It builds the report",
    body: "One shared pipeline produces a branded PDF, an interactive dashboard, and a Power BI export — same numbers, same visual language, every time.",
  },
  {
    n: "03",
    title: "It ships with a badge",
    body: "Traceability, aggregation sanity, and a claims scan run automatically. You hand over the report already knowing it holds up.",
  },
];

export function HowItWorks() {
  return (
    <section id="how-it-works" className="relative bg-mkt-ink pb-28 pt-4 md:pb-36">
      <Container>
        <SectionHeading
          eyebrow="How it works"
          title="Raw data in. A verified report out."
          description="Three steps, no spreadsheet gymnastics in between."
        />

        <div className="mt-14">
          <CoalesceCanvas />
        </div>

        <div className="mt-4 grid gap-8 border-t border-mkt-ink-line pt-12 md:grid-cols-3 md:gap-10">
          {STEPS.map((step, i) => (
            <motion.div
              key={step.n}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-60px" }}
              transition={{ duration: 0.6, ease: EASE, delay: 0.1 * i }}
              className="flex flex-col gap-3"
            >
              <span className="font-display text-sm text-gold">{step.n}</span>
              <h3 className="font-display text-xl text-canvas md:text-2xl">
                {step.title}
              </h3>
              <p className="text-sm leading-relaxed text-on-mkt-ink-muted md:text-base">
                {step.body}
              </p>
            </motion.div>
          ))}
        </div>
      </Container>
    </section>
  );
}
