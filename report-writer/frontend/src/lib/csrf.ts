const CSRF_COOKIE_NAME = "rp_csrf";

/**
 * Reads the rp_csrf cookie (non-httpOnly on purpose -- see app/auth.py's
 * _set_csrf_cookie) so it can be echoed back as X-CSRF-Token. A page on
 * another origin can trick a browser into sending the session cookie
 * automatically, but it can't read this cookie's value to forge a
 * matching header -- that's the entire double-submit protection.
 */
function getCsrfToken(): string | null {
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
