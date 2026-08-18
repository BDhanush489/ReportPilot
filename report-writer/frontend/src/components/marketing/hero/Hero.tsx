"use client";

import { motion } from "framer-motion";
import { Container } from "@/components/marketing/ui/Container";
import { Button } from "@/components/marketing/ui/Button";
import { Badge } from "@/components/marketing/ui/Badge";
import { APP_LOGIN_URL } from "@/lib/marketing/links";
import { HeroScene } from "./HeroScene";

const EASE = [0.16, 1, 0.3, 1] as const;

export function Hero() {
  return (
    <section
      id="top"
      className="relative flex min-h-svh items-center overflow-hidden bg-mkt-ink"
    >
      <HeroScene />
      {/* Scrim so headline stays legible over the 3D scene at every viewport. */}
      <div
        className="absolute inset-0"
        style={{
          background:
            "linear-gradient(180deg, rgba(11,12,14,0.35) 0%, rgba(11,12,14,0.15) 30%, rgba(11,12,14,0.55) 78%, rgba(11,12,14,0.92) 100%)",
        }}
      />

      <Container className="relative z-10 pt-28 pb-20">
        <motion.div
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, ease: EASE }}
          className="mb-7"
        >
          <Badge>
            <span
              className="h-1.5 w-1.5 rounded-full bg-gold"
              aria-hidden="true"
            />
            Every number machine-verified before it ships
          </Badge>
        </motion.div>

        <motion.h1
          initial={{ opacity: 0, y: 22 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, ease: EASE, delay: 0.08 }}
          className="font-display max-w-3xl text-[2.6rem] leading-[1.05] tracking-tight text-canvas sm:text-6xl md:text-7xl"
        >
          Client-ready reports,
          <br />
          <span className="text-gold">not client-checked ones.</span>
        </motion.h1>

        <motion.p
          initial={{ opacity: 0, y: 22 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, ease: EASE, delay: 0.18 }}
          className="mt-7 max-w-xl text-lg leading-relaxed text-on-mkt-ink-muted md:text-xl"
        >
          Point ReportPilot at a CSV, a spreadsheet, or your warehouse. It
          builds the branded report or dashboard — and hands it back with a
          defensibility badge, because the math was computed, not guessed.
        </motion.p>

        <motion.div
          initial={{ opacity: 0, y: 22 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, ease: EASE, delay: 0.28 }}
          className="mt-10 flex flex-col gap-4 sm:flex-row sm:items-center"
        >
          <Button href={APP_LOGIN_URL} variant="primary">
            Start free
          </Button>
          <Button href="#deliverables" variant="secondary">
            See a sample report
          </Button>
        </motion.div>

        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.8, ease: EASE, delay: 0.42 }}
          className="mt-6 text-xs uppercase tracking-[0.18em] text-on-mkt-ink-muted/70"
        >
          CSV · Excel · Live SQL warehouse — no card required
        </motion.p>
      </Container>
    </section>
  );
}
