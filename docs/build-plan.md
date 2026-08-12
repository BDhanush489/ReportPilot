# ReportPilot — Prioritized Build Plan

Ordered per the mission brief. Lever 1 is implemented this session (see
`docs:changelog.md` and the code below); levers 2-4 are planned in file-level
detail so the next session can execute directly against this doc.

---

## Lever 1 — Warehouse connectivity (IMPLEMENTED this session)

**Goal:** onboard a client once (schema discovery + LLM-assisted column mapping),
then pull metrics via SQL on every future run — CSV upload remains the fallback path.

| File | Status | Purpose |
|---|---|---|
| `backend/app/connectors/base.py` | new | `Connector` protocol: `list_tables()`, `list_columns(table)`, `run_query(sql) -> DataFrame` | 
| `backend/app/connectors/sqlite_connector.py` | new, **tested** | Concrete connector over a local SQLite file — the only backend actually exercised end-to-end in this sandbox (no live warehouse credentials available) |
| `backend/app/connectors/postgres_connector.py` | new, **written to spec, untested** | `psycopg2`/SQLAlchemy-based; same `Connector` interface. Needs a real DSN to verify |
| `backend/app/connectors/snowflake_connector.py`, `bigquery_connector.py`, `databricks_connector.py` | new, **written to spec, untested** | Each follows that vendor's documented Python driver (`snowflake-connector-python`, `google-cloud-bigquery`, `databricks-sql-connector`). No credentials in this environment to verify against — flagged in each file's docstring |
| `backend/app/data_context.py` | new | Schema discovery (`list_tables`/`list_columns`) + one LLM call (via the existing Claude→Ollama→template chain) that proposes a column mapping from the client's real schema to ReportPilot's canonical fields (`date`, `sessions`, `revenue_usd`, ...). The mapping is **stored and reused**, not re-derived every run — this is the "onboard once" unlock. **The LLM never touches metric values, only column *names* — the invariant holds untouched** |
| `backend/app/sql_source.py` | new | Given a connector + a saved data context, runs the canonical extraction queries and returns DataFrames shaped identically to `parsers.py`'s output, so `metrics.py` needs **zero changes** — it already only knows about DataFrames |
| `backend/app/main.py` | modified | New endpoints: `POST /api/data-sources/test`, `POST /api/data-sources/onboard`, `GET /api/data-sources`, and `generate-report` gains an optional `client_id` path that pulls from a saved data context instead of uploaded files |
| `backend/data_contexts/` | new dir | Per-client saved mapping, e.g. `data_contexts/aurora-home-goods.json` |
| `frontend` | modified | New "Connect a data source" flow alongside the existing file-upload path |

**Effort if built from scratch by a team:** 3-5 dev-days (connector abstraction +
one real driver + LLM mapping + persistence + endpoints + frontend). This session
implements the full architecture with the SQLite path verified live.

---

## Lever 2 — Auto-QA / defensibility badge (planned, not built this session)

| File | Status | Purpose |
|---|---|---|
| `backend/app/qa.py` | new | Three checks, all deterministic: (1) **traceability** — regex-extract every number in the LLM narrative and confirm it matches a value in `metrics_payload` within rounding tolerance, flagging anything that doesn't trace back (this generalizes the existing `_OVERPRECISE_NUMBER` guard in `agent.py` into a full trace-back check, not just a precision smell test); (2) **aggregation sanity** — spot-check that channel-level sums reconcile to the reported total within a tolerance; (3) **unsupported-claim scan** — flag narrative sentences containing a causal/comparative claim ("because," "driven by," "compared to") that isn't backed by a metric the model was actually given |
| `backend/app/agent.py` | modified | Call `qa.run_qa(report, metrics_payload)` after `_validate_report_shape`; attach `report["qa"] = {"passed": bool, "checks": [...]}` |
| `backend/app/templates/report.html` | modified | A "QA passed" badge near the cover page and a methodology footnote listing what was checked |
| `frontend/src/app/page.tsx` | modified | Badge in the preview header |

**Effort estimate:** 1-2 dev-days — the traceability check is the only non-trivial
piece (needs careful float-tolerance matching against nested metric dicts).

---

## Lever 3 — Recurring, scheduled reports (planned, not built this session)

| File | Status | Purpose |
|---|---|---|
| `backend/app/scheduler.py` | new | Per-client schedule records (`{client_id, frequency, day_of_month/week, data_context_id, branding}`); a background loop (or `APScheduler`) that fires `report_builder.build_report` using the client's saved `sql_source` on schedule |
| `backend/app/history.py` | new | Given a `client_id`, loads the most recent prior persisted report's metrics snapshot and diffs it against the current run — period-over-period deltas become **input JSON** to the narrative step (still deterministic numbers; the LLM's job is explaining *why*, same invariant) |
| `backend/app/main.py` | modified | CRUD endpoints for schedules (`POST/GET/DELETE /api/schedules`) |
| `backend/generated/` layout | modified | Reorganize to `generated/<client_id>/<report_id>/` so `history.py` has a client-scoped timeline to diff against |
| `frontend` | modified | "Recurring" toggle + frequency picker per client on the onboarding flow |

**Effort estimate:** 2-3 dev-days — the diffing/"what changed and why" narrative
prompt is the interesting part; the scheduler itself is routine.

---

## Lever 4 — Third deliverable: generated interactive HTML dashboard (planned, not built this session)

| File | Status | Purpose |
|---|---|---|
| `backend/app/html_dashboard.py` | new | Emits one self-contained HTML file (inline CSS/JS, no CDN deps) with KPI cards, a channel/period filter, and drill-down into per-section detail — sourced from the same `metrics_payload` + `insights` used by the PDF and PBIP, and the same `charts.py` palette constants, so all three deliverables are visually and numerically identical |
| `backend/app/main.py` | modified | `GET /api/report/{id}/dashboard` |
| `powerbi/validate_pbip.py`, `powerbi/check_field_references.py` | **unchanged — re-run as a regression gate** | No PBIP schema/model changes are needed for this lever; both scripts must still report all-green after the HTML dashboard ships, confirming lever 4 didn't regress the existing Power BI deliverable |

**Effort estimate:** 2-3 dev-days — a real filter/drill-down interaction without a
JS framework is the main time sink; the data plumbing is already shared.

---

## Sequencing rationale

Lever 1 is built first because levers 3 and 4 both assume a report has a stable,
re-runnable **source** (a saved data context), not just a one-off file upload —
building recurring scheduling or a third deliverable on top of "re-upload the same
3 files each time" would be building on sand. Lever 2 (QA badge) has no dependency
on lever 1 and could in principle ship in parallel; it's sequenced second here
because Segment 1 (the top-ranked ICP) cares about lever 3 more, and lever 3 needs
lever 1 underneath it.
