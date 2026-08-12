"use client";

import { motion } from "framer-motion";
import { Container } from "@/components/ui/Container";
import { Button } from "@/components/ui/Button";
import { APP_LOGIN_URL } from "@/lib/links";

const EASE = [0.16, 1, 0.3, 1] as const;

export function FinalCta() {
  return (
    <section className="relative overflow-hidden bg-ink py-28 md:py-36">
      <div
        aria-hidden="true"
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(45% 60% at 50% 100%, rgba(184,152,91,0.18), transparent 70%)",
        }}
      />
      <Container className="relative z-10">
        <motion.div
          initial={{ opacity: 0, y: 22 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-80px" }}
          transition={{ duration: 0.7, ease: EASE }}
          className="flex flex-col items-center gap-8 text-center"
        >
          <h2 className="font-display max-w-2xl text-4xl leading-[1.1] tracking-tight text-canvas md:text-6xl">
            Your next report doesn&apos;t need
            <br />
            <span className="text-gold">a second pass.</span>
          </h2>
          <p className="max-w-md text-base leading-relaxed text-on-ink-muted md:text-lg">
            Start free, generate your first verified report today, and see
            the badge for yourself.
          </p>
          <div className="flex flex-col gap-4 sm:flex-row">
            <Button href={APP_LOGIN_URL} variant="primary">
              Start free
            </Button>
            <Button href="#deliverables" variant="secondary">
              See a sample report
            </Button>
          </div>
        </motion.div>
      </Container>
    </section>
  );
}
