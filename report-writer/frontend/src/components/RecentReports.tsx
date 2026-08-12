"use client";

import { API_BASE } from "@/lib/api";
import { Skeleton } from "@/components/Skeleton";
import type { RecentReport } from "@/lib/types";

type Props = {
  reports: RecentReport[];
  loading: boolean;
  loadError: string | null;
  viewingId: string | null;
  primaryColor: string;
  onRetry: () => void;
  onView: (reportId: string) => void;
};

function formatDate(iso: string) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(d);
}

export default function RecentReports({
  reports,
  loading,
  loadError,
  viewingId,
  primaryColor,
  onRetry,
  onView,
}: Props) {
  return (
    <section
      aria-label="Recent reports"
      className="rounded-xl border border-[#e1e0d9] bg-white p-5"
    >
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-900">Recent reports</h2>
        <button
          type="button"
          onClick={onRetry}
          disabled={loading}
          className="rounded text-xs text-neutral-400 hover:text-neutral-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-neutral-400 disabled:opacity-40"
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {loading && reports.length === 0 && (
        <div className="space-y-2" aria-hidden>
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      )}

      {loadError && !loading && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          {loadError}{" "}
          <button
            type="button"
            onClick={onRetry}
            className="font-semibold underline underline-offset-2"
          >
            Try again
          </button>
        </div>
      )}

      {!loading && !loadError && reports.length === 0 && (
        <p className="text-xs text-neutral-400">
          Nothing generated yet. Reports you generate are saved here — they
          persist on disk, so they survive a backend restart.
        </p>
      )}

      <ul className="space-y-2">
        {reports.map((r) => {
          const isViewing = viewingId === r.report_id;
          return (
            <li
              key={r.report_id}
              className="flex items-center justify-between gap-2 rounded-md border border-[#e1e0d9] px-3 py-2 transition-colors hover:border-[#c3c2b7]"
            >
              <div className="min-w-0">
                <div className="truncate text-xs font-medium text-neutral-800">
                  {r.report_title || r.client_name || "Report"}
                </div>
                <div className="text-[11px] text-neutral-400">
                  {formatDate(r.created_at)}
                  {" · "}
                  {r.ai_generated ? r.ai_provider || "AI-written" : "draft narrative"}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-3">
                <button
                  type="button"
                  onClick={() => onView(r.report_id)}
                  disabled={isViewing}
                  className="rounded text-xs font-medium text-neutral-600 hover:text-neutral-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-neutral-400 disabled:opacity-40"
                >
                  {isViewing ? "Loading…" : "View"}
                </button>
                <a
                  href={`${API_BASE}/api/report/${r.report_id}/pdf`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rounded text-xs font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-neutral-400"
                  style={{ color: primaryColor }}
                >
                  PDF
                </a>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}