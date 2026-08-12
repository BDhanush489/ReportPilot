import { Nav } from "@/components/Nav";
import { Hero } from "@/components/hero/Hero";
import { TrustSection } from "@/components/trust/TrustSection";
import { HowItWorks } from "@/components/how-it-works/HowItWorks";
import { AudiencePaths } from "@/components/audience/AudiencePaths";
import { DeliverablesShowcase } from "@/components/deliverables/DeliverablesShowcase";
import { SocialProof } from "@/components/social-proof/SocialProof";
import { Pricing } from "@/components/pricing/Pricing";
import { FAQ } from "@/components/pricing/FAQ";
import { FinalCta } from "@/components/FinalCta";
import { Footer } from "@/components/Footer";

export default function Home() {
  return (
    <>
      <Nav />
      <main className="flex flex-1 flex-col">
        <Hero />
        <TrustSection />
        <HowItWorks />
        <AudiencePaths />
        <DeliverablesShowcase />
        <SocialProof />
        <Pricing />
        <FAQ />
        <FinalCta />
      </main>
      <Footer />
    </>
  );
}
