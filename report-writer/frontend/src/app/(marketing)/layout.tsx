import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./marketing.css";

export const metadata: Metadata = {
  title: "ReportPilot — Client-ready reports, verified.",
  description:
    "ReportPilot turns raw data into a branded, client-ready report or dashboard in minutes — with every number machine-verified before it ships.",
};

// Fraunces (the display face) is already loaded globally by the root
// layout (../layout.tsx) -- same font/config marketing-site used, no need
// to load it twice. Only the body face needs its own instance: this site
// pairs Fraunces with Inter, the app pairs it with Manrope.
const inter = Inter({
  subsets: ["latin"],
  variable: "--font-mkt-inter",
  display: "swap",
});

// .marketing-theme (see marketing.css) scopes the dark ink/canvas palette
// to just this route group -- /app, /login, /admin, /data-sources keep the
// root layout's own light theme untouched.
export default function MarketingLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className={`marketing-theme min-h-full flex flex-col ${inter.variable}`}>
      {children}
    </div>
  );
}
