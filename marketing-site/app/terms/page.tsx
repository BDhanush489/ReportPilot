import type { Metadata } from "next";
import { Nav } from "@/components/Nav";
import { Footer } from "@/components/Footer";
import { Container } from "@/components/ui/Container";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { CONTACT_HREF } from "@/lib/links";

export const metadata: Metadata = {
  title: "Terms of Service — ReportPilot",
  description: "Where things stand on terms of service while ReportPilot is in early access.",
};

export default function TermsPage() {
  return (
    <>
      <Nav />
      <main className="flex flex-1 flex-col">
        <section className="bg-ink py-28 md:py-36">
          <Container>
            <div className="flex max-w-2xl flex-col gap-7">
              <Eyebrow>Terms of Service</Eyebrow>
              <h1 className="font-display text-3xl leading-[1.12] tracking-tight text-canvas md:text-5xl">
                Same as our Privacy Policy — not finalized yet, and we&rsquo;d
                rather tell you that than fake it.
              </h1>
              <p className="text-base leading-relaxed text-on-ink-muted md:text-lg">
                ReportPilot is in early access. This page is a placeholder,
                not a reviewed terms-of-service agreement — we&rsquo;d rather
                leave it honestly blank than publish generic legal
                boilerplate that doesn&rsquo;t actually govern anything yet.
              </p>
              <p className="text-base leading-relaxed text-on-ink-muted md:text-lg">
                If acceptable use, account terms, or liability are a blocker
                for your team before you connect real client data,{" "}
                <a href={CONTACT_HREF} className="text-canvas underline decoration-ink-line underline-offset-4 hover:text-gold">
                  talk to us first
                </a>
                — we&rsquo;ll work it out directly rather than point you at a
                template.
              </p>
            </div>
          </Container>
        </section>
      </main>
      <Footer />
    </>
  );
}
