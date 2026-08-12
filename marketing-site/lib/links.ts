// Where the actual product lives -- a separate Next.js app (report-writer/
// frontend), not part of this project. Configurable because the two are
// meant to run on different origins (this site is the public marketing
// surface; the app is what a signed-in user actually uses) -- in local dev
// that's different ports on localhost, in production likely a different
// subdomain (e.g. app.reportpilot.com vs the marketing apex domain).
const APP_URL = process.env.NEXT_PUBLIC_APP_URL || "http://localhost:3000";

// Every "Start free" CTA across the site -- Google OAuth login IS the
// signup flow (first sign-in auto-creates a workspace), there's no
// separate signup form to link to.
export const APP_LOGIN_URL = `${APP_URL}/login`;

// The In-house tier is "Talk to us," not self-serve -- a real sales
// contact, not the login flow.
export const CONTACT_HREF = "mailto:hello@reportpilot.example?subject=In-house%20plan";
