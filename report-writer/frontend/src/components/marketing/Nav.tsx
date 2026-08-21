"use client";

import { useState } from "react";
import { AnimatePresence, motion, useMotionValueEvent, useScroll } from "framer-motion";
import { Container } from "@/components/marketing/ui/Container";
import { Button } from "@/components/marketing/ui/Button";
import { APP_LOGIN_URL } from "@/lib/marketing/links";

const EASE = [0.16, 1, 0.3, 1] as const;

const LINKS = [
  { href: "#trust", label: "How it's verified" },
  { href: "#how-it-works", label: "How it works" },
  { href: "#deliverables", label: "Deliverables" },
  { href: "#pricing", label: "Pricing" },
];

export function Nav() {
  const [solid, setSolid] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const { scrollY } = useScroll();

  useMotionValueEvent(scrollY, "change", (latest) => {
    setSolid(latest > 64);
  });

  return (
    <motion.header
      className="fixed inset-x-0 top-0 z-50 transition-colors duration-500 ease-(--ease-luxury)"
      style={{
        backgroundColor: solid || mobileOpen ? "rgba(11,12,14,0.82)" : "transparent",
        backdropFilter: solid || mobileOpen ? "blur(12px)" : "none",
        borderBottom: solid || mobileOpen
          ? "1px solid var(--color-mkt-ink-line)"
          : "1px solid transparent",
      }}
    >
      <Container className="flex h-18 items-center justify-between py-4">
        <a
          href="#top"
          className="font-display text-lg tracking-tight text-canvas"
          onClick={() => setMobileOpen(false)}
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

          {/* Below `sm`, "Start free" alone was the only thing reachable --
              there was no way to get to the nav links, "See a sample
              report", or "Sign in" at all, since those buttons are hidden
              above. This toggle is what replaces them on a phone; "Start
              free" itself stays visible in the collapsed header since it's
              the primary conversion action. */}
          <button
            type="button"
            aria-expanded={mobileOpen}
            aria-controls="mobile-nav-panel"
            aria-label={mobileOpen ? "Close menu" : "Open menu"}
            onClick={() => setMobileOpen((v) => !v)}
            className="flex h-10 w-10 items-center justify-center rounded-full text-canvas sm:hidden"
          >
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" aria-hidden="true">
              {mobileOpen ? (
                <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              ) : (
                <path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              )}
            </svg>
          </button>
        </div>
      </Container>

      <AnimatePresence>
        {mobileOpen && (
          <motion.div
            id="mobile-nav-panel"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3, ease: EASE }}
            className="overflow-hidden sm:hidden"
            style={{ backgroundColor: "rgba(11,12,14,0.96)", borderTop: "1px solid var(--color-mkt-ink-line)" }}
          >
            <Container className="flex flex-col gap-1 py-4">
              {LINKS.map((link) => (
                <a
                  key={link.href}
                  href={link.href}
                  onClick={() => setMobileOpen(false)}
                  className="rounded-lg px-2 py-3 text-base text-on-mkt-ink-muted transition-colors duration-300 hover:text-canvas"
                >
                  {link.label}
                </a>
              ))}
              <a
                href="#deliverables"
                onClick={() => setMobileOpen(false)}
                className="rounded-lg px-2 py-3 text-base text-on-mkt-ink-muted transition-colors duration-300 hover:text-canvas"
              >
                See a sample report
              </a>
              {/* Button ignores onClick when href is set (it renders a plain
                  Link) -- no explicit close needed here anyway, since both
                  of these navigate to /login, a route outside this layout
                  entirely, which unmounts this panel's state regardless. */}
              <div className="mt-3 flex flex-col gap-3 border-t border-mkt-ink-line pt-4">
                <Button href={APP_LOGIN_URL} variant="secondary">
                  Sign in
                </Button>
                <Button href={APP_LOGIN_URL} variant="primary">
                  Start free
                </Button>
              </div>
            </Container>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.header>
  );
}
