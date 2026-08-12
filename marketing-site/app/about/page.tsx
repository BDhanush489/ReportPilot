import type { Metadata } from "next";
import { Nav } from "@/components/Nav";
import { Footer } from "@/components/Footer";
import { Container } from "@/components/ui/Container";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { CONTACT_HREF } from "@/lib/links";

export const metadata: Metadata = {
  title: "About — ReportPilot",
  description: "Why ReportPilot exists, and the one rule everything else is built around.",
};

export default function AboutPage() {
  return (
    <>
      <Nav />
      <main className="flex flex-1 flex-col">
        <section className="bg-ink py-28 md:py-36">
          <Container>
            <div className="flex max-w-2xl flex-col gap-7">
              <Eyebrow>About</Eyebrow>
              <h1 className="font-display text-3xl leading-[1.12] tracking-tight text-canvas md:text-5xl">
                Reporting agencies shouldn&rsquo;t have to choose between
                <span className="text-gold"> fast</span> and{" "}
                <span className="text-gold">correct</span>.
              </h1>
              <p className="text-base leading-relaxed text-on-ink-muted md:text-lg">
                Client reporting is usually one of two things: a template
                someone fills in by hand every month, or an AI tool that
                writes something plausible-sounding and hopes the numbers
                hold up. ReportPilot started from a narrower, stricter rule —
                the two jobs stay separate. A deterministic pipeline computes
                every metric and checks its own work before anything ships;
                a language model only ever writes the prose around numbers
                it never touched. That&rsquo;s the whole idea. Everything
                else — templates, exports, connectors — is built on top of
                that one boundary, not around it.
              </p>
              <p className="text-base leading-relaxed text-on-ink-muted md:text-lg">
                We&rsquo;re early. If you&rsquo;re evaluating ReportPilot for
                your agency or in-house team and have questions this site
                doesn&rsquo;t answer, reach out directly —{" "}
                <a href={CONTACT_HREF} className="text-canvas underline decoration-ink-line underline-offset-4 hover:text-gold">
                  we read every message ourselves
                </a>
                .
              </p>
            </div>
          </Container>
        </section>
      </main>
      <Footer />
    </>
  );
}
