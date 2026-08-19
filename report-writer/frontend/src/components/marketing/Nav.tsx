"use client";

import { useState } from "react";
import { motion, useMotionValueEvent, useScroll } from "framer-motion";
import { Container } from "@/components/marketing/ui/Container";
import { Button } from "@/components/marketing/ui/Button";
import { APP_LOGIN_URL } from "@/lib/marketing/links";

const LINKS = [
  { href: "#trust", label: "How it's verified" },
  { href: "#how-it-works", label: "How it works" },
  { href: "#deliverables", label: "Deliverables" },
  { href: "#pricing", label: "Pricing" },
];

export function Nav() {
  const [solid, setSolid] = useState(false);
  const { scrollY } = useScroll();

  useMotionValueEvent(scrollY, "change", (latest) => {
    setSolid(latest > 64);
  });

  return (
    <motion.header
      className="fixed inset-x-0 top-0 z-50 transition-colors duration-500 ease-(--ease-luxury)"
      style={{
        backgroundColor: solid ? "var(--color-mkt-ink)" : "transparent",
        borderBottom: solid
          ? "1px solid var(--color-mkt-ink-line)"
          : "1px solid transparent",
      }}
    >
      <Container className="flex h-18 items-center justify-between py-4">
        <a
          href="#top"
          className="font-mkt-display text-lg italic tracking-tight text-canvas"
        >
          ReportPilot
        </a>

        <nav className="hidden items-center gap-8 md:flex">
          {LINKS.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="text-sm text-on-mkt-ink-muted transition-colors duration-300 hover:text-canvas"
            >
              {link.label}
            </a>
          ))}
        </nav>

        <div className="flex items-center gap-3">
          <Button href="#deliverables" variant="ghost" className="hidden sm:inline-flex">
            See a sample report
          </Button>
          <Button href={APP_LOGIN_URL} variant="ghost" className="hidden sm:inline-flex">
            Sign in
          </Button>
          <Button href={APP_LOGIN_URL} variant="primary">
            Start free
          </Button>
        </div>
      </Container>
    </motion.header>
  );
}
