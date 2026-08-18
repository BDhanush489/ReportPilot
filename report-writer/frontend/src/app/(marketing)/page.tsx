import { Nav } from "@/components/marketing/Nav";
import { Hero } from "@/components/marketing/hero/Hero";
import { TrustSection } from "@/components/marketing/trust/TrustSection";
import { HowItWorks } from "@/components/marketing/how-it-works/HowItWorks";
import { AudiencePaths } from "@/components/marketing/audience/AudiencePaths";
import { DeliverablesShowcase } from "@/components/marketing/deliverables/DeliverablesShowcase";
import { SocialProof } from "@/components/marketing/social-proof/SocialProof";
import { Pricing } from "@/components/marketing/pricing/Pricing";
import { FAQ } from "@/components/marketing/pricing/FAQ";
import { FinalCta } from "@/components/marketing/FinalCta";
import { Footer } from "@/components/marketing/Footer";

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
