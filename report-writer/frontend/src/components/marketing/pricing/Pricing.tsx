"use client";

import { motion } from "framer-motion";
import { Container } from "@/components/marketing/ui/Container";
import { SectionHeading } from "@/components/marketing/ui/SectionHeading";
import { Button } from "@/components/marketing/ui/Button";
import { APP_LOGIN_URL, CONTACT_HREF } from "@/lib/marketing/links";

const EASE = [0.16, 1, 0.3, 1] as const;

const TIERS = [
  {
    id: "solo",
    name: "Solo",
    price: "$39",
    period: "/mo",
    tagline: "For individual & fractional consultants",
    features: [
      "Up to 5 active clients",
      "PDF report + HTML dashboard",
      "CSV / Excel upload",
      "Full QA badge on every report",
      "Email support",
    ],
    cta: "Start free",
    highlighted: false,
  },
  {
    id: "agency",
    name: "Agency",
    price: "$149",
    period: "/mo",
    tagline: "For boutique agencies running recurring reports",
    features: [
      "Up to 50 active clients",
      "PDF + dashboard + Power BI export",
      "Recurring reports on a schedule",
      "Per-client branding",
      "Priority support",
    ],
    cta: "Start free",
    highlighted: true,
  },
  {
    id: "inhouse",
    name: "In-house",
    price: "Custom",
    period: "",
    tagline: "For growth teams connecting a live warehouse",
    features: [
      "Live SQL warehouse connection",
      "SSO & role-based access",
      "Custom branding & domains",
      "Dedicated support",
      "Volume-based pricing",
    ],
    cta: "Talk to us",
    highlighted: false,
  },
];

export function Pricing() {
  return (
    <section id="pricing" className="relative bg-paper py-28 md:py-36">
      <Container>
        <SectionHeading
          tone="light"
          align="center"
          eyebrow="Pricing"
          title="Straightforward, per seat you actually use."
          description="Every tier ships with the full QA badge — trust isn't an upsell."
        />

        <div className="mt-16 grid gap-6 lg:grid-cols-3">
          {TIERS.map((tier, i) => (
            <motion.div
              key={tier.id}
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-80px" }}
              transition={{ duration: 0.6, ease: EASE, delay: 0.08 * i }}
              className={`flex flex-col gap-6 rounded-[3px] border p-8 ${
                tier.highlighted
                  ? "border-verify bg-mkt-ink shadow-[0_30px_60px_-30px_rgba(20,23,27,0.4)]"
                  : "border-paper-line bg-paper-raised"
              }`}
            >
              {tier.highlighted && (
                <span className="w-fit rounded-[2px] bg-verify px-3 py-1 font-mkt-mono text-xs text-canvas">
                  Most popular
                </span>
              )}
              <div>
                <h3
                  className={`font-mkt-display text-2xl ${tier.highlighted ? "text-canvas" : "text-paper-text"}`}
                >
                  {tier.name}
                </h3>
                <p
                  className={`mt-1.5 text-sm ${tier.highlighted ? "text-on-mkt-ink-muted" : "text-paper-muted"}`}
                >
                  {tier.tagline}
                </p>
              </div>

              <div className="flex items-baseline gap-1">
                <span
                  className={`font-mkt-mono text-4xl tracking-tight tabular-nums ${tier.highlighted ? "text-canvas" : "text-paper-text"}`}
                >
                  {tier.price}
                </span>
                {tier.period && (
                  <span
                    className={`text-sm ${tier.highlighted ? "text-on-mkt-ink-muted" : "text-paper-muted"}`}
                  >
                    {tier.period}
                  </span>
                )}
              </div>

              <ul className="flex flex-1 flex-col gap-3">
                {tier.features.map((feature) => (
                  <li key={feature} className="flex items-start gap-2.5">
                    <span className="mkt-verify-mark mt-0.5" aria-hidden="true">
                      <svg viewBox="0 0 12 12" className="h-[0.65em] w-[0.65em]" fill="none">
                        <path d="M2.5 6.2 5 8.7 9.5 3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </span>
                    <span
                      className={`text-sm leading-relaxed ${tier.highlighted ? "text-on-mkt-ink-muted" : "text-paper-muted"}`}
                    >
                      {feature}
                    </span>
                  </li>
                ))}
              </ul>

              <Button
                href={tier.id === "inhouse" ? CONTACT_HREF : APP_LOGIN_URL}
                tone={tier.highlighted ? "dark" : "light"}
                variant={tier.highlighted ? "primary" : "secondary"}
                className="w-full"
              >
                {tier.cta}
              </Button>
            </motion.div>
          ))}
        </div>
      </Container>
    </section>
  );
}
