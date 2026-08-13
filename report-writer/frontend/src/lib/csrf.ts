const CSRF_COOKIE_NAME = "rp_csrf";

/**
 * In production, frontend (*.vercel.app) and backend (*.onrender.com) are
 * unrelated origins with no shared parent domain -- document.cookie on this
 * page can only ever see cookies belonging to THIS origin, never one the
 * backend set. So GET /api/auth/me now hands the token back in its JSON
 * body too (see app/auth.py's `me` route); setCsrfToken caches it here once
 * auth-context.tsx's refresh() reads it. This doesn't weaken the
 * double-submit protection: a forged cross-site request can't read that
 * response either (CORS only allows settings.frontend_origin_list), same as
 * it could never read the cookie.
 */
let inMemoryCsrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
  inMemoryCsrfToken = token;
}

/**
 * Reads the rp_csrf cookie (non-httpOnly on purpose -- see app/auth.py's
 * _set_csrf_cookie) so it can be echoed back as X-CSRF-Token. Falls back to
 * this for same-origin local dev (localhost:3000 -> localhost:8000 counts
 * as same-site, so the cookie IS readable there) -- setCsrfToken's cached
 * value takes priority whenever it's been set.
 */
function getCsrfToken(): string | null {
  if (inMemoryCsrfToken) return inMemoryCsrfToken;
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(
    new RegExp(`(?:^|; )${CSRF_COOKIE_NAME}=([^;]*)`)
  );
  return match ? decodeURIComponent(match[1]) : null;
}

/**
 * Merges an X-CSRF-Token header into `init` for a non-GET request. Only
 * meaningful once logged in (the cookie is minted at /google/callback); a
 * request made before that simply won't have the header, and the backend
 * only enforces it for session-cookie-authenticated requests anyway.
 */
export function withCsrf(init: RequestInit = {}): RequestInit {
  const token = getCsrfToken();
  if (!token) return init;
  return {
    ...init,
    headers: { ...(init.headers || {}), "X-CSRF-Token": token },
  };
}
