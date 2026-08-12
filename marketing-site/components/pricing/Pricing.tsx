"use client";

import { motion } from "framer-motion";
import { Container } from "@/components/ui/Container";
import { SectionHeading } from "@/components/ui/SectionHeading";
import { Button } from "@/components/ui/Button";
import { APP_LOGIN_URL, CONTACT_HREF } from "@/lib/links";

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
    <section id="pricing" className="relative bg-canvas py-28 md:py-36">
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
              className={`flex flex-col gap-6 rounded-2xl border p-8 ${
                tier.highlighted
                  ? "border-gold bg-ink shadow-[0_30px_60px_-30px_rgba(11,12,14,0.4)]"
                  : "border-canvas-line bg-canvas-soft"
              }`}
            >
              {tier.highlighted && (
                <span className="w-fit rounded-full bg-gold px-3 py-1 text-xs font-medium text-ink">
                  Most popular
                </span>
              )}
              <div>
                <h3
                  className={`font-display text-2xl ${tier.highlighted ? "text-canvas" : "text-ink"}`}
                >
                  {tier.name}
                </h3>
                <p
                  className={`mt-1.5 text-sm ${tier.highlighted ? "text-on-ink-muted" : "text-on-canvas-muted"}`}
                >
                  {tier.tagline}
                </p>
              </div>

              <div className="flex items-baseline gap-1">
                <span
                  className={`font-display text-4xl tracking-tight ${tier.highlighted ? "text-canvas" : "text-ink"}`}
                >
                  {tier.price}
                </span>
                {tier.period && (
                  <span
                    className={`text-sm ${tier.highlighted ? "text-on-ink-muted" : "text-on-canvas-muted"}`}
                  >
                    {tier.period}
                  </span>
                )}
              </div>

              <ul className="flex flex-1 flex-col gap-3">
                {tier.features.map((feature) => (
                  <li key={feature} className="flex items-start gap-2.5">
                    <svg
                      viewBox="0 0 20 20"
                      className={`mt-0.5 h-4 w-4 flex-none ${tier.highlighted ? "text-gold" : "text-gold-dark"}`}
                      fill="none"
                      aria-hidden="true"
                    >
                      <path
                        d="M4 10.5l3.5 3.5L16 5.5"
                        stroke="currentColor"
                        strokeWidth="1.8"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                    <span
                      className={`text-sm leading-relaxed ${tier.highlighted ? "text-on-ink-muted" : "text-on-canvas-muted"}`}
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
