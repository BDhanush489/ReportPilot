import type { Metadata } from "next";
import { fraunces, inter } from "@/lib/fonts";
import "./globals.css";

export const metadata: Metadata = {
  title: "ReportPilot — Client-ready reports, verified.",
  description:
    "ReportPilot turns raw data into a branded, client-ready report or dashboard in minutes — with every number machine-verified before it ships.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${fraunces.variable} ${inter.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-ink text-canvas">
        {children}
      </body>
    </html>
  );
}
