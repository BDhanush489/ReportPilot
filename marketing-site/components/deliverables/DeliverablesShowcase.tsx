"use client";

import { motion } from "framer-motion";
import { Container } from "@/components/ui/Container";
import { SectionHeading } from "@/components/ui/SectionHeading";
import { PdfMockup, DashboardMockup, PowerBiMockup } from "./mockups";

const EASE = [0.16, 1, 0.3, 1] as const;

const ITEMS = [
  {
    id: "pdf",
    label: "Branded PDF report",
    body: "The one you attach to an email and never think about again — typeset, paginated, and carrying your client's branding, not yours.",
    Mockup: PdfMockup,
  },
  {
    id: "dashboard",
    label: "Interactive HTML dashboard",
    body: "Self-contained, filterable, drills into the same numbers as the PDF — because it's built from the same shared data artifact.",
    Mockup: DashboardMockup,
  },
  {
    id: "powerbi",
    label: "Power BI export",
    body: "For teams already living in Power BI — the same verified figures, delivered as tiles your stakeholders already know how to read.",
    Mockup: PowerBiMockup,
  },
];

export function DeliverablesShowcase() {
  return (
    <section id="deliverables" className="relative bg-canvas py-28 md:py-36">
      <Container>
        <SectionHeading
          tone="light"
          eyebrow="One pipeline, three deliverables"
          title="Every format speaks the same numbers."
          description="Change the branding once — the PDF, the dashboard, and the Power BI export all draw from the same verified data artifact."
        />

        <div className="mt-16 grid gap-8 md:grid-cols-3 md:gap-6">
          {ITEMS.map(({ id, label, body, Mockup }, i) => (
            <motion.div
              key={id}
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-80px" }}
              transition={{ duration: 0.65, ease: EASE, delay: 0.1 * i }}
              className="flex flex-col gap-5"
            >
              <div
                aria-hidden="true"
                className="aspect-4/3 rounded-2xl border border-canvas-line bg-white/40 p-3"
              >
                <Mockup />
              </div>
              <div>
                <h3 className="font-display text-lg text-ink">{label}</h3>
                <p className="mt-2 text-sm leading-relaxed text-on-canvas-muted">
                  {body}
                </p>
              </div>
            </motion.div>
          ))}
        </div>
      </Container>
    </section>
  );
}
