"use client";

import { API_BASE } from "@/lib/api";
import type { GenerateResponse } from "@/lib/types";

type Props = {
  data: GenerateResponse;
  accentColor: string;
  primaryColor: string;
};

const QA_BADGE_STYLE: Record<string, { bg: string; border: string; fg: string }> = {
  "PASS": { bg: "#e8f5e9", border: "#0ca30c", fg: "#006300" },
  "PASS-WITH-WARNINGS": { bg: "#fdf3e3", border: "#fab219", fg: "#a6540f" },
  "FAIL": { bg: "#fbe9e7", border: "#d03b3b", fg: "#a01f1f" },
};

export default function ReportPreview({ data, accentColor, primaryColor }: Props) {
  const { report, report_id, ai_generated, ai_provider, ai_error, qa } = data;
  const pdfUrl = `${API_BASE}/api/report/${report_id}/pdf`;
  const badgeStyle = qa?.badge ? QA_BADGE_STYLE[qa.badge] : null;

  return (
    <article aria-label={`Report: ${report.report_title}`}>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-lg font-semibold text-neutral-900">
            {report.report_title}
          </h3>
          <div className="mt-1 flex items-center gap-2">
            <p className="text-xs text-neutral-400">{report.period_label}</p>
            {qa?.badge && badgeStyle && (
              <span
                className="rounded px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide"
                style={{ backgroundColor: badgeStyle.bg, color: badgeStyle.fg, border: `1px solid ${badgeStyle.border}` }}
                title={qa.failing_checks.length > 0 ? `Failing checks: ${qa.failing_checks.join(", ")}` : undefined}
              >
                QA: {qa.badge}
              </span>
            )}
          </div>
        </div>
        <a
          href={pdfUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="rounded-md px-4 py-2 text-xs font-semibold text-white whitespace-nowrap transition-opacity hover:opacity-90 focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-1"
          style={{ backgroundColor: primaryColor }}
        >
          Download / view PDF
        </a>
      </div>

      {ai_generated ? (
        <div
          className="mb-4 rounded-md border-l-4 px-3 py-2 text-xs"
          style={{ borderColor: "#0ca30c", backgroundColor: "#eaf7ea", color: "#1c4f1c" }}
        >
          Written by {ai_provider || "an AI model"}. Every number above still comes
          straight from the computed metrics — the model only writes the prose
          around them.
        </div>
      ) : (
        <div
          className="mb-4 rounded-md border-l-4 px-3 py-2 text-xs"
          style={{ borderColor: "#fab219", backgroundColor: "#fdf3e3", color: "#6b4d0a" }}
        >
          {/* Draft narrative — no AI model was reachable (no ANTHROPIC_API_KEY, and no
          local Ollama server found), so this preview uses a deterministic
          template. Numbers are identical either way; only the prose differs.
          {ai_error ? <div className="mt-1 opacity-70">({ai_error})</div> : null} */}
        </div>
      )}

      <div
        className="mb-4 rounded-md p-3 text-sm text-neutral-800"
        style={{ backgroundColor: "#fcfcfb", borderLeft: `4px solid ${primaryColor}` }}
      >
        {report.executive_summary}
      </div>

      <div className="mb-5 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <div
            className="mb-1.5 text-xs font-semibold uppercase tracking-wide"
            style={{ color: "#006300" }}
          >
            Highlights
          </div>
          <ul className="list-inside list-disc space-y-1 text-sm text-neutral-700">
            {report.highlights.map((h, i) => (
              <li key={i}>{h}</li>
            ))}
          </ul>
        </div>
        <div>
          <div
            className="mb-1.5 text-xs font-semibold uppercase tracking-wide"
            style={{ color: "#a6540f" }}
          >
            Watch-outs
          </div>
          <ul className="list-inside list-disc space-y-1 text-sm text-neutral-700">
            {report.watchouts.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      </div>

      {report.sections.map((s, i) => (
        <section key={i} className="mb-5">
          <h4
            className="mb-2 border-b pb-1.5 text-sm font-semibold text-neutral-900"
            style={{ borderColor: accentColor }}
          >
            {s.heading}
          </h4>
          <p className="mb-2 text-sm text-neutral-700">{s.narrative}</p>
          {s.recommendations?.length > 0 && (
            <ul className="mb-3 list-inside list-disc space-y-1 text-sm text-neutral-600">
              {s.recommendations.map((r, j) => (
                <li key={j}>{r}</li>
              ))}
            </ul>
          )}
          {s.charts && s.charts.length > 0 && (
            <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
              {s.charts.map((c, j) => (
                <figure key={j} className="rounded-md border border-[#e1e0d9] p-2">
                  {/* base64 data URI from the API — next/image adds nothing here */}
                  <img
                    src={`data:image/png;base64,${c.img}`}
                    alt={c.caption}
                    loading="lazy"
                    className="h-auto w-full rounded"
                  />
                  <figcaption className="mt-1 text-center text-[11px] text-neutral-400">
                    {c.caption}
                  </figcaption>
                </figure>
              ))}
            </div>
          )}
        </section>
      ))}

      <div className="mt-4 border-t border-[#e1e0d9] pt-3">
        <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-neutral-500">
          Next steps
        </div>
        <ol className="list-inside list-decimal space-y-1 text-sm text-neutral-700">
          {report.next_steps.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ol>
      </div>
    </article>
  );
}