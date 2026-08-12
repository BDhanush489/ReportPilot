"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { withCsrf } from "@/lib/csrf";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

type AdminUserRow = {
  id: string;
  email: string;
  name: string;
  is_platform_admin: boolean;
  created_at: string;
  tenant: { id: string; name: string; plan: string } | null;
  role: string | null;
};

type AdminTenantRow = {
  id: string;
  name: string;
  slug: string;
  plan: string;
  plan_label: string;
  member_count: number;
  active_clients: number;
  max_active_clients: number | null;
  created_at: string;
};

const PLAN_OPTIONS = [
  { id: "solo", label: "Solo" },
  { id: "agency", label: "Agency" },
  { id: "inhouse", label: "In-house" },
];

export default function AdminPage() {
  const { user, loading: authLoading } = useAuth();
  const router = useRouter();

  const [users, setUsers] = useState<AdminUserRow[]>([]);
  const [tenants, setTenants] = useState<AdminTenantRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [usersRes, tenantsRes] = await Promise.all([
        fetch(`${API_BASE}/api/admin/users`, { credentials: "include" }),
        fetch(`${API_BASE}/api/admin/tenants`, { credentials: "include" }),
      ]);
      if (!usersRes.ok || !tenantsRes.ok) throw new Error("Could not load admin data.");
      const usersData: { users: AdminUserRow[] } = await usersRes.json();
      const tenantsData: { tenants: AdminTenantRow[] } = await tenantsRes.json();
      setUsers(usersData.users);
      setTenants(tenantsData.tenants);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }, []);

  /* eslint-disable react-hooks/exhaustive-deps */
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login");
      return;
    }
    if (!authLoading && user && !user.is_platform_admin) {
      router.replace("/app");
      return;
    }
    if (user?.is_platform_admin) {
      /* eslint-disable react-hooks/set-state-in-effect */
      loadAll();
      /* eslint-enable react-hooks/set-state-in-effect */
    }
  }, [authLoading, user]);
  /* eslint-enable react-hooks/exhaustive-deps */

  async function togglePlatformAdmin(row: AdminUserRow) {
    setBusyId(row.id);
    try {
      const action = row.is_platform_admin ? "demote" : "promote";
      const res = await fetch(
        `${API_BASE}/api/admin/users/${row.id}/${action}`,
        withCsrf({ method: "POST", credentials: "include" })
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Request failed (${res.status})`);
      }
      await loadAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update that user.");
    } finally {
      setBusyId(null);
    }
  }

  async function changePlan(tenantId: string, plan: string) {
    setBusyId(tenantId);
    try {
      const res = await fetch(
        `${API_BASE}/api/admin/tenants/${tenantId}/plan`,
        withCsrf({
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ plan }),
        })
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Request failed (${res.status})`);
      }
      await loadAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update that tenant's plan.");
    } finally {
      setBusyId(null);
    }
  }

  // --- Clients (the schedules that back "active clients / cap") --------
  const [expandedTenantId, setExpandedTenantId] = useState<string | null>(null);
  const [clientsByTenant, setClientsByTenant] = useState<Record<string, string[]>>({});
  const [clientsLoading, setClientsLoading] = useState<string | null>(null);
  const [clientsError, setClientsError] = useState<Record<string, string>>({});
  const [newClientId, setNewClientId] = useState<Record<string, string>>({});
  const [clientBusy, setClientBusy] = useState<string | null>(null);

  async function loadClients(tenantId: string) {
    setClientsLoading(tenantId);
    try {
      const res = await fetch(`${API_BASE}/api/admin/tenants/${tenantId}/clients`, { credentials: "include" });
      if (!res.ok) throw new Error("Could not load this workspace's clients.");
      const data: { client_ids: string[] } = await res.json();
      setClientsByTenant((s) => ({ ...s, [tenantId]: data.client_ids }));
    } catch (e) {
      setClientsError((s) => ({ ...s, [tenantId]: e instanceof Error ? e.message : "Something went wrong." }));
    } finally {
      setClientsLoading(null);
    }
  }

  function toggleClients(tenantId: string) {
    const opening = expandedTenantId !== tenantId;
    setExpandedTenantId(opening ? tenantId : null);
    if (opening && !clientsByTenant[tenantId]) loadClients(tenantId);
  }

  async function addClient(tenantId: string) {
    setClientBusy(tenantId);
    setClientsError((s) => ({ ...s, [tenantId]: "" }));
    try {
      const clientId = (newClientId[tenantId] || "").trim();
      const res = await fetch(
        `${API_BASE}/api/admin/tenants/${tenantId}/clients`,
        withCsrf({
          method: "POST", credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(clientId ? { client_id: clientId } : {}),
        })
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Request failed (${res.status})`);
      }
      setNewClientId((s) => ({ ...s, [tenantId]: "" }));
      await Promise.all([loadClients(tenantId), loadAll()]);
    } catch (e) {
      setClientsError((s) => ({ ...s, [tenantId]: e instanceof Error ? e.message : "Could not add that client." }));
    } finally {
      setClientBusy(null);
    }
  }

  async function removeClient(tenantId: string, clientId: string) {
    setClientBusy(tenantId);
    setClientsError((s) => ({ ...s, [tenantId]: "" }));
    try {
      const res = await fetch(
        `${API_BASE}/api/admin/tenants/${tenantId}/clients/${encodeURIComponent(clientId)}`,
        withCsrf({ method: "DELETE", credentials: "include" })
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Request failed (${res.status})`);
      }
      await Promise.all([loadClients(tenantId), loadAll()]);
    } catch (e) {
      setClientsError((s) => ({ ...s, [tenantId]: e instanceof Error ? e.message : "Could not remove that client." }));
    } finally {
      setClientBusy(null);
    }
  }

  if (authLoading || !user || !user.is_platform_admin) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <span className="h-6 w-6 animate-spin rounded-full border-2 border-brand/30 border-t-brand" />
      </div>
    );
  }

  return (
    <div className="flex-1 bg-background">
      <header className="sticky top-0 z-10 border-b border-gridline bg-surface/90 backdrop-blur-sm">
        <div className="mx-auto max-w-6xl px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div
              className="flex h-8 w-8 items-center justify-center rounded-lg text-sm font-bold text-white shadow-sm"
              style={{ backgroundColor: "#2a78d6", fontFamily: "var(--font-display)" }}
            >
              R
            </div>
            <span className="font-display font-semibold tracking-tight text-ink">Platform Admin</span>
          </div>
          <Link href="/app" className="text-xs font-medium text-ink-muted transition-colors hover:text-ink">
            Back to app
          </Link>
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-6 py-10 space-y-10">
        <div>
          <h1 className="font-display text-2xl font-medium text-ink">Access control</h1>
          <p className="mt-1.5 text-sm text-ink-secondary">
            Every user and workspace across the whole app. Platform admin status is separate from,
            and above, per-workspace ownership — it never grants access to another tenant&rsquo;s
            reports or data.
          </p>
        </div>

        {error && (
          <div className="rounded-lg border-l-4 px-3.5 py-3 text-sm leading-relaxed"
            style={{ borderColor: "#d03b3b", backgroundColor: "#fdecec", color: "#7a1f1f" }}>
            {error}
          </div>
        )}

        <section className="rounded-2xl border border-gridline bg-surface shadow-card overflow-hidden">
          <div className="border-b border-gridline px-5 py-3.5">
            <h2 className="text-[13px] font-semibold uppercase tracking-wide text-ink-secondary">
              Users ({users.length})
            </h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gridline text-left text-xs text-ink-muted">
                  <th className="px-5 py-2.5 font-medium">Email</th>
                  <th className="px-5 py-2.5 font-medium">Name</th>
                  <th className="px-5 py-2.5 font-medium">Workspace</th>
                  <th className="px-5 py-2.5 font-medium">Role</th>
                  <th className="px-5 py-2.5 font-medium">Platform admin</th>
                  <th className="px-5 py-2.5 font-medium" />
                </tr>
              </thead>
              <tbody>
                {users.map((row) => (
                  <tr key={row.id} className="border-b border-gridline last:border-0">
                    <td className="px-5 py-2.5 text-ink">{row.email}</td>
                    <td className="px-5 py-2.5 text-ink-secondary">{row.name || "—"}</td>
                    <td className="px-5 py-2.5 text-ink-secondary">{row.tenant?.name || "—"}</td>
                    <td className="px-5 py-2.5 text-ink-secondary">{row.role || "—"}</td>
                    <td className="px-5 py-2.5">
                      {row.is_platform_admin ? (
                        <span className="rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide"
                          style={{ backgroundColor: "#eef4fc", color: "#164a85", border: "1px solid #2a78d655" }}>
                          Admin
                        </span>
                      ) : (
                        <span className="text-xs text-ink-muted">—</span>
                      )}
                    </td>
                    <td className="px-5 py-2.5 text-right">
                      <button
                        type="button"
                        disabled={busyId === row.id || row.id === user.id}
                        onClick={() => togglePlatformAdmin(row)}
                        title={row.id === user.id ? "You cannot change your own admin status" : undefined}
                        className="rounded-md border border-gridline px-2.5 py-1 text-xs font-medium text-ink-secondary transition-colors hover:border-baseline hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        {row.is_platform_admin ? "Demote" : "Promote"}
                      </button>
                    </td>
                  </tr>
                ))}
                {!loading && users.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-5 py-6 text-center text-xs text-ink-muted">No users yet.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="rounded-2xl border border-gridline bg-surface shadow-card overflow-hidden">
          <div className="border-b border-gridline px-5 py-3.5">
            <h2 className="text-[13px] font-semibold uppercase tracking-wide text-ink-secondary">
              Workspaces ({tenants.length})
            </h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gridline text-left text-xs text-ink-muted">
                  <th className="px-5 py-2.5 font-medium">Name</th>
                  <th className="px-5 py-2.5 font-medium">Members</th>
                  <th className="px-5 py-2.5 font-medium">Active clients</th>
                  <th className="px-5 py-2.5 font-medium">Plan</th>
                  <th className="px-5 py-2.5 font-medium" />
                </tr>
              </thead>
              <tbody>
                {tenants.map((row) => {
                  const expanded = expandedTenantId === row.id;
                  const clientIds = clientsByTenant[row.id] || [];
                  return (
                    <Fragment key={row.id}>
                      <tr className="border-b border-gridline last:border-0">
                        <td className="px-5 py-2.5 text-ink">{row.name}</td>
                        <td className="px-5 py-2.5 tabular-nums text-ink-secondary">{row.member_count}</td>
                        <td className="px-5 py-2.5 tabular-nums text-ink-secondary">
                          {row.active_clients}
                          {row.max_active_clients !== null ? ` / ${row.max_active_clients}` : " (unlimited)"}
                        </td>
                        <td className="px-5 py-2.5">
                          <select
                            value={row.plan}
                            disabled={busyId === row.id}
                            onChange={(e) => changePlan(row.id, e.target.value)}
                            className="rounded-md border border-gridline bg-white px-2 py-1 text-xs text-ink outline-none focus:border-brand focus:ring-2 focus:ring-brand/10 disabled:opacity-40"
                          >
                            {PLAN_OPTIONS.map((p) => (
                              <option key={p.id} value={p.id}>{p.label}</option>
                            ))}
                          </select>
                        </td>
                        <td className="px-5 py-2.5 text-right">
                          <button
                            type="button"
                            onClick={() => toggleClients(row.id)}
                            className="rounded-md border border-gridline px-2.5 py-1 text-xs font-medium text-ink-secondary transition-colors hover:border-baseline hover:text-ink"
                          >
                            {expanded ? "Hide clients" : "Manage clients"}
                          </button>
                        </td>
                      </tr>
                      {expanded && (
                        <tr className="border-b border-gridline last:border-0 bg-black/1.5">
                          <td colSpan={5} className="px-5 py-4">
                            {clientsLoading === row.id && clientIds.length === 0 ? (
                              <span className="text-xs text-ink-muted">Loading…</span>
                            ) : (
                              <>
                                {clientIds.length === 0 ? (
                                  <p className="text-xs text-ink-muted">No active clients yet.</p>
                                ) : (
                                  <ul className="flex flex-wrap gap-1.5">
                                    {clientIds.map((cid) => (
                                      <li key={cid}
                                        className="flex items-center gap-1.5 rounded-full border border-gridline bg-white py-1 pl-2.5 pr-1.5 text-xs text-ink-secondary">
                                        {cid}
                                        <button
                                          type="button"
                                          disabled={clientBusy === row.id}
                                          onClick={() => removeClient(row.id, cid)}
                                          title={`Remove ${cid}`}
                                          className="flex h-4 w-4 items-center justify-center rounded-full text-ink-muted transition-colors hover:bg-black/5 hover:text-[#a01f1f] disabled:cursor-not-allowed disabled:opacity-40"
                                        >
                                          ×
                                        </button>
                                      </li>
                                    ))}
                                  </ul>
                                )}
                                <div className="mt-3 flex items-center gap-2">
                                  <input
                                    value={newClientId[row.id] || ""}
                                    onChange={(e) => setNewClientId((s) => ({ ...s, [row.id]: e.target.value }))}
                                    placeholder="client id (optional — auto-generated if blank)"
                                    disabled={clientBusy === row.id}
                                    className="w-72 rounded-md border border-gridline bg-white px-2.5 py-1.5 text-xs text-ink outline-none focus:border-brand focus:ring-2 focus:ring-brand/10 disabled:opacity-40"
                                  />
                                  <button
                                    type="button"
                                    disabled={clientBusy === row.id}
                                    onClick={() => addClient(row.id)}
                                    className="rounded-md border border-gridline bg-white px-2.5 py-1.5 text-xs font-medium text-ink-secondary transition-colors hover:border-baseline hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
                                  >
                                    {clientBusy === row.id ? "Working…" : "+ Add client"}
                                  </button>
                                </div>
                                {clientsError[row.id] && (
                                  <p className="mt-2 text-xs" style={{ color: "#a01f1f" }}>{clientsError[row.id]}</p>
                                )}
                              </>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
                {!loading && tenants.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-5 py-6 text-center text-xs text-ink-muted">No workspaces yet.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}
