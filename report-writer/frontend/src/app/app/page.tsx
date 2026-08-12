"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import FileDropzone from "@/components/FileDropzone";
import { useAuth } from "@/lib/auth-context";
import { withCsrf } from "@/lib/csrf";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

type ReportChart = {
  caption: string;
  img: string;
};

type ReportSection = {
  heading: string;
  narrative: string;
  recommendations: string[];
  charts?: ReportChart[];
};

type InsightTag = "score" | "opportunity" | "risk" | "efficiency";

type InsightCard = {
  id: string;
  tag: InsightTag;
  title: string;
  headline: string;
  sub?: string;
  detail: string;
};

type DataQualityIssue = {
  source: string;
  column: string;
  kind: string;
  message: string;
  count: number;
  sample?: string[];
};

type DataQuality = {
  total_issues_found: number;
  total_values_affected: number;
  by_kind: Record<string, number>;
  details: DataQualityIssue[];
};

type Report = {
  report_title: string;
  period_label: string;
  executive_summary: string;
  highlights: string[];
  watchouts: string[];
  sections: ReportSection[];
  next_steps: string[];
  insights?: InsightCard[];
  data_quality?: DataQuality;
};

type QaBadge = {
  badge: "PASS" | "PASS-WITH-WARNINGS" | "FAIL";
  failing_checks: string[];
};

type GenerateResponse = {
  report_id: string;
  report: Report;
  ai_generated: boolean;
  ai_provider: string | null;
  ai_error: string | null;
  // Absent (undefined/null) for a report generated before the canonical
  // report object shipped on the backend — treat that as "no badge to
  // show," not as a failure.
  qa?: QaBadge | null;
};

type RecentReport = {
  report_id: string;
  created_at: string;
  agency_name: string | null;
  client_name: string | null;
  report_title: string | null;
  period_label: string | null;
  ai_generated: boolean;
  ai_provider: string | null;
};

// T4 — mirrors template_specs.list_templates()'s shape.
type TemplateInfo = {
  id: string;
  label: string;
  description: string;
  version: number;
  tone: string;
  sections: string[];
};

const INSIGHT_STYLE: Record<InsightTag, { bg: string; border: string; fg: string }> = {
  score: { bg: "#eef4fc", border: "#2a78d6", fg: "#164a85" },
  opportunity: { bg: "#eaf7ea", border: "#0ca30c", fg: "#1c6b1c" },
  risk: { bg: "#fdf0ea", border: "#ec835a", fg: "#9a4322" },
  efficiency: { bg: "#f1eefb", border: "#4a3aa7", fg: "#3a2d84" },
};

// Mirrors app/theme.py's BADGE_* tokens on the backend (bg/border/text per
// tier) -- kept numerically identical so the badge reads the same color
// here as it does in the PDF and the standalone dashboard.
const QA_BADGE_STYLE: Record<QaBadge["badge"], { bg: string; border: string; fg: string }> = {
  "PASS": { bg: "#e8f5e9", border: "#0ca30c", fg: "#006300" },
  "PASS-WITH-WARNINGS": { bg: "#fdf3e3", border: "#fab219", fg: "#a6540f" },
  "FAIL": { bg: "#fbe9e7", border: "#d03b3b", fg: "#a01f1f" },
};

function fileToDataUri(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error || new Error("Could not read the logo file."));
    reader.readAsDataURL(file);
  });
}

// Mirrors report_builder.STAGES on the backend — this is the real pipeline,
// not a simulated timer, so it stays in lockstep with report_builder.py.
const PIPELINE_STAGES = [
  "Parsing, cleaning & computing metrics",
  "Writing narrative",
  "Computing insights",
  "Building PDF",
  "Done",
];

const LABEL_CLASS = "text-xs font-medium text-ink-secondary";
const INPUT_CLASS =
  "mt-1.5 w-full rounded-lg border border-gridline bg-white px-3.5 py-2.5 text-sm text-ink shadow-sm outline-none transition-colors placeholder:text-ink-muted/70 focus:border-brand focus:ring-4 focus:ring-brand/10 disabled:cursor-not-allowed disabled:opacity-50";

// The three form sections really are sequential steps (branding decided ->
// template chosen -> data uploaded, in that order), so a numbered step
// badge encodes something true here, not decoration for its own sake.
function SectionHeading({ step, title }: { step: number; title: string }) {
  return (
    <div className="mb-4 flex items-center gap-2.5">
      <span
        className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold tabular-nums"
        style={{ backgroundColor: "#2a78d61a", color: "#2a78d6" }}
      >
        {step}
      </span>
      <h2 className="text-[13px] font-semibold tracking-wide text-ink-secondary uppercase">{title}</h2>
    </div>
  );
}

export default function Home() {
  const { user, loading: authLoading, logout } = useAuth();
  const router = useRouter();

  const [agencyName, setAgencyName] = useState("Northlight Growth Partners");
  const [clientName, setClientName] = useState("Aurora Home Goods");
  const [primaryColor, setPrimaryColor] = useState("#2a78d6");
  const [accentColor, setAccentColor] = useState("#eda100");
  const [logoFile, setLogoFile] = useState<File | null>(null);
  const [logoPreviewUrl, setLogoPreviewUrl] = useState<string | null>(null);
  const [showWhiteLabel, setShowWhiteLabel] = useState(false);
  const [fontFamily, setFontFamily] = useState("");
  const [footerText, setFooterText] = useState("");
  const [signatureName, setSignatureName] = useState("");
  const [signatureTitle, setSignatureTitle] = useState("");
  const [disclaimerText, setDisclaimerText] = useState("");

  const [analyticsFile, setAnalyticsFile] = useState<File | null>(null);
  const [seoFile, setSeoFile] = useState<File | null>(null);
  const [salesFile, setSalesFile] = useState<File | null>(null);

  const [loading, setLoading] = useState(false);
  const [currentStage, setCurrentStage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<GenerateResponse | null>(null);

  const [recent, setRecent] = useState<RecentReport[]>([]);
  const [recentLoading, setRecentLoading] = useState(false);

  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [templateId, setTemplateId] = useState("default");

  const canGenerate = !!(analyticsFile || seoFile || salesFile) && !loading;

  async function loadRecent() {
    setRecentLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/reports`, { credentials: "include" });
      if (res.ok) {
        const data: { reports: RecentReport[] } = await res.json();
        setRecent(data.reports);
      }
    } catch {
      // best-effort — an empty list just means "no recent reports yet"
    } finally {
      setRecentLoading(false);
    }
  }

  async function loadTemplates() {
    try {
      const res = await fetch(`${API_BASE}/api/templates`, { credentials: "include" });
      if (res.ok) {
        const data: { templates: TemplateInfo[] } = await res.json();
        setTemplates(data.templates);
        if (data.templates.length && !data.templates.some((t) => t.id === templateId)) {
          setTemplateId(data.templates[0].id);
        }
      }
    } catch {
      // best-effort — the form still works with the "default" template if this fails
    }
  }

  // Route protection is client-side, not Next middleware: the session
  // cookie is scoped to the BACKEND's origin, so Next middleware running on
  // the frontend's own origin can never read it anyway.
  /* eslint-disable react-hooks/exhaustive-deps */
  useEffect(() => {
    if (!authLoading && !user) router.replace("/login");
  }, [authLoading, user]);
  /* eslint-enable react-hooks/exhaustive-deps */

  // On-mount fetch-then-setState (not a synchronous render-loop) --
  // pre-existing pattern for loadRecent(), same fix now applied to
  // loadTemplates(). Gated on `user` so an unauthenticated visitor (about
  // to be redirected to /login by the effect above) never fires a doomed
  // 401 request first.
  /* eslint-disable react-hooks/set-state-in-effect, react-hooks/exhaustive-deps */
  useEffect(() => {
    if (user) {
      loadRecent();
      loadTemplates();
    }
  }, [user]);
  /* eslint-enable react-hooks/set-state-in-effect, react-hooks/exhaustive-deps */

  async function handleViewRecent(reportId: string) {
    setError(null);
    setResult(null);
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/report/${reportId}`, { credentials: "include" });
      if (!res.ok) throw new Error(`Report not found (${res.status})`);
      const data: GenerateResponse = await res.json();
      setResult(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load that report.");
    } finally {
      setLoading(false);
    }
  }

  async function handleGenerate() {
    setLoading(true);
    setCurrentStage(null);
    setError(null);
    setResult(null);
    try {
      const form = new FormData();
      form.set("agency_name", agencyName);
      form.set("client_name", clientName);
      form.set("primary_color", primaryColor);
      form.set("accent_color", accentColor);
      form.set("template_id", templateId);
      if (logoFile) form.set("logo_data_uri", await fileToDataUri(logoFile));
      if (fontFamily) form.set("font_family", fontFamily);
      if (footerText) form.set("footer_text", footerText);
      if (signatureName) form.set("signature_name", signatureName);
      if (signatureTitle) form.set("signature_title", signatureTitle);
      if (disclaimerText) form.set("disclaimer_text", disclaimerText);
      if (analyticsFile) form.set("analytics_file", analyticsFile);
      if (seoFile) form.set("seo_file", seoFile);
      if (salesFile) form.set("sales_file", salesFile);

      const res = await fetch(
        `${API_BASE}/api/generate-report`,
        withCsrf({ method: "POST", body: form, credentials: "include" })
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Request failed (${res.status})`);
      }
      const { job_id }: { job_id: string } = await res.json();

      // Stream real pipeline stages — not a simulated timer — via SSE, then
      // fetch the finished report once the backend reports "done".
      // withCredentials: true — EventSource, unlike fetch, defaults to NOT
      // sending cookies cross-origin; without this the session cookie never
      // reaches the backend and every event comes back "job not found".
      await new Promise<void>((resolve, reject) => {
        const source = new EventSource(`${API_BASE}/api/jobs/${job_id}/events`, { withCredentials: true });
        source.onmessage = async (event) => {
          const payload: { stage: string | null; status: string; error: string | null } = JSON.parse(event.data);
          setCurrentStage(payload.stage);
          if (payload.status === "error") {
            source.close();
            reject(new Error(payload.error || "Report generation failed"));
            return;
          }
          if (payload.status === "done") {
            source.close();
            try {
              const reportRes = await fetch(`${API_BASE}/api/report/${job_id}`, { credentials: "include" });
              if (!reportRes.ok) throw new Error(`Report not found (${reportRes.status})`);
              const data: GenerateResponse = await reportRes.json();
              setResult(data);
              resolve();
            } catch (e) {
              reject(e instanceof Error ? e : new Error("Could not load the finished report."));
            }
          }
        };
        source.onerror = () => {
          source.close();
          reject(new Error("Lost connection to the backend while generating the report."));
        };
      });
      loadRecent();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setLoading(false);
      setCurrentStage(null);
    }
  }

  // Loading (first /api/auth/me check) or about to be redirected to /login
  // by the effect above -- never flash the real form in either state.
  if (authLoading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <span className="h-6 w-6 animate-spin rounded-full border-2 border-brand/30 border-t-brand" />
      </div>
    );
  }

  return (
    <div className="flex-1 bg-background">
      {/* Nav */}
      <header className="sticky top-0 z-10 border-b border-gridline bg-surface/90 backdrop-blur-sm">
        <div className="mx-auto max-w-6xl px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg text-sm font-bold text-white shadow-sm"
              style={{ backgroundColor: "#2a78d6", fontFamily: "var(--font-display)" }}>
              R
            </div>
            <span className="font-display font-semibold tracking-tight text-ink">
              ReportPilot
            </span>
          </div>
          <div className="flex items-center gap-4">
            <span className="hidden text-xs text-ink-muted sm:block">AI report writer for agencies</span>
            <div className="flex items-center gap-3 border-l border-gridline pl-4">
              <a href="/data-sources" className="text-xs font-medium text-ink-muted transition-colors hover:text-ink">
                Data sources
              </a>
              {user.is_platform_admin && (
                <a href="/admin" className="text-xs font-medium text-ink-muted transition-colors hover:text-ink">
                  Admin
                </a>
              )}
              <span className="hidden text-xs text-ink-secondary sm:block" title={user.email}>
                {user.name || user.email}
              </span>
              <button
                type="button"
                onClick={() => {
                  logout();
                  router.replace("/login");
                }}
                className="text-xs font-medium text-ink-muted transition-colors hover:text-ink"
              >
                Sign out
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* Hero */}
      <div className="mx-auto max-w-6xl px-6 pt-14 pb-8">
        <h1
          className="max-w-2xl text-3xl font-medium tracking-tight text-ink md:text-4xl text-balance font-display"
        >
          Turn raw client data into a branded report — in minutes, not hours.
        </h1>
        <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-ink-secondary">
          Upload an analytics export, an SEO/site audit, and a sales spreadsheet. ReportPilot computes
          every number deterministically, surfaces the opportunities buried in it, and has an AI model
          write the client-ready narrative around it — never the other way around.
        </p>
      </div>

      <div className="mx-auto max-w-6xl px-6 pb-20 grid grid-cols-1 lg:grid-cols-5 gap-6 items-start">
        {/* Left: form — sticks in place while the report preview scrolls on the right */}
        <div className="lg:col-span-2 space-y-5 lg:sticky lg:top-24">
          <section className="rounded-2xl border border-gridline bg-surface p-5 shadow-card">
            <SectionHeading step={1} title="Branding" />
            <div className="space-y-4">
              <div>
                <label className={LABEL_CLASS}>Agency name</label>
                <input
                  value={agencyName}
                  onChange={(e) => setAgencyName(e.target.value)}
                  disabled={loading}
                  className={INPUT_CLASS}
                />
              </div>
              <div>
                <label className={LABEL_CLASS}>Client name</label>
                <input
                  value={clientName}
                  onChange={(e) => setClientName(e.target.value)}
                  disabled={loading}
                  className={INPUT_CLASS}
                />
              </div>
              <div className="flex gap-4">
                <div className="flex-1">
                  <label className={LABEL_CLASS}>Primary color</label>
                  <div className="mt-1.5 flex items-center gap-2 rounded-lg border border-gridline bg-white py-1.5 pl-1.5 pr-3 shadow-sm">
                    <span className="relative h-7 w-7 shrink-0 overflow-hidden rounded-md ring-1 ring-inset ring-black/5">
                      <input type="color" value={primaryColor} onChange={(e) => setPrimaryColor(e.target.value)}
                        disabled={loading}
                        className="absolute -left-1 -top-1 h-9 w-9 cursor-pointer border-0 bg-transparent p-0 disabled:cursor-not-allowed" />
                    </span>
                    <span className="font-mono text-xs tabular-nums text-ink-secondary">{primaryColor}</span>
                  </div>
                </div>
                <div className="flex-1">
                  <label className={LABEL_CLASS}>Accent color</label>
                  <div className="mt-1.5 flex items-center gap-2 rounded-lg border border-gridline bg-white py-1.5 pl-1.5 pr-3 shadow-sm">
                    <span className="relative h-7 w-7 shrink-0 overflow-hidden rounded-md ring-1 ring-inset ring-black/5">
                      <input type="color" value={accentColor} onChange={(e) => setAccentColor(e.target.value)}
                        disabled={loading}
                        className="absolute -left-1 -top-1 h-9 w-9 cursor-pointer border-0 bg-transparent p-0 disabled:cursor-not-allowed" />
                    </span>
                    <span className="font-mono text-xs tabular-nums text-ink-secondary">{accentColor}</span>
                  </div>
                </div>
              </div>
              <div>
                <label className={LABEL_CLASS}>Company logo (optional)</label>
                <div className="mt-1.5 flex items-center gap-3">
                  {logoPreviewUrl && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={logoPreviewUrl} alt="Logo preview" className="h-10 w-10 shrink-0 rounded-lg border border-gridline bg-white object-contain p-1 shadow-sm" />
                  )}
                  <input
                    type="file"
                    accept="image/png,image/jpeg"
                    disabled={loading}
                    onChange={(e) => {
                      const file = e.target.files?.[0] ?? null;
                      setLogoFile(file);
                      setLogoPreviewUrl((prev) => {
                        if (prev) URL.revokeObjectURL(prev);
                        return file ? URL.createObjectURL(file) : null;
                      });
                    }}
                    className="text-xs text-ink-muted file:mr-3 file:rounded-md file:border-0 file:bg-neutral-100 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-ink-secondary file:transition-colors hover:file:bg-neutral-200 disabled:opacity-50"
                  />
                </div>
                <p className="mt-1.5 text-[11px] leading-relaxed text-ink-muted">PNG or JPEG. Shown on the PDF cover, the dashboard header, and every page of the Power BI export.</p>
              </div>

              <button
                type="button"
                onClick={() => setShowWhiteLabel((v) => !v)}
                className="inline-flex items-center gap-1 text-xs font-medium text-brand transition-colors hover:text-[#1d5aa3]"
              >
                {showWhiteLabel ? "Hide" : "Show"} advanced white-label options
                <svg viewBox="0 0 20 20" fill="currentColor" className={`h-3.5 w-3.5 transition-transform ${showWhiteLabel ? "rotate-180" : ""}`}>
                  <path fillRule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clipRule="evenodd" />
                </svg>
              </button>

              {showWhiteLabel && (
                <div className="space-y-3.5 pt-1 border-t border-gridline">
                  <div>
                    <label className={LABEL_CLASS}>Font family (CSS font stack, optional)</label>
                    <input
                      value={fontFamily}
                      onChange={(e) => setFontFamily(e.target.value)}
                      placeholder='e.g. "Georgia", serif'
                      disabled={loading}
                      className={INPUT_CLASS}
                    />
                  </div>
                  <div>
                    <label className={LABEL_CLASS}>Footer text override (optional)</label>
                    <input
                      value={footerText}
                      onChange={(e) => setFooterText(e.target.value)}
                      placeholder="Replaces the default footer line"
                      disabled={loading}
                      className={INPUT_CLASS}
                    />
                  </div>
                  <div className="flex gap-3">
                    <div className="flex-1">
                      <label className={LABEL_CLASS}>Signature name (optional)</label>
                      <input
                        value={signatureName}
                        onChange={(e) => setSignatureName(e.target.value)}
                        disabled={loading}
                        className={INPUT_CLASS}
                      />
                    </div>
                    <div className="flex-1">
                      <label className={LABEL_CLASS}>Signature title (optional)</label>
                      <input
                        value={signatureTitle}
                        onChange={(e) => setSignatureTitle(e.target.value)}
                        disabled={loading}
                        className={INPUT_CLASS}
                      />
                    </div>
                  </div>
                  <div>
                    <label className={LABEL_CLASS}>Disclaimer text (optional)</label>
                    <textarea
                      value={disclaimerText}
                      onChange={(e) => setDisclaimerText(e.target.value)}
                      rows={2}
                      placeholder="Shown at the bottom of the PDF and dashboard"
                      disabled={loading}
                      className={`${INPUT_CLASS} resize-none`}
                    />
                  </div>
                </div>
              )}
            </div>
          </section>

          <section className="rounded-2xl border border-gridline bg-surface p-5 shadow-card">
            <SectionHeading step={2} title="Report template" />
            <div className="space-y-2">
              {templates.map((t) => {
                const selected = templateId === t.id;
                return (
                  <label
                    key={t.id}
                    className={`flex items-start gap-3 rounded-xl border p-3.5 cursor-pointer transition-all ${
                      selected ? "shadow-sm" : "border-gridline hover:border-baseline hover:bg-black/1.5"
                    }`}
                    style={selected ? { borderColor: primaryColor, backgroundColor: `${primaryColor}0d` } : undefined}
                  >
                    <input
                      type="radio"
                      name="template_id"
                      value={t.id}
                      checked={selected}
                      onChange={() => setTemplateId(t.id)}
                      disabled={loading}
                      style={{ accentColor: primaryColor }}
                      className="mt-0.5 h-3.5 w-3.5 shrink-0 cursor-pointer"
                    />
                    <span>
                      <span className="block text-sm font-medium text-ink">{t.label}</span>
                      <span className="mt-0.5 block text-xs leading-relaxed text-ink-muted">{t.description}</span>
                    </span>
                  </label>
                );
              })}
              {templates.length === 0 && (
                <p className="text-xs text-ink-muted">Using the default template.</p>
              )}
            </div>
          </section>

          <section className="rounded-2xl border border-gridline bg-surface p-5 shadow-card">
            <SectionHeading step={3} title="Data sources" />
            <p className="mb-3.5 text-xs leading-relaxed text-ink-muted">Upload at least one. Sample files live in{" "}
              <code className="mx-0.5 rounded bg-neutral-100 px-1 py-0.5 font-mono text-[11px] text-ink-secondary">backend/sample_data/</code>.</p>
            <div className="space-y-3.5">
              <FileDropzone label="Web analytics export" hint="CSV — GA4-style channel/date export"
                accept=".csv" file={analyticsFile} onChange={setAnalyticsFile} accentColor={accentColor} disabled={loading} />
              <FileDropzone label="SEO / site audit" hint="CSV — one row per crawled URL"
                accept=".csv" file={seoFile} onChange={setSeoFile} accentColor={accentColor} disabled={loading} />
              <FileDropzone label="Sales pipeline" hint="XLSX or CSV — CRM deal export"
                accept=".xlsx,.xls,.csv" file={salesFile} onChange={setSalesFile} accentColor={accentColor} disabled={loading} />
            </div>
          </section>

          <button
            onClick={handleGenerate}
            disabled={!canGenerate}
            className="group relative flex w-full items-center justify-center gap-2 overflow-hidden rounded-xl py-3.5 text-sm font-semibold text-white shadow-[0_1px_2px_rgba(11,11,11,0.08),0_8px_20px_-6px_var(--btn-shadow)] transition-all hover:-translate-y-px hover:shadow-[0_1px_2px_rgba(11,11,11,0.10),0_12px_24px_-6px_var(--btn-shadow)] active:translate-y-0 disabled:pointer-events-none disabled:opacity-40 disabled:shadow-none disabled:hover:translate-y-0"
            style={{ backgroundColor: primaryColor, ["--btn-shadow" as string]: `${primaryColor}66` }}
          >
            {loading && <span className="h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-white/40 border-t-white" />}
            {loading ? "Generating report…" : "Generate report"}
          </button>
          {error && (
            <div className="flex items-start gap-2 rounded-lg border-l-4 px-3.5 py-3 text-sm leading-relaxed"
              style={{ borderColor: "#d03b3b", backgroundColor: "#fdecec", color: "#7a1f1f" }}>
              <svg viewBox="0 0 20 20" fill="currentColor" className="mt-0.5 h-4 w-4 shrink-0">
                <path fillRule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.63-1.516 2.63H3.72c-1.347 0-2.189-1.463-1.515-2.63L8.485 2.495zM10 6a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 6zm0 8a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" />
              </svg>
              {error}
            </div>
          )}

          <section className="rounded-2xl border border-gridline bg-surface p-5 shadow-card">
            <h2 className="mb-3.5 text-[13px] font-semibold uppercase tracking-wide text-ink-secondary">Recent reports</h2>
            {recentLoading && recent.length === 0 && (
              <div className="space-y-2">
                {[0, 1, 2].map((i) => (
                  <div key={i} className="h-12 animate-pulse rounded-lg bg-neutral-100" />
                ))}
              </div>
            )}
            {!recentLoading && recent.length === 0 && (
              <p className="text-xs leading-relaxed text-ink-muted">
                Nothing generated yet. Reports you generate are saved here — they
                persist on disk, so they survive a backend restart.
              </p>
            )}
            <ul className="max-h-72 space-y-1.5 overflow-y-auto">
              {recent.map((r) => (
                <li key={r.report_id}
                  className="flex items-center justify-between gap-2 rounded-lg border border-transparent px-3 py-2.5 transition-colors hover:border-gridline hover:bg-black/1.5">
                  <div className="min-w-0">
                    <div className="truncate text-xs font-medium text-ink">
                      {r.report_title || r.client_name || "Report"}
                    </div>
                    <div className="mt-0.5 text-[11px] text-ink-muted">
                      {new Date(r.created_at).toLocaleString()}
                      {" · "}
                      {r.ai_generated ? (r.ai_provider || "AI-written") : "draft narrative"}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-3">
                    <button onClick={() => handleViewRecent(r.report_id)}
                      className="text-xs font-medium text-ink-secondary transition-colors hover:text-ink">
                      View
                    </button>
                    <a href={`${API_BASE}/api/report/${r.report_id}/pdf`} target="_blank" rel="noopener noreferrer"
                      className="text-xs font-medium transition-opacity hover:opacity-70" style={{ color: primaryColor }}>
                      PDF
                    </a>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        </div>

        {/* Right: preview */}
        <div className="lg:col-span-3">
          <div className="rounded-2xl border border-gridline bg-surface p-6 shadow-card min-h-[70vh] sm:p-8">
            {!result && !loading && (
              <div className="flex h-full flex-col items-center justify-center py-24 text-center">
                <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl border border-gridline bg-white shadow-sm">
                  <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5 text-ink-muted">
                    <path d="M7 3.5h7l4 4V19a1.5 1.5 0 01-1.5 1.5h-9A1.5 1.5 0 016 19V5A1.5 1.5 0 017 3.5z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
                    <path d="M14 3.5V8h4M9 12h6M9 15h6M9 9h2" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </div>
                <div className="font-display text-[15px] text-ink">Your generated report will appear here.</div>
                <div className="mt-1 text-xs text-ink-muted">Upload data and click Generate to see a live preview.</div>
              </div>
            )}
            {loading && (
              <div className="flex h-full flex-col items-center justify-center py-24">
                <div className="w-full max-w-xs space-y-4">
                  {PIPELINE_STAGES.filter((s) => s !== "Done").map((label) => {
                    const currentIdx = currentStage ? PIPELINE_STAGES.indexOf(currentStage) : -1;
                    const idx = PIPELINE_STAGES.indexOf(label);
                    const state = currentIdx < 0 ? "pending" : idx < currentIdx ? "done" : idx === currentIdx ? "active" : "pending";
                    return (
                      <div key={label} className="flex items-center gap-3">
                        {state === "done" && (
                          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white" style={{ backgroundColor: primaryColor }}>
                            ✓
                          </span>
                        )}
                        {state === "active" && (
                          <span className="h-5 w-5 shrink-0 rounded-full border-2 animate-spin" style={{ borderColor: `${primaryColor}33`, borderTopColor: primaryColor }} />
                        )}
                        {state === "pending" && (
                          <span className="h-5 w-5 shrink-0 rounded-full border border-gridline" />
                        )}
                        <span className={`text-sm ${state === "active" ? "font-medium text-ink" : state === "done" ? "text-ink-secondary" : "text-ink-muted/60"}`}>
                          {label}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            {result && (
              <ReportPreview data={result} accentColor={accentColor} primaryColor={primaryColor} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function ReportPreview({ data, accentColor, primaryColor }: { data: GenerateResponse; accentColor: string; primaryColor: string }) {
  const { report, report_id, ai_generated, ai_provider, ai_error, qa } = data;
  const pdfUrl = `${API_BASE}/api/report/${report_id}/pdf`;
  const pbipUrl = `${API_BASE}/api/report/${report_id}/export/pbip`;
  const badgeStyle = qa?.badge ? QA_BADGE_STYLE[qa.badge] : null;

  return (
    <div className="animate-[fadeIn_0.35s_ease-out]">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-display text-xl font-medium text-ink">{report.report_title}</h3>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <p className="text-xs text-ink-muted">{report.period_label}</p>
            {qa?.badge && badgeStyle && (
              <span
                className="rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide"
                style={{ backgroundColor: badgeStyle.bg, color: badgeStyle.fg, border: `1px solid ${badgeStyle.border}` }}
                title={qa.failing_checks.length > 0 ? `Failing checks: ${qa.failing_checks.join(", ")}` : undefined}
              >
                QA: {qa.badge}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <a
            href={pbipUrl}
            download
            className="whitespace-nowrap rounded-lg border px-4 py-2 text-xs font-semibold shadow-sm transition-colors hover:bg-black/2"
            style={{ color: primaryColor, borderColor: `${primaryColor}55`, backgroundColor: "white" }}
            title="Downloads a Power BI Project (.pbip) as a .zip — open AuroraHomeGoods.pbip in Power BI Desktop"
          >
            Export to Power BI
          </a>
          <a
            href={pdfUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="whitespace-nowrap rounded-lg px-4 py-2 text-xs font-semibold text-white shadow-sm transition-transform hover:-translate-y-px"
            style={{ backgroundColor: primaryColor }}
          >
            Download PDF
          </a>
        </div>
      </div>

      {ai_generated ? (
        <div className="mb-4 rounded-lg border-l-4 px-3.5 py-2.5 text-xs leading-relaxed" style={{ borderColor: "#0ca30c", backgroundColor: "#eaf7ea", color: "#1c4f1c" }}>
          Written by {ai_provider || "an AI model"}. Every number above still comes straight from the
          computed metrics — the model only writes the prose around them.
        </div>
      ) : (
        <div className="mb-4 rounded-lg border-l-4 px-3.5 py-2.5 text-xs leading-relaxed" style={{ borderColor: "#fab219", backgroundColor: "#fdf3e3", color: "#6b4d0a" }}>
          Draft narrative — no AI model was reachable (no ANTHROPIC_API_KEY, and no local Ollama server
          found), so this preview uses a deterministic template. Numbers are identical either way; only
          the prose differs.
          {ai_error ? <div className="mt-1 opacity-70">({ai_error})</div> : null}
        </div>
      )}

      {report.data_quality && report.data_quality.total_issues_found > 0 && (
        <div className="mb-4 rounded-lg border p-3.5" style={{ borderColor: "#6b8fc955", backgroundColor: "#f4f7fc" }}>
          <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide" style={{ color: "#2a4a7a" }}>
            Data quality — {report.data_quality.total_values_affected} value{report.data_quality.total_values_affected === 1 ? "" : "s"} auto-corrected
            across {report.data_quality.total_issues_found} check{report.data_quality.total_issues_found === 1 ? "" : "s"}
          </div>
          <ul className="list-inside list-disc space-y-1 text-xs text-ink-secondary">
            {report.data_quality.details.map((d, i) => <li key={i}>{d.message}</li>)}
          </ul>
        </div>
      )}

      {report.insights && report.insights.length > 0 && (
        <div className="mb-6">
          <div className="mb-2.5 text-xs font-semibold uppercase tracking-wide text-ink-muted">
            Insights worth acting on
          </div>
          <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
            {report.insights.map((card) => {
              const style = INSIGHT_STYLE[card.tag];
              return (
                <div key={card.id} className="rounded-xl border p-3.5 transition-shadow hover:shadow-sm"
                  style={{ backgroundColor: style.bg, borderColor: style.border + "40" }}>
                  <div className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: style.fg, opacity: 0.75 }}>
                    {card.title}
                  </div>
                  <div className="mt-1 font-display text-xl font-medium tabular-nums" style={{ color: style.border }}>
                    {card.headline}
                  </div>
                  {card.sub && <div className="mt-0.5 text-[11px] tabular-nums" style={{ color: style.fg }}>{card.sub}</div>}
                  <div className="mt-1.5 text-[11px] leading-snug" style={{ color: style.fg, opacity: 0.9 }}>
                    {card.detail}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="mb-5 rounded-lg p-3.5 text-sm leading-relaxed text-ink" style={{ backgroundColor: "var(--background)", borderLeft: `3px solid ${primaryColor}` }}>
        {report.executive_summary}
      </div>

      <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="rounded-xl border border-status-good/20 bg-status-good/4 p-3.5">
          <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-[#006300]">Highlights</div>
          <ul className="list-inside list-disc space-y-1 text-sm leading-relaxed text-ink-secondary">
            {report.highlights.map((h, i) => <li key={i}>{h}</li>)}
          </ul>
        </div>
        <div className="rounded-xl border border-status-warning/25 bg-status-warning/6 p-3.5">
          <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-[#a6540f]">Watch-outs</div>
          <ul className="list-inside list-disc space-y-1 text-sm leading-relaxed text-ink-secondary">
            {report.watchouts.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </div>
      </div>

      {report.sections.map((s, i) => (
        <div key={i} className="mb-6">
          <div className="mb-2.5 border-b pb-2 font-display text-base font-medium text-ink" style={{ borderColor: accentColor }}>
            {s.heading}
          </div>
          <p className="mb-2.5 text-sm leading-relaxed text-ink-secondary">{s.narrative}</p>
          {s.recommendations?.length > 0 && (
            <ul className="mb-3.5 list-inside list-disc space-y-1 text-sm leading-relaxed text-ink-secondary">
              {s.recommendations.map((r, j) => <li key={j}>{r}</li>)}
            </ul>
          )}
          {s.charts && s.charts.length > 0 && (
            <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
              {s.charts.map((c, j) => (
                <figure key={j} className="overflow-hidden rounded-lg border border-gridline bg-white p-2 shadow-sm">
                  {/* eslint-disable-next-line @next/next/no-img-element -- base64 data URI from the API, next/image adds nothing here */}
                  <img src={`data:image/png;base64,${c.img}`} alt={c.caption} className="h-auto w-full rounded" />
                  <figcaption className="mt-1.5 text-center text-[11px] text-ink-muted">{c.caption}</figcaption>
                </figure>
              ))}
            </div>
          )}
        </div>
      ))}

      <div className="mt-5 border-t border-gridline pt-4">
        <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-ink-muted">Next steps</div>
        <ol className="list-inside list-decimal space-y-1 text-sm leading-relaxed text-ink-secondary">
          {report.next_steps.map((n, i) => <li key={i}>{n}</li>)}
        </ol>
      </div>
    </div>
  );
}
