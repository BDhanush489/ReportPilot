"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import ConfirmDialog from "@/components/ConfirmDialog";
import { useAuth } from "@/lib/auth-context";
import { API_BASE, errorMessage, fetchJson } from "@/lib/api";

type DataSourceRow = {
  client_id: string;
  created_at: string;
  connector_kind: string;
  sources: string[];
};

type ConnectorKind = "gsc" | "ga4" | "pagespeed" | "other";

const KIND_LABEL: Record<string, string> = {
  gsc: "Google Search Console",
  ga4: "Google Analytics 4",
  pagespeed: "PageSpeed Insights",
  sqlite: "SQLite",
  postgres: "Postgres",
  snowflake: "Snowflake",
  bigquery: "BigQuery",
  databricks: "Databricks",
  imap_inbox: "Inbox (email)",
  slack_inbox: "Slack",
};

const OTHER_KINDS = ["sqlite", "postgres", "snowflake", "bigquery", "databricks"];

const LABEL_CLASS = "text-xs font-medium text-ink-secondary";
const INPUT_CLASS =
  "mt-1.5 w-full rounded-lg border border-gridline bg-white px-3.5 py-2.5 text-sm text-ink shadow-sm outline-none transition-colors placeholder:text-ink-muted/70 focus:border-brand focus:ring-4 focus:ring-brand/10 disabled:cursor-not-allowed disabled:opacity-50";

function readJsonFile(file: File): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        resolve(JSON.parse(reader.result as string));
      } catch {
        reject(new Error("That file isn't valid JSON — expected the service account key downloaded from Google Cloud Console."));
      }
    };
    reader.onerror = () => reject(reader.error || new Error("Could not read the file."));
    reader.readAsText(file);
  });
}

type GenRow = { stage: string | null; status: "running" | "done" | "error"; reportId?: string; error?: string };

export default function DataSourcesPage() {
  const { user, tenant, loading: authLoading } = useAuth();
  const router = useRouter();

  const [rows, setRows] = useState<DataSourceRow[]>([]);
  const [rowsLoading, setRowsLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  const loadRows = useCallback(async () => {
    setRowsLoading(true);
    setListError(null);
    try {
      const data = await fetchJson<{ data_sources: DataSourceRow[] }>("/api/data-sources");
      setRows(data.data_sources);
    } catch (e) {
      setListError(errorMessage(e, "Could not load your data sources."));
    } finally {
      setRowsLoading(false);
    }
  }, []);

  /* eslint-disable react-hooks/exhaustive-deps */
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login");
      return;
    }
    if (user) loadRows();
  }, [authLoading, user]);
  /* eslint-enable react-hooks/exhaustive-deps */

  // --- Connect form state ---
  const [clientId, setClientId] = useState("");
  const [kind, setKind] = useState<ConnectorKind>("gsc");
  const [otherKind, setOtherKind] = useState("sqlite");

  const [gscSiteUrl, setGscSiteUrl] = useState("");
  const [gscServiceAccount, setGscServiceAccount] = useState<Record<string, unknown> | null>(null);
  const [gscFileName, setGscFileName] = useState("");

  const [ga4PropertyId, setGa4PropertyId] = useState("");
  const [ga4ServiceAccount, setGa4ServiceAccount] = useState<Record<string, unknown> | null>(null);
  const [ga4FileName, setGa4FileName] = useState("");

  const [pagespeedUrls, setPagespeedUrls] = useState("");
  const [pagespeedApiKey, setPagespeedApiKey] = useState("");
  const [pagespeedStrategy, setPagespeedStrategy] = useState("mobile");

  const [otherConfigText, setOtherConfigText] = useState("{}");
  const [otherTableMapText, setOtherTableMapText] = useState('{"analytics": "table_name"}');

  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [saveOk, setSaveOk] = useState<string | null>(null);

  async function handleServiceAccountFile(
    file: File,
    setValue: (v: Record<string, unknown>) => void,
    setName: (v: string) => void
  ) {
    try {
      const parsed = await readJsonFile(file);
      setValue(parsed);
      setName(file.name);
      setFormError(null);
    } catch (e) {
      setFormError(errorMessage(e, "Could not read that file."));
    }
  }

  function buildConfigAndTableMap(): { config: Record<string, unknown>; table_map: Record<string, string>; kindValue: string } {
    if (kind === "gsc") {
      if (!gscServiceAccount) throw new Error("Upload the service account JSON key first.");
      if (!gscSiteUrl.trim()) throw new Error("Site URL is required.");
      return {
        kindValue: "gsc",
        config: { service_account_info: gscServiceAccount, site_url: gscSiteUrl.trim() },
        table_map: { seo: "search_analytics" },
      };
    }
    if (kind === "ga4") {
      if (!ga4ServiceAccount) throw new Error("Upload the service account JSON key first.");
      if (!ga4PropertyId.trim()) throw new Error("Property ID is required.");
      return {
        kindValue: "ga4",
        config: { service_account_info: ga4ServiceAccount, property_id: ga4PropertyId.trim() },
        table_map: { analytics: "ga4_report" },
      };
    }
    if (kind === "pagespeed") {
      const urls = pagespeedUrls.split("\n").map((u) => u.trim()).filter(Boolean);
      if (urls.length === 0) throw new Error("Add at least one URL to audit (one per line).");
      const config: Record<string, unknown> = { urls, strategy: pagespeedStrategy };
      if (pagespeedApiKey.trim()) config.api_key = pagespeedApiKey.trim();
      return { kindValue: "pagespeed", config, table_map: { seo: "pagespeed_audit" } };
    }
    // "other" -- raw JSON escape hatch for the SQL warehouse connectors
    // (postgres/snowflake/bigquery/databricks/sqlite), which aren't
    // verified live in this environment and have too many
    // vendor-specific fields for a dedicated form each.
    let config: Record<string, unknown>;
    let table_map: Record<string, string>;
    try {
      config = JSON.parse(otherConfigText);
    } catch {
      throw new Error("Config must be valid JSON.");
    }
    try {
      table_map = JSON.parse(otherTableMapText);
    } catch {
      throw new Error("Table map must be valid JSON.");
    }
    return { kindValue: otherKind, config, table_map };
  }

  async function handleTest() {
    setFormError(null);
    setTestResult(null);
    setSaveOk(null);
    setTesting(true);
    try {
      const { config, kindValue } = buildConfigAndTableMap();
      const data = await fetchJson<{ ok: boolean; tables: string[] }>("/api/data-sources/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: kindValue, config }),
      });
      setTestResult(data.tables);
    } catch (e) {
      setFormError(errorMessage(e, "Connection test failed."));
    } finally {
      setTesting(false);
    }
  }

  async function handleSave() {
    setFormError(null);
    setSaveOk(null);
    if (!clientId.trim()) {
      setFormError("client_id is required — this is the client the data will be reported on.");
      return;
    }
    setSaving(true);
    try {
      const { config, table_map, kindValue } = buildConfigAndTableMap();
      const data = await fetchJson<{ client_id: string; sources: Record<string, unknown> }>("/api/data-sources/onboard", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_id: clientId.trim(), kind: kindValue, config, table_map }),
      });
      const sourceKeys = Object.keys(data.sources);
      setSaveOk(sourceKeys.length ? `Connected — mapped: ${sourceKeys.join(", ")}.` : "Connected.");
      setTestResult(null);
      await loadRows();
    } catch (e) {
      setFormError(errorMessage(e, "Could not save this data source."));
    } finally {
      setSaving(false);
    }
  }

  // --- Generate a real report straight from a connected source ---
  const [genState, setGenState] = useState<Record<string, GenRow>>({});

  async function generateForClient(rowClientId: string) {
    setGenState((s) => ({ ...s, [rowClientId]: { stage: "Queued", status: "running" } }));
    try {
      const form = new FormData();
      form.set("client_id", rowClientId);
      form.set("agency_name", tenant?.name || "Your Agency");
      form.set("client_name", rowClientId);
      const { job_id } = await fetchJson<{ job_id: string }>("/api/generate-report", { method: "POST", body: form });

      await new Promise<void>((resolve, reject) => {
        const source = new EventSource(`${API_BASE}/api/jobs/${job_id}/events`, { withCredentials: true });
        source.onmessage = (event) => {
          const payload: { stage: string | null; status: string; error: string | null } = JSON.parse(event.data);
          if (payload.status === "error") {
            source.close();
            reject(new Error(payload.error || "Report generation failed"));
            return;
          }
          setGenState((s) => ({ ...s, [rowClientId]: { stage: payload.stage, status: "running" } }));
          if (payload.status === "done") {
            source.close();
            resolve();
          }
        };
        source.onerror = () => {
          source.close();
          reject(new Error("Lost connection to the backend while generating the report."));
        };
      });
      setGenState((s) => ({ ...s, [rowClientId]: { stage: "Done", status: "done", reportId: job_id } }));
    } catch (e) {
      setGenState((s) => ({ ...s, [rowClientId]: { stage: null, status: "error", error: errorMessage(e, "Report generation failed.") } }));
    }
  }

  // --- Remove a connected data source ---
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await fetchJson(`/api/data-sources/${encodeURIComponent(deleteTarget)}`, { method: "DELETE" });
      await loadRows();
    } catch (e) {
      setDeleteError(errorMessage(e, "Could not remove this data source."));
    } finally {
      setDeleting(false);
      setDeleteTarget(null);
    }
  }

  if (authLoading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <span className="h-6 w-6 animate-spin rounded-full border-2 border-brand/30 border-t-brand" />
      </div>
    );
  }

  return (
    <div className="flex-1 bg-background">
      <header className="sticky top-0 z-10 border-b border-gridline bg-surface/90 backdrop-blur-sm">
        <div className="mx-auto max-w-4xl px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div
              className="flex h-8 w-8 items-center justify-center rounded-lg text-sm font-bold text-white shadow-sm"
              style={{ backgroundColor: "#2a78d6", fontFamily: "var(--font-display)" }}
            >
              R
            </div>
            <span className="font-display font-semibold tracking-tight text-ink">Data sources</span>
          </div>
          <Link href="/app" className="text-xs font-medium text-ink-muted transition-colors hover:text-ink">
            Back to app
          </Link>
        </div>
      </header>

      <div className="mx-auto max-w-4xl px-6 py-10 space-y-10">
        <div>
          <h1 className="font-display text-2xl font-medium text-ink">Connect a data source</h1>
          <p className="mt-1.5 text-sm text-ink-secondary">
            Onboard a client once, then generate reports straight from live data instead of a fresh
            file upload every time. Credentials are stored per-client, encrypted at rest when
            DATA_CONTEXT_ENCRYPTION_KEY is configured on the backend.
          </p>
        </div>

        <section className="rounded-2xl border border-gridline bg-surface p-5 shadow-card">
          <div>
            <label className={LABEL_CLASS}>Client ID</label>
            <input
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              placeholder="e.g. aurora-home-goods"
              className={INPUT_CLASS}
            />
            <p className="mt-1.5 text-[11px] leading-relaxed text-ink-muted">
              The client this data will be reported on — pick a short, stable slug, you&rsquo;ll reuse it
              every time you generate a report for them.
            </p>
          </div>

          <div className="mt-4">
            <label className={LABEL_CLASS}>Source type</label>
            <div className="mt-1.5 flex flex-wrap gap-2">
              {([
                ["gsc", "Search Console"],
                ["ga4", "Google Analytics 4"],
                ["pagespeed", "PageSpeed Insights"],
                ["other", "Other (SQL / advanced)"],
              ] as [ConnectorKind, string][]).map(([k, label]) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => {
                    setKind(k);
                    setTestResult(null);
                    setFormError(null);
                    setSaveOk(null);
                  }}
                  className={`rounded-lg border px-3.5 py-2 text-xs font-medium transition-colors ${
                    kind === k
                      ? "border-brand bg-brand/8 text-brand"
                      : "border-gridline text-ink-secondary hover:border-baseline hover:text-ink"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {kind === "gsc" && (
            <div className="mt-4 space-y-3.5 border-t border-gridline pt-4">
              <div>
                <label className={LABEL_CLASS}>Service account JSON key</label>
                <input
                  type="file"
                  accept="application/json"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) handleServiceAccountFile(file, setGscServiceAccount, setGscFileName);
                  }}
                  className="mt-1.5 block w-full text-xs text-ink-muted file:mr-3 file:rounded-md file:border-0 file:bg-neutral-100 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-ink-secondary file:transition-colors hover:file:bg-neutral-200"
                />
                {gscFileName && (
                  <p className="mt-1.5 text-[11px] text-status-good">✓ {gscFileName} — never sent anywhere except this backend.</p>
                )}
              </div>
              <div>
                <label className={LABEL_CLASS}>Site URL</label>
                <input
                  value={gscSiteUrl}
                  onChange={(e) => setGscSiteUrl(e.target.value)}
                  placeholder="sc-domain:example.com or https://example.com/"
                  className={INPUT_CLASS}
                />
                <p className="mt-1.5 text-[11px] leading-relaxed text-ink-muted">
                  Exactly as registered in Search Console. Grant this service account&rsquo;s email
                  Restricted access under Settings → Users and permissions first.
                </p>
              </div>
            </div>
          )}

          {kind === "ga4" && (
            <div className="mt-4 space-y-3.5 border-t border-gridline pt-4">
              <div>
                <label className={LABEL_CLASS}>Service account JSON key</label>
                <input
                  type="file"
                  accept="application/json"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) handleServiceAccountFile(file, setGa4ServiceAccount, setGa4FileName);
                  }}
                  className="mt-1.5 block w-full text-xs text-ink-muted file:mr-3 file:rounded-md file:border-0 file:bg-neutral-100 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-ink-secondary file:transition-colors hover:file:bg-neutral-200"
                />
                {ga4FileName && (
                  <p className="mt-1.5 text-[11px] text-status-good">✓ {ga4FileName} — never sent anywhere except this backend.</p>
                )}
              </div>
              <div>
                <label className={LABEL_CLASS}>Property ID</label>
                <input
                  value={ga4PropertyId}
                  onChange={(e) => setGa4PropertyId(e.target.value)}
                  placeholder="e.g. 457874199"
                  className={INPUT_CLASS}
                />
                <p className="mt-1.5 text-[11px] leading-relaxed text-ink-muted">
                  The numeric ID from Admin → Property details. Grant this service account&rsquo;s email
                  Viewer access under Property Access Management first.
                </p>
              </div>
            </div>
          )}

          {kind === "pagespeed" && (
            <div className="mt-4 space-y-3.5 border-t border-gridline pt-4">
              <div>
                <label className={LABEL_CLASS}>URLs to audit (one per line)</label>
                <textarea
                  value={pagespeedUrls}
                  onChange={(e) => setPagespeedUrls(e.target.value)}
                  rows={4}
                  placeholder={"https://example.com/\nhttps://example.com/pricing"}
                  className={`${INPUT_CLASS} resize-none font-mono`}
                />
              </div>
              <div className="flex gap-3.5">
                <div className="flex-1">
                  <label className={LABEL_CLASS}>API key (optional)</label>
                  <input
                    value={pagespeedApiKey}
                    onChange={(e) => setPagespeedApiKey(e.target.value)}
                    placeholder="Falls back to PAGESPEED_API_KEY on the backend"
                    className={INPUT_CLASS}
                  />
                </div>
                <div className="w-36">
                  <label className={LABEL_CLASS}>Strategy</label>
                  <select
                    value={pagespeedStrategy}
                    onChange={(e) => setPagespeedStrategy(e.target.value)}
                    className={INPUT_CLASS}
                  >
                    <option value="mobile">Mobile</option>
                    <option value="desktop">Desktop</option>
                  </select>
                </div>
              </div>
              <p className="text-[11px] leading-relaxed text-ink-muted">
                The anonymous quota is usually already exhausted — get a free key at Google Cloud
                Console → enable &ldquo;PageSpeed Insights API&rdquo; → Credentials → Create API key.
              </p>
            </div>
          )}

          {kind === "other" && (
            <div className="mt-4 space-y-3.5 border-t border-gridline pt-4">
              <div>
                <label className={LABEL_CLASS}>Connector</label>
                <select value={otherKind} onChange={(e) => setOtherKind(e.target.value)} className={INPUT_CLASS}>
                  {OTHER_KINDS.map((k) => (
                    <option key={k} value={k}>{KIND_LABEL[k]}</option>
                  ))}
                </select>
                <p className="mt-1.5 text-[11px] leading-relaxed text-ink-muted">
                  Only SQLite is verified end-to-end in this environment — the rest are written to
                  their vendor&rsquo;s documented driver but need real credentials to confirm live.
                </p>
              </div>
              <div>
                <label className={LABEL_CLASS}>Config (JSON)</label>
                <textarea
                  value={otherConfigText}
                  onChange={(e) => setOtherConfigText(e.target.value)}
                  rows={3}
                  className={`${INPUT_CLASS} resize-none font-mono`}
                />
                <p className="mt-1.5 text-[11px] leading-relaxed text-ink-muted">
                  e.g. <code className="rounded bg-neutral-100 px-1 py-0.5">{"{\"path\": \"./client.db\"}"}</code> for
                  SQLite, <code className="rounded bg-neutral-100 px-1 py-0.5">{"{\"dsn\": \"postgresql://...\"}"}</code> for Postgres.
                </p>
              </div>
              <div>
                <label className={LABEL_CLASS}>Table map (JSON)</label>
                <textarea
                  value={otherTableMapText}
                  onChange={(e) => setOtherTableMapText(e.target.value)}
                  rows={2}
                  className={`${INPUT_CLASS} resize-none font-mono`}
                />
                <p className="mt-1.5 text-[11px] leading-relaxed text-ink-muted">
                  Maps canonical source type (&ldquo;analytics&rdquo;/&ldquo;seo&rdquo;/&ldquo;sales&rdquo;) to a real table name.
                </p>
              </div>
            </div>
          )}

          {formError && (
            <div className="mt-4 flex items-start gap-2 rounded-lg border-l-4 px-3.5 py-3 text-sm leading-relaxed"
              style={{ borderColor: "#d03b3b", backgroundColor: "#fdecec", color: "#7a1f1f" }}>
              {formError}
            </div>
          )}
          {testResult && (
            <div className="mt-4 rounded-lg border-l-4 px-3.5 py-3 text-xs leading-relaxed"
              style={{ borderColor: "#2a78d6", backgroundColor: "#eef4fc", color: "#164a85" }}>
              Connection OK — {testResult.length ? `found: ${testResult.join(", ")}` : "reachable, no tables listed."}
            </div>
          )}
          {saveOk && (
            <div className="mt-4 rounded-lg border-l-4 px-3.5 py-3 text-xs leading-relaxed"
              style={{ borderColor: "#0ca30c", backgroundColor: "#eaf7ea", color: "#1c4f1c" }}>
              {saveOk}
            </div>
          )}

          <div className="mt-5 flex gap-2.5">
            <button
              type="button"
              onClick={handleTest}
              disabled={testing || saving}
              className="rounded-lg border border-gridline bg-white px-4 py-2.5 text-xs font-semibold text-ink-secondary shadow-sm transition-colors hover:bg-black/2 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {testing ? "Testing…" : "Test connection"}
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={saving || testing}
              className="rounded-lg px-4 py-2.5 text-xs font-semibold text-white shadow-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50"
              style={{ backgroundColor: "#2a78d6" }}
            >
              {saving ? "Connecting…" : "Connect & save"}
            </button>
          </div>
        </section>

        <section className="rounded-2xl border border-gridline bg-surface shadow-card overflow-hidden">
          <div className="border-b border-gridline px-5 py-3.5">
            <h2 className="text-[13px] font-semibold uppercase tracking-wide text-ink-secondary">
              Connected ({rows.length})
            </h2>
          </div>
          {listError && (
            <div className="px-5 py-3 text-xs" style={{ color: "#a01f1f" }}>{listError}</div>
          )}
          {deleteError && (
            <div className="px-5 py-3 text-xs" style={{ color: "#a01f1f" }}>{deleteError}</div>
          )}
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gridline text-left text-xs text-ink-muted">
                  <th className="px-5 py-2.5 font-medium">Client</th>
                  <th className="px-5 py-2.5 font-medium">Source</th>
                  <th className="px-5 py-2.5 font-medium">Mapped</th>
                  <th className="px-5 py-2.5 font-medium">Connected</th>
                  <th className="px-5 py-2.5 font-medium" />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const gen = genState[row.client_id];
                  return (
                    <tr key={row.client_id} className="border-b border-gridline last:border-0 align-top">
                      <td className="px-5 py-2.5 font-medium text-ink">{row.client_id}</td>
                      <td className="px-5 py-2.5 text-ink-secondary">{KIND_LABEL[row.connector_kind] || row.connector_kind}</td>
                      <td className="px-5 py-2.5 text-ink-secondary">{row.sources.join(", ") || "—"}</td>
                      <td className="px-5 py-2.5 text-ink-muted">{new Date(row.created_at).toLocaleDateString()}</td>
                      <td className="px-5 py-2.5">
                        <div className="flex items-start justify-end gap-3">
                          <div className="text-right">
                            {(!gen || gen.status === "error") && (
                              <button
                                type="button"
                                onClick={() => generateForClient(row.client_id)}
                                className="rounded-md border border-gridline px-2.5 py-1 text-xs font-medium text-ink-secondary transition-colors hover:border-baseline hover:text-ink"
                              >
                                Generate report
                              </button>
                            )}
                            {gen?.status === "running" && (
                              <span className="inline-flex items-center gap-1.5 text-xs text-ink-muted">
                                <span className="h-3 w-3 animate-spin rounded-full border-2 border-brand/30 border-t-brand" />
                                {gen.stage || "Working…"}
                              </span>
                            )}
                            {gen?.status === "done" && gen.reportId && (
                              <a
                                href={`${API_BASE}/api/report/${gen.reportId}/pdf`}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-xs font-semibold"
                                style={{ color: "#2a78d6" }}
                              >
                                View PDF →
                              </a>
                            )}
                            {gen?.status === "error" && (
                              <span className="block text-[11px]" style={{ color: "#a01f1f" }}>{gen.error}</span>
                            )}
                          </div>
                          <button
                            type="button"
                            disabled={gen?.status === "running"}
                            onClick={() => setDeleteTarget(row.client_id)}
                            className="text-xs font-medium text-ink-muted transition-colors hover:text-[#a01f1f] disabled:cursor-not-allowed disabled:opacity-40"
                            title="Remove this data source"
                          >
                            Remove
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
                {!rowsLoading && rows.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-5 py-6 text-center text-xs text-ink-muted">
                      Nothing connected yet — use the form above.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Remove this data source?"
        body={`This deletes the saved connection for "${deleteTarget}" — the credentials and column mapping, not any reports already generated from it. Any schedule still pointing at it will start failing on its next run.`}
        confirmLabel="Remove"
        destructive
        busy={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
