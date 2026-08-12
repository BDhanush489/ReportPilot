import type { Metadata } from "next";
import { Fraunces, Manrope } from "next/font/google";
import { AuthProvider } from "@/lib/auth-context";
import "./globals.css";

// Fraunces (warm, editorial serif) for headings/display -- a report is an
// editorial document, and this is the one place a serif differentiates the
// product from the generic geometric-sans-everywhere SaaS look. Manrope for
// UI/body text: a distinct, considered grotesk (not Inter) that stays crisp
// at small sizes for form chrome and dense report text alike.
const fraunces = Fraunces({
  subsets: ["latin"],
  variable: "--font-display",
  axes: ["opsz", "SOFT", "WONK"],
  display: "swap",
});

const manrope = Manrope({
  subsets: ["latin"],
  variable: "--font-body",
  display: "swap",
});

export const metadata: Metadata = {
  title: "ReportPilot — AI Report Writer for Agencies",
  description: "Turn raw client data into a branded, client-ready report in minutes.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`h-full antialiased ${fraunces.variable} ${manrope.variable}`}>
      <body className="min-h-full flex flex-col">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
