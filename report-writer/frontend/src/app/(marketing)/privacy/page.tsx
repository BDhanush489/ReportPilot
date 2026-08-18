import type { Metadata } from "next";
import { Nav } from "@/components/marketing/Nav";
import { Footer } from "@/components/marketing/Footer";
import { Container } from "@/components/marketing/ui/Container";
import { Eyebrow } from "@/components/marketing/ui/Eyebrow";
import { CONTACT_HREF } from "@/lib/marketing/links";

export const metadata: Metadata = {
  title: "Privacy Policy — ReportPilot",
  description: "Where things stand on data handling while ReportPilot is in early access.",
};

export default function PrivacyPage() {
  return (
    <>
      <Nav />
      <main className="flex flex-1 flex-col">
        <section className="bg-mkt-ink py-28 md:py-36">
          <Container>
            <div className="flex max-w-2xl flex-col gap-7">
              <Eyebrow>Privacy Policy</Eyebrow>
              <h1 className="font-display text-3xl leading-[1.12] tracking-tight text-canvas md:text-5xl">
                We haven&rsquo;t finalized this yet — and we&rsquo;d rather
                say that than post something we don&rsquo;t mean.
              </h1>
              <p className="text-base leading-relaxed text-on-mkt-ink-muted md:text-lg">
                ReportPilot is in early access, and this page is a
                placeholder rather than a reviewed, binding policy. Publishing
                boilerplate legal text before it reflects how the product
                actually handles your data felt worse than leaving this
                honest instead.
              </p>
              <p className="text-base leading-relaxed text-on-mkt-ink-muted md:text-lg">
                If you&rsquo;re evaluating ReportPilot and need to understand
                exactly what happens to your data — client files, connector
                credentials, report content — before you connect anything,{" "}
                <a href={CONTACT_HREF} className="text-canvas underline decoration-mkt-ink-line underline-offset-4 hover:text-gold">
                  ask us directly
                </a>
                . We&rsquo;d rather answer specifically than have you guess
                from a generic template.
              </p>
            </div>
          </Container>
        </section>
      </main>
      <Footer />
    </>
  );
}
