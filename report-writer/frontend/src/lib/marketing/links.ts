// The marketing pages and the app itself are one Next.js app now (same
// origin), so this is just a relative path -- no NEXT_PUBLIC_APP_URL/
// cross-origin wiring needed.
//
// Every "Start free" CTA across the site -- Google OAuth login IS the
// signup flow (first sign-in auto-creates a workspace), there's no
// separate signup form to link to.
export const APP_LOGIN_URL = "/login";

// The In-house tier is "Talk to us," not self-serve -- a real sales
// contact, not the login flow.
export const CONTACT_HREF = "mailto:hello@reportpilot.example?subject=In-house%20plan";
