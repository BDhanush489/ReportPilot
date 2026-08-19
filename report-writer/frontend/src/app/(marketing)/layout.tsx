import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans, Newsreader } from "next/font/google";
import "./marketing.css";

export const metadata: Metadata = {
  title: "ReportPilot — Client-ready reports, verified.",
  description:
    "ReportPilot turns raw data into a branded, client-ready report or dashboard in minutes — with every number machine-verified before it ships.",
};

// A deliberately different pairing from the app's own Fraunces + Manrope
// (../layout.tsx) -- this page's voice is closer to an audited document
// than a generic dashboard. Newsreader: a text serif built for long-form
// reading at a range of optical sizes, standing in for "this is what your
// report looks like" rather than a decorative display face. Plex Sans:
// technical, unfussy body text. Plex Mono: the one place the page borrows
// a ledger's vocabulary -- verification codes, tabular figures.
const newsreader = Newsreader({
  subsets: ["latin"],
  variable: "--font-mkt-newsreader",
  style: ["normal", "italic"],
  display: "swap",
});

const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-mkt-plex-sans",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mkt-plex-mono",
  display: "swap",
});

// .marketing-theme (see marketing.css) scopes this page's tokens to just
// this route group -- /app, /login, /admin, /data-sources keep the root
// layout's own light theme untouched.
export default function MarketingLayout({ children }: { children: React.ReactNode }) {
  return (
    <div
      className={`marketing-theme min-h-full flex flex-col ${newsreader.variable} ${plexSans.variable} ${plexMono.variable}`}
    >
      {children}
    </div>
  );
}
