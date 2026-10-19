"use client";

import { useEffect, useState } from "react";
import { errorMessage, fetchJson } from "@/lib/api";

export type SourceMapping = {
  table: string | null;
  column_map: Record<string, string | null>;
  mapping_method: string | null;
  discovered_columns: string[];
  canonical_fields: string[];
  required_field: string | null;
  missing_required: boolean;
};

type MappingResponse = { client_id: string; sources: Record<string, SourceMapping> };

const SOURCE_LABEL: Record<string, string> = { analytics: "Web analytics", seo: "SEO", sales: "Sales" };

const SELECT_CLASS =
  "w-full rounded-md border border-gridline bg-white px-2.5 py-1.5 text-xs text-ink shadow-sm outline-none transition-colors focus:border-brand focus:ring-4 focus:ring-brand/10";

/** Per-client column-mapping editor: one dropdown per canonical field, listing
 * the table's real columns. The fix for a mapping onboarding guessed wrong
 * (no AI provider -> fuzzy matching), without removing and re-connecting. */
export default function MappingEditor({ clientId, onClose }: { clientId: string; onClose: () => void }) {
  const [sources, setSources] = useState<Record<string, SourceMapping> | null>(null);
  const [draft, setDraft] = useState<Record<string, Record<string, string | null>>>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  function applyResponse(data: MappingResponse) {
    setSources(data.sources);
    setDraft(Object.fromEntries(Object.entries(data.sources).map(([st, s]) => [st, { ...s.column_map }])));
  }

  useEffect(() => {
    fetchJson<MappingResponse>(`/api/data-sources/${encodeURIComponent(clientId)}/mapping`)
      .then(applyResponse)
      .catch((e) => setLoadError(errorMessage(e, "Could not load this client's mapping.")));
  }, [clientId]);

  function setField(sourceType: string, field: string, column: string) {
    setSaved(false);
    setDraft((d) => ({ ...d, [sourceType]: { ...d[sourceType], [field]: column || null } }));
  }

  async function save() {
    if (!sources) return;
    // Send only what changed, so an untouched field keeps its saved value.
    const overrides: Record<string, Record<string, string | null>> = {};
    for (const [st, fields] of Object.entries(draft)) {
      for (const [field, column] of Object.entries(fields)) {
        if ((sources[st].column_map[field] ?? null) !== column) {
          (overrides[st] ||= {})[field] = column;
        }
      }
    }
    if (Object.keys(overrides).length === 0) {
      onClose();
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      const data = await fetchJson<MappingResponse>(`/api/data-sources/${encodeURIComponent(clientId)}/mapping`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ overrides }),
      });
      applyResponse(data);
      setSaved(true);
    } catch (e) {
      setSaveError(errorMessage(e, "Could not save the mapping."));
    } finally {
      setSaving(false);
    }
  }

  if (loadError) {
    return <p className="text-xs" style={{ color: "#a01f1f" }}>{loadError}</p>;
  }
  if (!sources) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-ink-muted">
        <span className="h-3 w-3 animate-spin rounded-full border-2 border-brand/30 border-t-brand" />
        Loading mapping…
      </span>
    );
  }
  if (Object.keys(sources).length === 0) {
    return <p className="text-xs text-ink-muted">This source has no column mapping to edit.</p>;
  }

  return (
    <div className="space-y-5">
      <p className="text-xs leading-relaxed text-ink-secondary">
        Pick the column in each table that holds each field. Fields marked <strong>required</strong> must be
        mapped before a report can be generated; anything else left unmapped is filled with a default.
      </p>

      {Object.entries(sources).map(([st, src]) => (
        <div key={st}>
          <h3 className="text-xs font-semibold text-ink">
            {SOURCE_LABEL[st] || st}
            <span className="ml-1.5 font-normal text-ink-muted">
              table <code className="rounded bg-neutral-100 px-1 py-0.5">{src.table}</code>
              {src.mapping_method && <> · mapped by {src.mapping_method.replace("_", " ")}</>}
            </span>
          </h3>
          <div className="mt-2 grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-2">
            {src.canonical_fields.map((field) => {
              const value = draft[st]?.[field] ?? "";
              const required = field === src.required_field;
              const missing = required && !value;
              return (
                <label key={field} className="flex items-center gap-2">
                  <span className="w-40 shrink-0 truncate font-mono text-[11px] text-ink-secondary" title={field}>
                    {field}
                    {required && (
                      <span className="ml-1 font-sans font-semibold" style={{ color: missing ? "#a01f1f" : undefined }}>
                        required
                      </span>
                    )}
                  </span>
                  <select
                    value={value}
                    onChange={(e) => setField(st, field, e.target.value)}
                    className={SELECT_CLASS}
                    style={missing ? { borderColor: "#d03b3b" } : undefined}
                  >
                    <option value="">— not mapped —</option>
                    {src.discovered_columns.map((col) => (
                      <option key={col} value={col}>{col}</option>
                    ))}
                  </select>
                </label>
              );
            })}
          </div>
        </div>
      ))}

      {saveError && <p className="text-xs" style={{ color: "#a01f1f" }}>{saveError}</p>}
      {saved && <p className="text-xs" style={{ color: "#1c4f1c" }}>Mapping saved. Generate the report again.</p>}

      <div className="flex gap-2.5">
        <button
          type="button"
          onClick={save}
          disabled={saving}
          className="rounded-lg bg-brand px-3.5 py-2 text-xs font-semibold text-white shadow-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save mapping"}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg border border-gridline bg-white px-3.5 py-2 text-xs font-semibold text-ink-secondary shadow-sm transition-colors hover:bg-black/2"
        >
          Close
        </button>
      </div>
    </div>
  );
}
