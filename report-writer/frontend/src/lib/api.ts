import { withCsrf } from "@/lib/csrf";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type FetchJsonOptions = RequestInit & {
  /** ms before the request is aborted. Default 30s. */
  timeoutMs?: number;
  /** Retry idempotent requests this many times on network failure / 5xx. Default 0. */
  retries?: number;
};

/**
 * fetch wrapper: JSON parsing, typed errors, timeout, optional retry with
 * exponential backoff. Pass an external `signal` to allow caller-side aborts
 * (both signals are honored).
 */
export async function fetchJson<T>(
  path: string,
  opts: FetchJsonOptions = {}
): Promise<T> {
  const { timeoutMs = 30_000, retries = 0, signal: outerSignal, ...init } = opts;

  let lastError: unknown;
  for (let attempt = 0; attempt <= retries; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const onOuterAbort = () => controller.abort();
    outerSignal?.addEventListener("abort", onOuterAbort);
    if (outerSignal?.aborted) controller.abort();

    try {
      // credentials: "include" -- every request carries the session cookie
      // cross-origin by default; withCsrf() echoes rp_csrf back as
      // X-CSRF-Token for non-GET requests (a no-op before login / for GETs).
      const res = await fetch(
        `${API_BASE}${path}`,
        withCsrf({ credentials: "include", ...init, signal: controller.signal })
      );

      if (!res.ok) {
        let detail = `Request failed (${res.status})`;
        try {
          const body = await res.json();
          if (typeof body?.detail === "string") detail = body.detail;
        } catch {
          /* non-JSON error body */
        }
        // Retry only on 5xx; 4xx is the caller's problem.
        if (res.status >= 500 && attempt < retries) {
          lastError = new ApiError(detail, res.status);
          await backoff(attempt);
          continue;
        }
        throw new ApiError(detail, res.status);
      }

      if (res.status === 204) return undefined as T;
      return (await res.json()) as T;
    } catch (e) {
      if (e instanceof ApiError) throw e;
      // Caller-initiated abort: propagate immediately, never retry.
      if (outerSignal?.aborted) throw new DOMException("Aborted", "AbortError");
      // Timeout or network failure — retry if we have budget.
      lastError = e;
      if (attempt < retries) {
        await backoff(attempt);
        continue;
      }
      if (e instanceof DOMException && e.name === "AbortError") {
        throw new ApiError("The request timed out. Is the backend running?", 0);
      }
      throw new ApiError(
        "Could not reach the server. Check that the backend is running.",
        0
      );
    } finally {
      clearTimeout(timer);
      outerSignal?.removeEventListener("abort", onOuterAbort);
    }
  }
  throw lastError instanceof Error
    ? lastError
    : new ApiError("Request failed.", 0);
}

function backoff(attempt: number) {
  return new Promise((r) => setTimeout(r, 400 * 2 ** attempt));
}

export function errorMessage(e: unknown, fallback = "Something went wrong.") {
  return e instanceof Error ? e.message : fallback;
}
