"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

// A real top-level navigation, not a fetch: Google redirects back to the
// BACKEND's /api/auth/google/callback (that's what's registered in Google
// Cloud Console), which sets the session cookie and redirects here to the
// frontend -- this page never sees or handles an authorization code itself.
export default function LoginPage() {
  const { user, loading } = useAuth();
  const router = useRouter();

  // Already signed in (e.g. a bookmarked /login, or landing here after the
  // marketing site's "Sign in" link) -- send them straight to the tool
  // instead of showing the button again.
  /* eslint-disable react-hooks/exhaustive-deps */
  useEffect(() => {
    if (!loading && user) router.replace("/app");
  }, [loading, user]);
  /* eslint-enable react-hooks/exhaustive-deps */

  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden bg-background px-6">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={{
          background: "radial-gradient(60% 50% at 50% 30%, rgba(42,120,214,0.08), transparent 70%)",
        }}
      />
      <div className="relative w-full max-w-sm rounded-2xl border border-gridline bg-surface p-8 text-center shadow-card transition-shadow duration-300 hover:shadow-card-hover">
        <div className="font-display mx-auto mb-5 flex h-10 w-10 items-center justify-center rounded-lg bg-brand text-sm font-bold text-white shadow-[0_1px_2px_rgba(11,11,11,0.08),0_4px_10px_-4px_rgba(42,120,214,0.5)]">
          R
        </div>
        <h1 className="font-display text-xl font-medium text-ink">Sign in to ReportPilot</h1>
        <p className="mt-2 text-sm leading-relaxed text-ink-secondary">
          Use your Google account to access your agency&rsquo;s workspace. First sign-in creates it automatically.
        </p>
        <a
          href={`${API_BASE}/api/auth/google/login`}
          className="mt-6 flex w-full items-center justify-center gap-2.5 rounded-lg border border-gridline bg-white px-4 py-2.5 text-sm font-medium text-ink shadow-sm transition-all duration-200 hover:-translate-y-px hover:border-black/15 hover:shadow-md focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
        >
          <GoogleIcon />
          Continue with Google
        </a>
        <Link href="/#pricing" className="mt-5 block text-xs font-medium text-ink-muted transition-colors hover:text-ink">
          View pricing
        </Link>
      </div>
    </div>
  );
}

function GoogleIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4 shrink-0" aria-hidden="true">
      <path fill="#4285F4" d="M19.6 10.23c0-.68-.06-1.36-.18-2H10v3.79h5.4a4.62 4.62 0 01-2 3.03v2.5h3.23c1.9-1.75 2.97-4.33 2.97-7.32z" />
      <path fill="#34A853" d="M10 20c2.7 0 4.96-.89 6.62-2.42l-3.23-2.5c-.9.6-2.05.96-3.39.96-2.6 0-4.8-1.76-5.59-4.12H1.06v2.59A10 10 0 0010 20z" />
      <path fill="#FBBC05" d="M4.41 11.92A5.99 5.99 0 014.09 10c0-.67.11-1.32.32-1.92V5.49H1.06A10 10 0 000 10c0 1.61.39 3.14 1.06 4.51z" />
      <path fill="#EA4335" d="M10 3.98c1.47 0 2.79.5 3.83 1.5l2.87-2.87A9.96 9.96 0 0010 0 10 10 0 001.06 5.49l3.35 2.59C5.2 5.73 7.4 3.98 10 3.98z" />
    </svg>
  );
}
