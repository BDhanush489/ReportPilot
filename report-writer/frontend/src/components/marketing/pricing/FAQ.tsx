"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { SectionHeading } from "@/components/marketing/ui/SectionHeading";

const EASE = [0.16, 1, 0.3, 1] as const;

const FAQS = [
  {
    q: "How does the QA badge actually work?",
    a: "Every plotted or printed number is recomputed independently from the source data and diffed against what's displayed. If they match within tolerance, that figure passes. The report carries a PASS, PASS-WITH-WARNINGS, or FAIL badge — never a silent guess.",
  },
  {
    q: "What data sources are supported?",
    a: "CSV and Excel uploads today, with column-type detection that handles messy real-world exports — mixed delimiters, locale decimal formats, split year/month columns. Live SQL warehouse connections are available on the In-house tier.",
  },
  {
    q: "Can I white-label reports for my clients?",
    a: "Yes — branding (logo, name, primary and accent color) is set per client and reused automatically on every report you regenerate for them.",
  },
  {
    q: "What happens if a number can't be verified?",
    a: "It's flagged, not hidden and not guessed. The report shows exactly which figure failed reconciliation and why, so you know before your client does.",
  },
  {
    q: "Does the AI ever touch the numbers?",
    a: "No. The language model only narrates figures a deterministic pipeline already computed — it never performs arithmetic that ends up in the report, and unsupported claims are scanned for and rejected before the report ships.",
  },
  {
    q: "Can I cancel anytime?",
    a: "Yes, monthly plans have no lock-in. Reports you've already generated remain yours.",
  },
];

export function FAQ() {
  const [open, setOpen] = useState<number | null>(0);

  return (
    <section id="faq" className="relative bg-canvas-soft py-28 md:py-32">
      <div className="mx-auto w-full max-w-205 px-6 md:px-10">
        <SectionHeading tone="light" eyebrow="FAQ" title="Questions worth asking." />

        <div className="mt-12 divide-y divide-canvas-line border-t border-b border-canvas-line">
          {FAQS.map((item, i) => {
            const isOpen = open === i;
            return (
              <div key={item.q}>
                <button
                  onClick={() => setOpen(isOpen ? null : i)}
                  aria-expanded={isOpen}
                  className="flex w-full items-center justify-between gap-6 py-5 text-left"
                >
                  <span className="font-display text-lg text-mkt-ink">{item.q}</span>
                  <motion.span
                    animate={{ rotate: isOpen ? 45 : 0 }}
                    transition={{ duration: 0.3, ease: EASE }}
                    className="flex-none text-2xl leading-none text-gold-dark"
                    aria-hidden="true"
                  >
                    +
                  </motion.span>
                </button>
                <AnimatePresence initial={false}>
                  {isOpen && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.35, ease: EASE }}
                      className="overflow-hidden"
                    >
                      <p className="pb-5 pr-10 text-sm leading-relaxed text-on-canvas-muted">
                        {item.a}
                      </p>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
