"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { withCsrf } from "@/lib/csrf";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export type AuthUser = {
  id: string;
  email: string;
  name: string;
  avatar_url: string | null;
  is_platform_admin: boolean;
};
export type AuthTenant = { id: string; name: string; slug: string; plan: string };

type AuthState = {
  user: AuthUser | null;
  tenant: AuthTenant | null;
  role: string | null;
  /** Display label for tenant.plan, e.g. "Agency" -- see app/plans.py. */
  planLabel: string | null;
  /** True only until the first /api/auth/me check resolves. */
  loading: boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [tenant, setTenant] = useState<AuthTenant | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [planLabel, setPlanLabel] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/auth/me`, { credentials: "include" });
      if (res.ok) {
        const data: { user: AuthUser; tenant: AuthTenant; role: string | null; plan: string } = await res.json();
        setUser(data.user);
        setTenant(data.tenant);
        setRole(data.role);
        setPlanLabel(data.plan);
      } else {
        // Not logged in (or session expired) -- the correct, expected
        // steady state for an anonymous visitor, not an error to surface.
        setUser(null);
        setTenant(null);
        setRole(null);
        setPlanLabel(null);
      }
    } catch {
      // Backend unreachable -- treat the same as "not logged in" rather
      // than leaving the UI stuck on a loading spinner forever.
      setUser(null);
      setTenant(null);
      setRole(null);
      setPlanLabel(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await fetch(`${API_BASE}/api/auth/logout`, withCsrf({ method: "POST", credentials: "include" }));
    } finally {
      setUser(null);
      setTenant(null);
      setRole(null);
      setPlanLabel(null);
    }
  }, []);

  // On-mount fetch-then-setState, same pattern/rationale as page.tsx's
  // loadRecent()/loadTemplates() -- not a synchronous render-loop.
  /* eslint-disable react-hooks/set-state-in-effect, react-hooks/exhaustive-deps */
  useEffect(() => {
    refresh();
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect, react-hooks/exhaustive-deps */

  return (
    <AuthContext.Provider value={{ user, tenant, role, planLabel, loading, refresh, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth() must be used within <AuthProvider>.");
  return ctx;
}
