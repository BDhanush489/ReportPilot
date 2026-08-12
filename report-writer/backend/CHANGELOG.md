# Real connectors: Google Search Console, GA4, and PageSpeed Insights

Three new connectors in `app/connectors/` — the first ones here that pull
from a live Google API instead of a warehouse table or an inbox. All three
plug into the exact onboarding/report pipeline every SQL connector already
uses (`POST /api/data-sources/test`, `/onboard`, and
`report_builder.build_report_from_data_context`) with zero changes to
`main.py`, `data_context.py`, or `sql_source.py` — `GSCConnector`,
`GA4Connector`, and `PageSpeedConnector` all answer
`list_tables()`/`list_columns()`/`run_query()` the same shape a warehouse
connector does; `run_query()` just serves an already-fetched DataFrame
instead of issuing real SQL, via a new shared helper
(`connectors/base.py`'s `parse_select_as`/`select_columns_for_query`) that
parses the narrow `SELECT real AS alias, ... FROM table` shape
`sql_source._select_mapped_columns` always generates. Internal column names
were chosen to equal `data_context.py`'s canonical field names exactly, so
the AI/fuzzy-match column-mapping step onboarding normally needs becomes a
trivial identity match.

`GA4Connector` maps GA4's Data API dimensions/metrics onto every one of
`data_context.py`'s canonical "analytics" fields (date, channel_group,
device_category, sessions, new_users, engaged_sessions, conversions,
revenue_usd, bounce_rate, avg_session_duration_sec) — a full mapping.
`GSCConnector` covers the subset of the canonical "seo" fields Search
Console actually reports (url, clicks_28d, impressions_28d, ctr,
avg_position); the rest stay unmapped, same as any partial source, picked up
by `normalize_seo_audit`'s existing default-filling. Deliberately did NOT
equate GSC's `clicks` with the canonical `organic_sessions_28d` field — a
click isn't a session, and fabricating that equivalence would have been a
silent accuracy regression for the sake of filling one more column.
`PageSpeedConnector` covers `url`/`load_time_ms`/`mobile_friendly`; it audits
one URL per API call, so the URL list to audit is explicit connector config,
not discovered (pairs naturally with `GSCConnector`'s real top URLs, but
works standalone).

## Auth: two postures, both machine-to-machine, neither touching app/auth.py

GSC and GA4 both authenticate via a Google Cloud **service account** — a
credential the tenant generates themselves (Cloud Console -> IAM & Admin ->
Service Accounts -> JSON key), then grants read access to their own
property/site. New shared module `app/connectors/google_auth.py` signs the
RFC 7523 JWT-bearer assertion with `cryptography` (already a hard dependency
— reused from `data_context.py`'s Fernet encryption, no new install) and
exchanges it for a bearer token over plain `urllib`, matching this
codebase's established "stdlib HTTP over a vendor SDK" posture
(`slack_source.py`, `email_source.py`) rather than adding `google-auth`/
`google-api-python-client` as dependencies. This has nothing to do with
`app/auth.py`'s Authlib "Sign in with Google" flow, which authenticates a
human via browser redirect — a completely separate credential for a
completely separate purpose, same distinction already drawn for Authlib's
own OAuth-handshake cookie vs. the app's real session cookie.

PageSpeed needs no service account at all — just an optional API key
(`config["api_key"]`, falling back to a new `PAGESPEED_API_KEY` env var).
"Optional" turned out to be theoretical: a live smoke test against the real
API in this environment returned HTTP 429 ("Quota exceeded ... Queries per
day") with no key at all, so the docs and code comments now say a key is
required in practice, not just quota-raising — getting one is still free and
instant (Cloud Console -> enable "PageSpeed Insights API" -> Credentials ->
Create API key, no OAuth, no review).

## Zero new required dependencies

All three connectors run on what was already in `requirements.txt`
(`cryptography`, stdlib `urllib`) — unlike the four vendor-SDK warehouse
connectors (`requirements-connectors.txt`), nothing extra to install. GSC
and GA4 are unverified against a real Google Cloud project in this
environment (same "written to the documented API, untested live for lack of
credentials" posture already stated for Postgres/Snowflake/BigQuery/
Databricks) — real unit tests cover every code path against mocked HTTP,
and a live smoke test is one real service account away. PageSpeed was
smoke-tested directly against the live public API.

Tests: `tests/test_connectors_base.py` (6), `tests/test_google_auth.py` (5,
including a real throwaway RSA keypair generated per test so JWT signing
runs for real, not just the mocked token exchange), `tests/test_gsc_connector.py`
(9), `tests/test_ga4_connector.py` (5), `tests/test_pagespeed_connector.py`
(8) — 33 new tests, full existing suite still green.

# E1 — real auth, multi-tenancy, and access control

ReportPilot had no database and no real access control before this: every
report/schedule/data-source/alert-config/delivery-log was a flat JSON file
keyed by a bare, unauthenticated `client_id`/`report_id`, gated (if at all)
by one shared `X-API-Key` secret that granted access to everything or
nothing. This closes that gap: real Google OAuth login, per-user DB-backed
sessions, and per-tenant isolation enforced at the storage layer, not just
the API layer.

## Data model

New SQLite database (`app/db.py`, SQLAlchemy 2.0 + Alembic), four tables:
`User` (keyed on Google's `sub`, never email — email can be reassigned at
Google's end, so keying on it would let a changed address silently take
over a different user's account), `Tenant`, `Membership` (schema
forward-built for a future invite flow — `role` exists, nothing creates a
`member` row yet), `AuthSession` (DB-backed, not JWT — a database already
exists at this scale so JWT's statelessness buys nothing, while a DB row
gives real logout/revocation; only `sha256(token)` is ever stored, same
reasoning as a password hash). `alembic upgrade head` is an explicit,
documented step — never auto-run on startup, matching this app's existing
"nothing happens by accident" convention (`AUTO_SCHEDULER_ENABLED`, etc.).

First Google login auto-creates a Tenant + owner Membership (there's no
invite flow, so a second person at the same agency who signs in gets their
OWN separate tenant, not membership in a colleague's — a real, stated
limitation, not hidden).

## Structural tenant isolation

Every one of the five on-disk JSON stores (`report_store.py`,
`data_context.py`, `scheduler.py`, `alerts.py`, `delivery.py`) is now
namespaced by path — `{DIR}/{tenant_id}/{client_id}.json`, not
`{DIR}/{client_id}.json` plus a `tenant_id` field inside. A design review
before writing any code caught that the field-only version doesn't actually
prevent two tenants naming a client the same thing (e.g. "acme") from
silently overwriting each other's saved warehouse credentials — a real
data-loss bug the path-based fix closes structurally: a cross-tenant read
404s because the file genuinely isn't there. `tenant_id` is a required
parameter everywhere in the store layer, no default — a forgotten argument
is a `TypeError`, never a silent shared bucket.

The same review caught two more real gaps before they shipped: `main.py`'s
in-memory `_REPORTS`/`_JOBS` caches are a SECOND access path to report data
that bypasses any check added only to the disk loaders (now tenant-stamped
at creation, checked on every read); and `scheduler.list_schedules()` being
one function for both the cron job (needs every tenant's due schedules) and
a user-triggered `POST /api/schedules/run` would let any tenant trigger
report generation/delivery for every OTHER tenant — split into
`list_all_schedules()` (infra-only) and `list_schedules_for_tenant()`.

## Access control

`Depends(auth.get_tenant_id)` on all ~22 tenant-scoped routes, two ways in:
a browser session cookie, or an `X-API-Key` matching a live `ApiToken` (see
below) — session wins if both are somehow present. The old blanket
`X-API-Key`/`API_KEY` middleware is gone entirely. `POST /api/schedules/run`
gets its own dual path instead: a valid `SCHEDULER_SERVICE_TOKEN` (cron,
touches every tenant, intentional) or a session (touches only that tenant).

CSRF (double-submit): a non-httpOnly `rp_csrf` cookie minted alongside the
session at login, echoed as `X-CSRF-Token` on every non-GET request,
compared with `hmac.compare_digest`. Enforced once, app-wide
(`FastAPI(dependencies=[Depends(auth.require_csrf)])`) so a newly added
mutating route is protected by default rather than only when someone
remembers to add the check. Only applies when a session cookie is present —
a machine caller (API token, service token) was never CSRF-vulnerable in
the first place, since a malicious page can ride a victim's cookies into a
forged request but can't read or attach a secret it was never given.

Per-tenant `ApiToken`s (`POST/GET /api/auth/tokens`, `DELETE
/api/auth/tokens/{id}`) were a mid-implementation addition, not in the
original plan: `client_agent.exe` (Ingestion Mode 1 — a scheduled Windows
task on a *client's* machine pushing files to `/api/generate-report`) has
no browser and used to authenticate with the old shared `X-API-Key`. Removing
that middleware broke it outright. The fix is a real per-tenant credential,
not a shared secret: minted from the browser while logged in, resolves to
exactly that tenant, sha256-hashed at rest like a session, no expiry (a
scheduled job needs to keep working indefinitely once configured), revoked
by deleting the row. `client_agent.py` itself needed zero code changes — it
already sent the right header name.

## Frontend

`src/lib/auth-context.tsx` (`AuthProvider`/`useAuth()`, checks
`/api/auth/me` on mount), `src/app/login/page.tsx` (a real top-level
navigation to `/api/auth/google/login`, not a fetch — the backend owns the
whole redirect round-trip and never hands the frontend an authorization
code), `src/lib/csrf.ts`. Every fetch call now carries `credentials:
"include"`; the SSE progress stream needed `{ withCredentials: true }`
specifically (`EventSource`, unlike `fetch`, defaults to NOT sending
cookies cross-origin). Route protection is client-side (`useEffect` redirect
to `/login`), not Next middleware — the session cookie lives on the
BACKEND's origin, which Next middleware running on the frontend's own
origin can never read.

## A pre-existing, unrelated bug found and fixed along the way

Verifying "full suite green" surfaced 18 failing tests in `app/viz/`
(schema-agnostic chart engine) that had nothing to do with this track —
confirmed by file mtimes predating this work entirely. Root cause:
`profiler.py` classifies numeric columns as `numeric_quantity` /
`numeric_identifier`, but `suitability.py`/`aggregates.py`/`engine.py`/
`suggestions.py` still checked for a bare `"numeric"`/`"id"` — a
vocabulary drift from an earlier profiler.py refactor that was never
propagated to its consumers. Fixed by making `suitability.py` (the one
module genuinely shared by TWO independent callers with two different,
non-overlapping vocabularies — `app/viz`'s own richer one, and
`chart_intelligence.py`'s deliberately narrower 3-way one for the
fixed-schema report pipeline) accept both spellings as synonyms, rather
than a blind rename that would have silently broken the main report
pipeline's chart-suitability checks. Two more independent bugs surfaced in
the same test file once that fix let its tests actually run:
`_load_fixture()` not unpacking `load_any()`'s documented `(df, load_meta)`
tuple, and a stale assertion expecting an alphanumeric ID column
("ORD00042") to classify as `numeric_identifier` when it's genuinely
`categorical` (not numeric-parseable at all).

Also found and fixed, while wiring E1's own report-generation path: a real
pre-existing race in `main.py` where the in-memory report cache went live
*before* the disk write it was supposed to mirror completed, so a poller
could get a cache-hit 200 and then read disk directly before the file
existed. Fixed by persisting before caching, not the other way around.

## Explicitly out of scope this pass

Billing, Track I integrations, multiple users per tenant (the `role` field
is forward-built but inert), migrating existing on-disk dev fixtures into
tenant-namespaced paths (the `aurora-home-goods` demo data context moved to
a fixed `demo-tenant/` path purely so its own tests keep working — not a
general migration tool), `data_context.py`'s pre-existing optional
credential-at-rest encryption gap (real, separate, not bundled here), a
Postgres migration itself (only the SQLAlchemy/Alembic abstraction that
makes it a later config change is built now).

Tests: `tests/test_db_models.py` (8), `tests/test_auth.py` (29, covering
OAuth tenant-creation, DB-backed sessions, and the new API-token
mechanism), `tests/test_tenant_isolation.py` (11 test functions / 32
collected cases — the 10 cross-tenant scenarios from the design review,
including a parametrized sweep of all 22 tenant-scoped routes 401ing with
no session), `tests/test_csrf.py` (10), `tests/test_api_key_auth.py`
(rewritten, 11) — plus every existing test file that touched the
now-tenant-scoped store layer updated for the new required `tenant_id`
parameter. Full suite green throughout, including the `app/viz/` fixes
above (frontend: `tsc --noEmit`, ESLint, and `next build` all clean; one
manual step remains — a real Google login click-through, since that's the
one part of this that genuinely needs a human with real credentials).

# Graph-of-loops continuation: T3, T4, P, B4, D2.2, D2.3, W1, W2

Continuing the mission graph node by node after T1/T2. An audit agent first
confirmed A1-A3, B1-B3, C1, D1, D2.0-D2.1 were already green from prior work
(with real file:line evidence, not assumed) and precisely identified what
was actually missing: B4 and D2.2 entirely unbuilt, W1 mostly cosmetic
(only logo/2 colors truly wired, and the logo never even rendered in the
PDF/dashboard), W2 had no real report-to-report diff. That audit drove
everything below.

## T3 — template versioning
Every spec file now carries `version: N`; a "bump" is a new
`{id}.v{N}.json` file, never an edit in place. `ReportObject` records the
RESOLVED `template_id`/`template_version` (not the caller's possibly-"latest"
request). New `scheduler.regenerate_run()` reads a past report's recorded
version and rebuilds pinned to exactly that — proven by bumping a template
mid-test and confirming the regenerated report's chart list matches the
original, not the bump. 8 tests.

## T4 — two maximally different templates
"Full Monthly Report" (the existing 11-chart, 3-section deep template) and
new "Executive Summary" (1 chart per section, tone=executive — genuinely
one-page synthesis). `TemplateSpec` gained `label`/`description`/`hidden`/
`prompt_guidance`; `template_specs.list_templates()` + `GET /api/templates`
+ a frontend radio picker expose real selection end-to-end. T1's
`analytics_only` fixture template is now `hidden: true` (a proof-of-concept,
not a product template) rather than competing with the two real ones.
Caught and fixed a real pre-existing bug while proving this against real
data: `qa._flatten_numeric`/`_numeric_diff` didn't handle Python tuples,
so every `seo_metrics().top_issues` count (a list of `(name, count)`
tuples) was silently untraceable — a real aurora-home-goods report's badge
came back FAIL for a genuinely correct number. Also removed a hardcoded,
untraceable "28" ("trailing 28 days") from the SEO fallback narrative. 8 +
regression tests.

## P — one industry pack, as pure data
`local_service_business.v1.json`: skips web analytics (local-service clients
care about calls/booked jobs, not raw traffic), keeps SEO + lead-source
performance, carries real `prompt_guidance` text steering the narrative
toward booked-jobs framing. Zero `.py` changes — every field it uses
(`label`, `prompt_guidance`, section/chart selection) already existed for
T4. 6 tests, including a source-grep proving no renderer file mentions its id.

## B4 — KPI alerts
New `app/alerts.py`: per-client `AlertRule` (metric path + direction +
threshold), breach detection reuses B1's `MetricDelta` verbatim (never a
second recompute), small-N suppressed (prior < 10 is noise not signal), a
FAIL-badge report alerts on nothing (same trust rule B3 delivery already
enforces), dedup'd per as_of so a re-run never re-fires. Delivery reuses
`delivery.py`'s channel/logging machinery directly — a different subject/
body through the same `send()`, not a parallel system. Wired into
`scheduler.run_schedule`; `POST/GET /api/alerts` manage rules. 17 tests,
including a real alert fired end-to-end off a real revenue drop between two
scheduler runs.

## D2.2 — Power BI measure correctness
Real DAX measures now ship on the tables that host them (`Total Revenue`,
`Total Sessions`, `Total Conversions`, `Sales Total Revenue`, `Total Pages
Crawled`) — plain `SUM()` over pre-aggregated columns, the only DAX shape
this architecture's already-aggregated tables can honestly support (see the
module's own note on why replicating metrics.py's groupby in M would be a
second, forbidden metrics path). No live Power BI engine exists in this
pipeline to execute the DAX, so "recompute and reconcile" means: sum the
exact rows about to be embedded (what a live measure would operate over)
and compare against the canonical `ReportObject` value. A mismatch raises
`MeasureReconciliationError` and blocks the export outright — proven with a
tampered metric that leaves nothing on disk, not a partial export.
Deliberately no measure on `SeoTopIssues` (a top-8 subset — summing it
doesn't equal any real total, so no measure claims it does). Also closes
"the badge travels": new `QaSummary` table carries the same `qa.badge`
every other surface shows, plus a fixed methodology note. Golden fixture
regenerated; caught along the way that this environment's reachable local
Ollama server made the fixture's own badge non-deterministic the moment a
badge started appearing in a committed file — fixed by forcing the
deterministic path in the fixture, not by weakening the comparison. 33 + 6
new tests.

## D2.3 — live warehouse connection
Additive `connection_mode="live"` (default stays `"snapshot"`, byte-for-byte
unchanged). Live mode DirectQueries the client's actual raw warehouse table
using each connector's real native Power Query function
(`PostgreSQL.Database`, `Snowflake.Databases`, `GoogleBigQuery.Database`,
`Databricks.Catalogs`) with only non-secret topology (host/database/
warehouse/catalog) — every password/token/DSN-with-credentials is parsed
out and discarded; Desktop's own credential manager handles auth on first
refresh. Stated architectural boundary, not hidden: live tables are raw-
shaped (a column rename, zero aggregation), not a live version of the
pre-aggregated snapshot tables — replicating metrics.py's groupby logic in
M would be a second metrics path. sqlite (this project's own real demo
connector) has no native Power Query connector at all; live mode degrades
explicitly with a stated reason rather than silently falling back to
snapshot — proven against the real aurora-home-goods data context, not just
a hypothetical. 12 tests, including a no-secrets-anywhere grep across every
byte actually written to disk.

## W1 — real white-label
Closed the exact gaps the audit found. Logo now renders on the PDF cover
AND the dashboard header (previously Power-BI-export-only). New optional
branding fields — `font_family`, `footer_text`, `signature_name`/`title`,
`disclaimer_text` — flow through one shared `theme.to_template_context()`
override point so the PDF and dashboard can't drift on how they're applied.
The default PDF footer no longer says "...'s AI Report Writer" (a product
self-reference with no way to turn off) — now just "Generated by
{agency_name}," fully overridable. Frontend gained a collapsed "advanced
white-label" section. 16 tests, including a zero-"ReportPilot"-anywhere
grep on both the PDF and dashboard HTML.

## W2 — report-to-report diff
Extracted the exact comparison logic scheduler.py's automatic current-vs-
prior attachment already ran into `period_diff.diff_report_objects()` — one
implementation, two call sites now (scheduler's automatic B1→B2 attachment,
and this on-demand diff), never two differs. New
`GET /api/reports/diff?report_id_a=...&report_id_b=...` and
`GET /api/clients/{client_name}/reports` (per-client, by period — the other
half of the same exit criterion `GET /api/reports` only did globally
before). 7 tests.

## Explicitly not built in this pass (stated, not silent)
- No frontend UI for managing B4 alert rules or running a W2 diff (backend
  capability is real and tested; UI is a follow-on).
- D2.3 has no HTTP/UI exposure yet — needs a decision on how a caller
  supplies which client's data_context to use at export time.
- D2.4 (publish to workspace) stays GATED — needs real Power BI/Fabric
  OAuth. D2.5 (human-verified open test) needs real Power BI Desktop, not
  available in this environment.
- Gate check: E (product shell — auth/tenancy/billing) and I (GA4/Google
  Ads/Meta OAuth integrations) are the two remaining un-started tracks.
  Both require real infrastructure decisions and credentials only the user
  can provide (which auth/billing provider, real OAuth app registrations)
  — explicitly not started without that.

# T2 — data-availability contract: never a silent blank/0 chart

depends on: T1 (green). GOAL: a template must never silently render an
empty or zero-filled section.

**The gap this closes**: `parsers.py`'s whole design is "never crash on a
slightly different export — fill a sane default and keep going" (e.g. a
missing `revenue_usd` column silently becomes `0.0` for every row). Correct
for robustness, but it meant a client whose GA export has no ecommerce
tracking would get a "Revenue by channel" chart showing real-looking bars at
$0 — indistinguishable from "this channel genuinely made nothing."

- **`cleaning.missing_column()`** (new): a distinct issue `kind` —
  `column_missing` — logged when a column was entirely absent (every row
  defaulted), separate from `missing_value` (a few blank cells in an
  otherwise-real column). Wired into `parsers.py`'s three `normalize_*`
  functions, but scoped to a deliberately small
  `_BUSINESS_CRITICAL_COLUMNS` set (`revenue_usd` for analytics, `amount_usd`
  for sales) — not every defaultable column. Logging all of them was tried
  first and broke a real test
  (`test_year_and_month_columns_are_synthesized_into_a_date`, a real GA
  export shape with 7 routinely-absent columns like `engaged_sessions`) —
  correctly, since routine gaps are harmless and logging them would just be
  noise burying the columns that actually matter.
- **`ChartSpec.requires_columns`** (template_specs.py): each chart in a spec
  can name the source columns its whole point depends on.
  `default.json`/`analytics_only.json` mark the six revenue-driven charts
  (`Weekly revenue`, `Revenue by channel`, and the four sales revenue
  charts) with `requires_columns: ["revenue_usd"]` / `["amount_usd"]`.
- **`template_specs.select_renderable_charts()`** (new): splits a section's
  declared charts into (renderable, omitted) by checking `requires_columns`
  against that section's `column_missing` issues — called from
  `_finish_report` before `render_section_charts`, so an omitted chart is
  never even built, not built-then-hidden.
- **Visible in the output AND the QA JSON**, per the exit criteria, not just
  one: `cleaning.chart_omitted()` folds the omission into the same
  `cleaning_issues` list that becomes `report["data_quality"]` —
  `report.html` already renders `data_quality.details[].message` verbatim,
  so no template change was needed for it to show up in the PDF. Separately,
  `_finish_report` adds an additive `qa["data_availability"]["omitted_charts"]`
  key (only when non-empty) to the QA JSON — never flips `qa["badge"]`,
  since omitting a chart is a designed degrade, not a QA failure.
- **Stated, not silent, boundary**: scoped to charts/KPIs only, per the
  mission's own framing ("a template requesting an unavailable KPI degrades
  explicitly"). A defaulted number can still appear inside fallback/LLM
  narrative *prose* (e.g. "$0 in revenue" as a sentence) — narrating around
  a chart-level omission is a genuinely separate problem (closer to A3,
  narrative↔chart binding) and explicitly NOT solved here.
- 8 new tests. Caught mid-implementation: this environment has a reachable
  local Ollama server, so an unguarded `build_report()` call in a test hits
  the real (slow, non-deterministic) local model — exactly what
  `test_report_object.py`'s "don't route fixtures through build_report()"
  convention exists to avoid. Two new tests hit this directly (a live-model
  sentence cited an unrelated number that failed traceability, flipping the
  badge to FAIL for reasons that had nothing to do with T2); fixed by
  forcing the deterministic fallback (`monkeypatch` on
  `agent._ollama_available`), matching the existing convention rather than
  inventing a new one.
- Full suite re-run clean on every directly-affected module (parsers,
  template_specs, report_object, chart_intelligence/annotation, pbip_export,
  exports, run_qa_cli, html_dashboard, qa) — 66+18 passing, zero new
  failures; the pbip golden fixture (real revenue/amount data) is untouched
  since nothing in it is actually missing.

# T1 — templates are declarative specs, not Python branches

First node of the new GRAPH OF LOOPS mission (Track T — Report Templates,
gated behind F0, which was already green: `ReportObject` was already the one
structure every renderer — PDF, dashboard, PPTX, email, PBIP — reads from).
STOP-gated as an interface change; plan presented and confirmed before
building.

**What was hardcoded before this**: `report_builder.py` had exactly one
template, expressed as Python control flow — a fixed `SECTION_ORDER`
3-tuple, three separate `_analytics_section()`/`_seo_section()`/
`_sales_section()` functions each hardcoding its own chart list inline, and
a module-level `_CHART_SPECS` dict. Adding a second template meant
copy-pasting those branches. `agent.py` had exactly one `SYSTEM_PROMPT` and
no concept of tone anywhere in the codebase.

- **`app/template_specs.py`** (new): the one place that maps stable string
  ids a JSON spec file uses to the real callables that compute metrics /
  render chart images. A template is now a JSON file under
  `app/template_specs/*.json` — section list, order, per-section chart list
  (caption, chart builder id, chart type, metric paths, shape, x/y fields),
  and a `tone` field. `render_section_charts()` replaces the three old
  hand-written per-section chart lists with one generic loop driven by the
  spec.
- **`app/template_specs/default.json`**: encodes the exact pre-T1 section
  order/labels/captions — this IS the regression guarantee. `_CHART_SPECS`/
  `_CHART_METRIC_PATHS` in `report_builder.py` still exist (several existing
  tests import them directly) but are now *derived* from this file at import
  time, not hand-written literals.
- **`app/template_specs/analytics_only.json`**: a second, genuinely smaller
  real template (one section, 2 of the 5 analytics charts, `tone:
  "executive"`) added purely as a JSON file — `report_builder.py`'s source
  text names it nowhere (asserted directly by a test that greps the source),
  proving "adding a template requires zero renderer changes" against the
  real pipeline, not a mock.
- **`report_builder.py` refactor**: `build_report()` /
  `build_report_from_data_context()` gained an optional `template_id: str =
  "default"` parameter (fully backward compatible — no existing caller
  passes it). `_finish_report()` loads the spec, filters `metrics_payload`
  down to only the sections the template actually selects (a section
  present in uploaded data but not in the spec is never rendered — not by
  the narrative, not by charts, not by the canonical object's `metrics`/
  `series`), and drives `sections_requested`/chart generation/tone entirely
  from the spec.
- **Tone** (`agent.py`): `generate_report()`/`_build_user_prompt()`/
  `_fallback_report()` all gained a `tone: str = "manager"` parameter.
  "manager" is an empty prompt override by design — `SYSTEM_PROMPT` was
  already written in that register, so default-template live-LLM output is
  byte-identical to pre-T1. `executive`/`specialist` add a short register
  clause to the system prompt for live models; the deterministic fallback
  (the only path this environment can actually exercise without an API key)
  varies only how many highlight bullets lead the executive summary
  (`_TONE_HIGHLIGHT_COUNT`) — every number and every section's narrative
  text is provably unaffected by tone (`test_fallback_tone_changes_only_how_many_highlights_lead_not_their_content`).
- **Global invariant proven, not assumed**: built `default` (tone=manager,
  3 sections) and `analytics_only` (tone=executive, 1 section) from the
  *same* real `aurora-home-goods` data and asserted
  `default_obj.metrics["analytics"] == analytics_only_obj.metrics["analytics"]`
  byte-for-byte — different tone, different section selection, zero figures
  moved.
- **QA badge**: unchanged code path (`qa.run_qa` still runs once per report
  from the shared `_finish_report` tail) — proven to still fire correctly
  for a non-default template, not just asserted to be untouched.
- 12 new tests in `tests/test_template_specs.py`. Full existing suite run
  clean against the refactor (400+ passing across two runs); the only
  failures observed are 18 pre-existing failures confined to
  `tests/test_viz_*.py` (the schema-agnostic chart-suggestion engine, last
  touched Jul 26–27, imports none of the modules this node touched) and one
  confirmed-flaky network-dependent schema-fetch test in
  `test_pbip_export.py` that passes in isolation — neither caused by this
  change, neither fixed by it (out of scope for T1).

**Not done in this node** (explicitly deferred to the nodes that own them):
no second template exposed to users/UI (T4), no `template_id`/version
recorded on `ReportObject` (T3), no data-availability validation before
generation (T2).

# Client branding, a logo, and real content in the Power BI export

Follow-on to D2.1, requested directly: real client branding (not Power BI's
default theme), a company logo, and more graphs/content — plus one real
interactive filter.

**Two shapes verified against real references before writing a line of
JSON**, same discipline as D2.1's textbox: a custom theme and a logo both
route through `report.json`'s `resourcePackages` mechanism, confirmed
against the *official* `ResourcePackage`/`ResourcePackageItem` definitions
already cached in this repo's own `report_3.3.0` schema (not a guess) — logo
uses item `type: "Image"`, theme uses `type: "CustomTheme"`, both registered
under a `RegisteredResources` package the way a real report.json (from
data-goblin/power-bi-agentic-development's K201 example) actually does it.

- **Custom theme** (`_build_theme_json`): reuses `theme.py`'s exact
  `CATEGORICAL` palette and `STATUS` colors — the same palette already in
  the PDF and HTML dashboard — plus this report's own `primary_color`/
  `accent_color`. Checked against Microsoft's own published
  `reportThemeSchema` (fetched from `microsoft/powerbi-desktop-samples`,
  Draft-7 JSON Schema, only `"name"` is actually required) via the same
  `pbip_validate.validate_schemas()` gate every other file goes through —
  the theme file carries its own `$schema` pointer, so no new validator
  code was needed, it just started getting checked.
- **Logo**: `branding.logo_data_uri` (a data: URI) decodes to real bytes,
  written to `StaticResources/RegisteredResources/logo.png`, and shown as
  an `image` visual in the header of every page. Malformed/missing logos
  are dropped, never fatal to the rest of the export. **Wired all the way
  to the form** — `logo_data_uri` didn't exist as a real field anywhere
  before this (only a docstring mention): added to `/api/generate-report`'s
  Form fields and to `page.tsx`'s branding section (file input, live
  preview, converted to a data URI client-side via `FileReader`). Verified
  with a real HTTP round trip: uploaded a real PNG through the actual form
  fields, generated a real report, exported it, confirmed `logo.png` really
  is in the downloaded zip.
- **Percent formatting, checked for a real bug before shipping it**:
  `metrics.py`'s `*_pct` fields are already ×100 (e.g. `win_rate_pct =
  66.9`, not `0.669`) — a real DAX `"0.0%"` format token *also* multiplies
  by 100, which would have shown `6690.0%`. Used a quoted-literal format
  string (`0.0"%"`) instead — plain decimal formatting with a literal `%`
  character appended, not the percent operator. Caught by reasoning through
  it before generating, then confirmed directly against real generated TMDL
  (`win_rate_pct` renders `66.9`, not `6690.0`).
- **More content**: two SEO tables (`SeoWorstPages`, `SeoOpportunityPages`)
  that the PDF path never charts at all — genuinely new material, not a
  duplicate. Seven KPI cards (reusing `html_dashboard.py`'s exact labels —
  "Web Sessions", "Closed-Won Revenue", etc. — so a number is never called
  two different things on two different surfaces) placed above the charts
  on each page, bound to D2.0's already-pre-aggregated `*Totals` tables
  (a raw column, not a DAX measure — correct without one, since each Totals
  table already has exactly one row; D2.2 is where measures matter for
  numbers that aggregate across multiple rows).
- **One real interactive filter**: a channel slicer on the Web Analytics
  page, bound to `AnalyticsByChannel`. Concrete and verifiable rather than
  a vague "more interactive" — asserted directly against the generated
  slicer's field binding.
- **Real bug caught by the existing test suite immediately**: two tests
  assumed "zero charts → zero page content." With KPI cards now populating
  a page independently of `report_object.charts`, that assumption broke —
  correctly, since a report with real totals but no charts should still
  show them. Not silently patched: the tests were rewritten to assert the
  new behavior explicitly, plus a new test confirms the *actually*-empty
  case (no charts, no metrics, no series at all) still skips Report/.pbip
  entirely.
- New layout-correctness test: every visual's `(x, y, width, height)` box on
  a page is checked pairwise for overlap — cheap, and it's the one thing
  that silently breaks when several independent content types (header,
  cards, charts, tables, slicer) share the same page.

Golden reference (`tests/fixtures/pbip_reference/`) regenerated — now 15
tables (was 13), the full branded/enriched Report tree, 66 files total.
`tests/test_pbip_export.py` grew to 28 tests, `tests/test_exports.py` +1.
Full suite for these two files plus `test_report_object.py`: 66 passed.

# D2.1 — chart → visual mapping, and it's a real button now

Every ChartRef in a real report now becomes a real, bound Power BI visual —
and the whole thing is wired all the way to the UI, not just callable from a
Python shell.

`app/pbip_export.py` extended:
- Single mapping table (`_CHART_TYPE_TO_VISUAL`: line/bar/pie → their Power
  BI visualType), never re-decided elsewhere — `chart_type` always comes
  from the ChartRef itself. A separate `_VISUAL_SPECS` table supplies what
  the type map can't (which D2.0 table/x-field/y-fields/series-field each
  caption binds to) — this is binding metadata, not a second chart-type map.
- **Multi-series binding verified concretely**: "Monthly revenue & win
  rate" binds both `revenue_usd` and `win_rate` into the same visual's Y
  projections — asserted directly against the real generated JSON, not
  assumed from the spec.
- **Annotations were the real risk in this node.** Guessing the PBIR
  textbox JSON shape risks a file Desktop refuses to open — the same
  failure mode the old hand-built demo's own README already flags for
  anything unverified. Found a real, documented example instead (data-
  goblin/power-bi-agentic-development's pbir-format skill reference, fetched
  and verified against Microsoft's own visualContainer schema, not
  memorized) rather than inventing the shape. Every annotation in the real
  aurora-home-goods report converted to a static textbox carrying the exact
  same text A2 already computed — verified byte-for-byte against
  `chart.annotation["text"]`, not just "a textbox got created."
- **Real bug caught by testing the zero-chart case, not assumed safe**: an
  earlier version wrote `.platform`/`report.json`/`pages.json` for the
  `*.Report/` folder unconditionally, only gating the `.pbip` root file
  behind "are there any pages" — leaving a broken, page-less Report folder
  on disk for a report with zero charts. Fixed by gating the entire
  Report-side write behind `page_ids`, computed first.
- Both reused validators (`pbip_validate.py`) now have real work to do:
  28 real `*.json` files (report/page/visual definitions) pass Microsoft's
  schemas clean, and every visual's field reference resolves to a real
  column — checked directly, not assumed from the spec being "obviously
  right."
- Golden reference (`tests/fixtures/pbip_reference/`) regenerated to the
  full package (SemanticModel + Report + `.pbip` root file); regeneration
  today diffs byte-clean against it.

**Then wired all the way through, since a generator nobody can reach isn't
an export feature**: `app/exports.py` gained `export_pbip()` — zips
`build_pbip()`'s directory tree into one downloadable archive (a PBIP is
dozens of small files, not one blob, so unlike pptx/email_html this export's
`content` is a `.zip`), registered into the same `export_report(obj,
formats)` dispatch every other format already goes through. `main.py`'s
existing `/api/report/{id}/export/{fmt}` endpoint needed one line (the
`.zip` extension mapping) — no new endpoint, reusing the door D1 already
built. Verified with a real HTTP round trip through `TestClient`, not just
`export_pbip()` called directly: generate a real report, hit
`GET /api/report/{id}/export/pbip`, unzip the actual response body, confirm
it's the same valid, schema-passing project. `report-writer/frontend/src/app/
page.tsx` gained an "Export to Power BI" button next to the existing
PDF download link, same style, pointed straight at that endpoint.

New/updated tests: `tests/test_pbip_export.py` (+8, now 18 total, including
the real end-to-end HTTP round trip), `tests/test_exports.py` (+3: a real
zip with a real project inside, the no-charts-means-SemanticModel-only case,
and `export_report`'s dispatch now covering all four formats).

**Sub-graph state: D2.0 and D2.1 both green, and reachable from the UI.**
D2.2 (measure correctness — the QA extension: DAX measures reconciled
against ReportObject, a FAILing badge blocking export) is next. D2.3 (live
warehouse), D2.4 (publish to workspace, gated), D2.5 (human-verified open
test) remain red/not started.

# D2.0 — Power BI export, parameterized (semantic-model layer only)

Track D sub-graph: Power BI as an export target, built as a graph of loops
(D2.0 → D2.1 → D2.2 → D2.3/D2.4 gated, D2.5 human-verified). This is D2.0,
the interface-change node — plan presented and confirmed before writing any
code, per the mission's own STOP gate.

**The one fact that shaped the whole design**: `ReportObject` (F0) never
carries raw rows, only `metrics` (curated aggregates) and `series`
(weekly/monthly rollups) — and raw uploaded/fetched files aren't persisted
anywhere in this pipeline either. So the hand-built reference generator at
`d:\IMDollars\powerbi\build_pbip.py` (a row-level star schema, read from raw
CSV/XLSX via Power Query) could not be parameterized as-is — the new
generator (`app/pbip_export.py`) builds a small set of *already-aggregated*
tables instead (one row per channel, one row per month, ...), extracted via
`ReportObject.resolve()` — the exact dotted-path mechanism report_builder.py's
chart specs already use. This is a *stronger* fit for the GLOBAL INVARIANT
than a row-level model would be: Power BI never sees raw rows, so it cannot
compute an answer that differs from ReportPilot's own — D2.2's "does the DAX
measure reconcile with ReportObject" is a guarantee by construction here, not
one that needs defending against DAX/pandas semantic drift.

`build_pbip(report_object, out_dir)`:
- 13 tables derived from a declarative spec, reusing **A1's own shape
  vocabulary verbatim** (`records`/`dict_counts`/`pairs` — the same three
  names `chart_intelligence.py` already defines) plus one new `scalar_dict`
  kind for KPI/summary tables charts don't need but a semantic model does.
- Data is embedded directly into each table's TMDL partition as a Power
  Query `#table(...)` M literal — not read from an external CSV via a
  machine-path parameter. The old demo bakes an absolute local path into
  `expressions.tmdl` (`SampleDataFolder`) and calls `uuid.uuid4()` for every
  `.platform`/relationship ID; both would fail "byte-identical across two
  runs." Fixed here: no external file dependency, and every ID is
  `uuid5(fixed_namespace, stable_name)` — deterministic by construction.
- **Real bug caught by testing against real output, not assumed**: the
  first version named every section's summary table "Totals" — three
  sections, one filename, silently overwriting each other's `.tmdl` file on
  disk and producing duplicate `ref table Totals` lines in `model.tmdl`.
  Caught by generating against a real 3-section report before writing any
  tests. Fixed with section-qualified names (`AnalyticsTotals`/`SeoTotals`/
  `SalesTotals`).
- Reused `d:\IMDollars\powerbi\validate_pbip.py` and
  `check_field_references.py` as test gates, exactly as instructed — but
  parameterized into `app/pbip_validate.py` to accept any project directory
  instead of a hardcoded `AuroraHomeGoods.*` glob. Honest scope note: `.platform`/
  `definition.pbism` carry a `$schema` field but (per the PBIP format's own
  convention, unchanged here) aren't named `*.json`, so schema validation
  finds nothing to check until D2.1 adds a `*.Report/` folder — reported as
  `checked == 0, ok == True`, not a fabricated pass count.
- **"Reproduces the committed reference PBIP" reinterpreted, per the
  confirmed plan**: byte-diffing against the *old* hand-built
  `AuroraHomeGoods.*` isn't reachable — that's a row-level model, this is an
  aggregates model, by design. Generated once against a REAL `ReportObject`
  (via the already-onboarded `aurora-home-goods` SQLite data context,
  `build_report_from_data_context`, not a hand-built stub), committed as the
  new golden reference at `tests/fixtures/pbip_reference/AuroraHomeGoods.SemanticModel/`.
  Regenerating today diffs byte-clean against it — verified by a dedicated
  regression test, not assumed.
- Measures are explicitly **not** part of this node — D2.1 ("chart → visual
  mapping") is where a KPI card first needs one bound to it; adding measures
  speculatively here, before D2.1 defines what needs binding to what, risked
  churn. Columns still carry a sensible default `summarizeBy` (naming-
  convention heuristic — `_pct`/"rate"/`avg_` → average, other numerics →
  sum, text/boolean → none — reusing the exact convention already
  established and tested in `html_dashboard.py`'s `formatCell()`), so
  dragging a raw column into a new visual still aggregates sensibly without
  a named measure.

New tests: `tests/test_pbip_export.py` (9) — real generation against
`aurora-home-goods`, determinism (byte-identical across two runs), zero
client-specific-string leakage into a different client's output, missing-
section and empty-list tables skipped with a stated reason (never a crash
or a silent omission), both reused validators passing clean, and the
golden-reference regression. Full suite after: 379 passed, the same 18
pre-existing unrelated `viz/`-subsystem failures, zero regressions.

**Sub-graph state: D2.0 green.** D2.1 (chart → visual mapping) is next —
confirmed simplification found while reading `charts.py` for this node: every
`bar`-type chart in this app renders horizontal (`barh`), so D2.1's chart-type
map is unambiguous (`bar` → `clusteredBarChart`, no column/bar orientation
decision needed). D2.2 (measure correctness), D2.5 (human-verified open test)
remain red. D2.3 (live warehouse) and D2.4 (publish to workspace, gated) not
started — D2.4 stays gated until Power BI/Fabric OAuth is authorized, per the
mission's own instruction.

# Four ingestion modes: onboarding a client no longer means "SQL or nothing"

A client either lets us hold their inbox/warehouse credentials, or doesn't —
that split now has four concrete, selectable modes instead of one:

1. **Client push (new)** — `client_agent/`: a standalone Windows CLI, built
   into `reportpilot-agent.exe` via PyInstaller, that a client's own IT runs
   locally on Task Scheduler. `setup` saves Gmail/Outlook/Slack credentials
   encrypted with Windows DPAPI (`CryptProtectData`) — tied to that Windows
   user + machine, no key file to leak. `run` fetches new attachments and
   POSTs them to the hosted `/api/generate-report`, following real progress
   via the SSE job-events stream, exit code 0/1 for Task Scheduler's history.
   Deliberately has zero import from `app/` (avoids dragging pandas/fastapi
   into a tiny exe) — the small MIME-parsing/slot-guessing functions are an
   intentional hand-kept duplicate of `email_source.py`'s. Verified for
   real, not just mocked: the built `.exe` was actually run end-to-end
   (`setup` then `run --dry-run`), and its IMAP path genuinely reached
   Gmail's real server and got a real `AUTHENTICATIONFAILED` on a fake
   password — proof the DNS/TLS/protocol path isn't just unit-mocked.
2. **Hosted inbox polling (new)** — `app/slack_source.py` (new, Slack Web
   API file fetch, same connector shape as `email_source.py`'s IMAP one on
   purpose) + `report_builder.build_report_from_data_context` now branches
   on `connector.kind`: `imap_inbox`/`slack_inbox` fetch fresh attachments
   and hand them straight to `build_report()`, instead of querying a
   warehouse. Two new `main.py` endpoints, `onboard-inbox`/`onboard-slack`,
   test the connection before saving (same posture as the existing SQL
   onboarding endpoint). The payoff: **zero changes needed in
   `scheduler.py`** — a schedule's `data_source_ref` already just points at
   a `data_context` entry, so the existing cadence/idempotency/background-
   loop machinery now polls a mailbox or Slack channel for free. Verified
   end-to-end through the real API (onboard → create schedule → fire it →
   fetch the generated report) with a mocked connector carrying real
   `sample_data/*.csv,*.xlsx` bytes.
3. **Warehouse connector (existing, drivers now actually installed)** —
   `requirements-connectors.txt` drivers installed and all four connector
   modules (`postgres`/`snowflake`/`bigquery`/`databricks`) confirmed to
   import cleanly. **Caught while doing this**: `snowflake-connector-python`
   transitively downgrades `pandas` from 3.0.3 to 2.3.3 — a real interaction
   worth knowing about before installing this file in a shared environment,
   not just an abstract "these are heavy" warning. Full regression run
   afterward: same 18 pre-existing unrelated `viz/`-subsystem failures,
   zero new ones — the downgrade doesn't break anything in this codebase,
   but it's not nothing either. Still no live warehouse available to verify
   actual connectivity — that limitation is unchanged and stated plainly.
4. **Manual upload (existing, unchanged)** — the baseline
   `/api/generate-report` file-upload flow. No new work; now documented as
   one of four equally-legitimate modes rather than "the only way in."

New tests: `test_slack_source.py` (11), `test_report_builder_inbox_context.py`
(4), `test_onboard_inbox_slack.py` (5, includes the full Mode 2 API loop),
`test_client_agent.py` (16, DPAPI round-trip is real — this environment is a
real Windows machine — everything else mocked at the network boundary).
Full suite after all of it: 370 passed, the same 18 pre-existing failures,
zero regressions.

# Closing the Known Limitations — nine fixes against the honest gap list

Follow-on to the architecture-doc review: each of the nine documented
limitations addressed directly, one commit-worthy change per item, verified
against real generated reports where the fix touches the live pipeline —
not just unit-tested in isolation.

## The QA badge is now visible in the PDF and the actual upload UI

`app/templates/report.html`, `app/theme.py` (new `BADGE_PASS/WARNING/FAIL_*`
tokens), `app/templates/dashboard.html`, `app/main.py` (`GET /api/report/{id}`
now returns `qa`), `report-writer/frontend/src/app/page.tsx` (the actual live
`ReportPreview` — see below), `tests/test_report_object.py` (+1).

- `report.html`'s cover page now shows the PASS/PASS-WITH-WARNINGS/FAIL badge,
  matching what the dashboard already had.
- **Real bug caught rendering the first attempt**: the badge background used
  an 8-digit `#RRGGBBAA` hex-alpha color on top of a `STATUS` token — works
  in a browser, but xhtml2pdf (the PDF renderer) doesn't support CSS4 8-digit
  hex and renders it fully opaque. Since the text color was the *same* base
  color, this produced same-color text on same-color background —
  effectively invisible. Confirmed visually via a pymupdf-rendered page
  crop, not assumed. Fixed by adding real solid pre-mixed badge tokens to
  `theme.py` (`BADGE_PASS_BG`/`BORDER`/`TEXT`, etc.) and using those in both
  `report.html` and `dashboard.html` instead of the alpha trick — one
  regression test added asserting text/background are never equal, for
  every tier.
- **Found while wiring this up**: `report-writer/frontend/src/components/
  Reportpreview.tsx` — the file that looked like "the" report preview
  component — is never imported anywhere in the frontend. The actual live
  UI is an independent, inline `ReportPreview` function and `GenerateResponse`
  type defined directly in `page.tsx`. Fixed the real, live one; gave the
  orphaned file the same fix for consistency in case it's wired in later,
  but flagging it here since it's a genuine piece of dead code worth a
  decision (delete it, or actually use it).

## QA now validates insights.py's deterministic figures too

`app/qa.py` (`check_insights_sanity`, `InsightsSanityResult`, wired into
`run_qa`/`QAReport`), `tests/test_qa_insights_sanity.py` (new, 10 tests).

- **Design decision that mattered**: a naive reuse of the narrative-
  traceability regex-scan (the same mechanism that checks agent.py's prose)
  would have false-positived on every legitimately-*derived* figure — a
  health score or a dollar-opportunity estimate is a new number insights.py
  computed, not a copy of an existing metric, so "does this literal exist in
  metrics_payload" is the wrong question and would cry wolf constantly. The
  correct test is a recompute-and-diff (the same pattern `aggregation_sanity`
  already uses for `metrics.py`), but as **exact string comparison** against
  a fresh `compute_insights()` call, not numeric diffing — insight cards are
  pre-rendered text (`headline`/`sub`/`detail`), and `compute_insights` is
  pure, so two calls against the same metrics produce byte-identical text
  unless something real changed.
- **Regression caught before it shipped**: making this check unconditional
  broke 4 pre-existing `test_qa.py` tests immediately, because their
  hand-built fixtures never set an `"insights"` key at all. Fixed by
  distinguishing `None` (key absent — caller isn't using this facility,
  skip entirely, same accommodation `source_frames`/`charts` already get)
  from a real empty list (`insights.py` legitimately found nothing to say —
  still validated for real).
- Verified against real sample data: an untouched real report's insights
  pass cleanly; a hand-injected mismatch (simulating the project's own
  historical `_avg_deal` mutation bug) is caught; a missing or unexpected
  card is caught. End-to-end check against a real `build_report()` call
  confirmed `insights_sanity.ok == True` on real data — no false positives
  in the live pipeline.

## Sales revenue definition mismatch — fixed, not just documented

`app/metrics.py` (`sales_metrics`'s synthesized-monthly fallback),
`tests/test_metrics.py` (new — this module had no dedicated tests before).

- The bug: when a source file has no monthly-summary sheet, the fallback
  summed `amount_usd` across *every* deal for that month regardless of
  stage (open, lost, won), while the headline `Sales Revenue` figure summed
  only `won` deals. Fixed to filter the fallback's revenue the same way the
  headline does — deal-stage counts (`deals_won`/`deals_lost`) still count
  every stage, only the revenue sum changed. Verified against real sample
  data with the fallback forced on: headline and synthesized-monthly sum
  now match exactly (`202706.76 == 202706.76`; they didn't before).

## Autonomous scheduling — a real background loop, not just a due-date check

`app/scheduler.py` (`start_background_loop`), `app/main.py` (lifespan-based
startup, `AUTO_SCHEDULER_ENABLED`/`_INTERVAL_SECONDS`/`_DELIVER` env vars),
`tests/test_scheduler.py` (+3).

- `scheduler.py` could already answer "is this due" — nothing called that on
  a timer. `start_background_loop(interval_seconds, deliver)` starts a daemon
  thread that runs one cycle immediately (so a due schedule doesn't wait out
  a full interval after a restart), then on the configured interval,
  forever, until `stop_event.set()`. One bad cycle (a transient warehouse/LLM
  error) is caught and logged, never kills the loop — verified directly by
  injecting a failure on the first call and confirming the thread survives
  and retries.
- Opt-in via `AUTO_SCHEDULER_ENABLED` (default off): starting the app to
  poke at the API must never silently start generating reports for every
  saved schedule.
- Verified end-to-end, not just with a mocked loop: started the real loop
  against a real "daily" (always-due) schedule and confirmed a real report
  was generated and persisted within the polling window.

## Credentials at rest — encrypted when configured, loud when not

`app/data_context.py` (Fernet encryption via `DATA_CONTEXT_ENCRYPTION_KEY`),
`requirements.txt` (+`cryptography`), `tests/test_data_context_encryption.py`
(new, 10 tests).

- `connector.config` (DSNs, passwords, tokens) is now encrypted before
  touching disk when `DATA_CONTEXT_ENCRYPTION_KEY` is set. Unset: still
  saves (a local single-user demo shouldn't hard-fail on a missing key), but
  logs a loud warning on every plaintext save — running insecurely is never
  silent. A malformed key, or the wrong key at decrypt time, raises with a
  clear message rather than failing quietly.
- Backward compatible: a data context saved before this feature existed
  (`connector.config` as a bare dict, no wrapper) loads exactly as before,
  key configured or not — verified directly.
- Scoped honestly: this protects the JSON file on disk from casual
  reading/backup leakage. It is not tenant isolation and doesn't restrict
  who can call the API for their own saved config — that's what the API-key
  gate below is for, and multi-tenant isolation remains explicitly out of
  scope (Track E, gated).

## Optional shared-secret API key gate

`app/main.py` (`require_api_key` middleware), `tests/test_api_key_auth.py`
(new, 7 tests).

- `API_KEY` unset (default): no auth at all, preserving today's zero-config
  local behavior. Set: every request needs a matching `X-API-Key` header,
  read fresh from the environment per-request (not cached at import) so it
  can be toggled without a restart. `/api/health` stays exempt (health
  checks/load balancers shouldn't need a credential); CORS preflight
  (`OPTIONS`) is never blocked — verified directly, since a blocked
  preflight would silently break every real authenticated request from a
  browser.
- Explicitly **not** multi-tenant auth — one shared secret, no per-user
  identity, no tenant boundary. Closes "anyone who can reach this port can
  read any `report_id`," nothing more; stated plainly so it isn't mistaken
  for more than it is.

## Connector capability matrix — drivers now actually installable

`requirements-connectors.txt` (new, optional).

- Postgres/Snowflake/BigQuery/Databricks adapters existed but their driver
  packages weren't anywhere — not `requirements.txt`, not documented outside
  each connector's own inline `ImportError` message. Added as an **optional**
  file (not merged into the base install — these are heavy vendor SDKs, and
  the only connector actually verified against a live database in this
  environment is SQLite, so forcing every install to carry all four would be
  pure bloat for the common case). Package names and versions pulled from
  each connector's own existing `pip install` error message, not guessed.
  Still unverified against live services — that remains an honest limitation
  no amount of code review here can close without real credentials.

## `period_diff.py` is now part of the live report route

`app/report_object.py` (`ReportObject.period_comparison`), `app/scheduler.py`
(`_prior_report_id`, `_attach_period_comparison`, wired into `run_schedule`),
`app/html_dashboard.py` + `app/templates/dashboard.html` (surfaced, not just
computed), `tests/test_scheduler.py` (+8), `tests/test_html_dashboard.py` (+2).

- B1's diff functions were tested in isolation but never called by anything
  that generates a real report. Wired into `scheduler.py` specifically (not
  `report_builder.py`): a schedule is the one place with a genuine "prior
  report" to diff against, via `schedule.runs`. After a fresh generation,
  `_attach_period_comparison` finds the most recent earlier report for that
  same schedule, loads its persisted object, diffs `analytics.totals` and
  `sales.totals` against the new report's own, and re-persists with the
  result attached — `None` for a schedule's first-ever run or a one-off
  upload, not an error state.
- **Distinct on purpose from metrics.py's existing "change %" fields**:
  those compare the first half of one upload against its second half; this
  compares one full report against a genuinely separate prior report. Both
  now documented clearly enough not to be confused for each other.
- Surfaced, not just stored: the dashboard shows a "vs. prior report"
  table (metric / this period / prior / % change, color-coded by
  direction) when a comparison is present. Verified end-to-end against two
  real sequential scheduled generations: the second report's
  `period_comparison.prior_report_id` correctly points at the first.

## Regression discipline

Full suite run after every item, not just at the end. Final state: the same
18 pre-existing, unrelated `app/viz/`-subsystem failures at every checkpoint
(unchanged count throughout — confirmed, not assumed), zero regressions
introduced across all nine fixes. New/updated test files:
`test_metrics.py` (new), `test_qa_insights_sanity.py` (new),
`test_data_context_encryption.py` (new), `test_api_key_auth.py` (new), plus
substantial additions to `test_report_object.py`, `test_html_dashboard.py`,
and `test_scheduler.py`.

# Inbound email as a data source (follow-on to B3, requested separately)

`app/email_source.py` (new), `app/main.py` (`POST /api/generate-report/from-inbox`),
`tests/test_email_source.py` (new, 20 tests).

Complements B3 (which only *sends* the finished report out) with the
inverse direction: fetch CSV/Excel attachments from a mailbox and generate
a report *from* them — same `report_builder.build_report(uploads,
branding)` pipeline an HTTP file upload already uses, unchanged.

- **Auth scoping, decided explicitly rather than half-building three
  paths**: IMAP + an app password is the one path implemented — works for
  Gmail (`imap.gmail.com`) and for a personal/Outlook.com account with
  basic auth still enabled (`outlook.office365.com`). Most Microsoft 365
  business tenants have IMAP basic auth disabled by default and need Graph
  API + an Azure AD app registration instead; that path is a stub
  (`create_graph_api_connector`) that raises with a clear explanation
  rather than pretending to work — same honesty as B3's channels and D1's
  Google Slides export. `IMAPInboxConnector.__init__` raising cleanly
  against a genuinely-unreachable host (RFC 2606's `.invalid` TLD — fails
  fast via DNS, no live server or network flakiness needed to test it) is
  verified directly.
- **Fetch → slot → generate, with nothing silently dropped.**
  `attachments_from_message` (pure MIME parsing, tested against hand-built
  `email.message.Message` objects, no live mailbox needed) extracts
  matching attachments; `guess_upload_slot` maps a filename to
  analytics/seo/sales by deterministic substring hints (same philosophy as
  `data_context.py`'s fuzzy column matching); `build_uploads_from_inbox`
  assembles the exact `{"analytics": (filename, filelike), ...}` shape
  `build_report` already expects. An attachment with an unrecognized
  filename, or a second attachment competing for an already-filled slot,
  goes to a returned `unmatched` list — never silently overwritten or
  dropped, verified directly.
- **Verified end-to-end with real sample data, not just synthetic
  fixtures**: wrapped the actual `sample_data/*.csv`/`.xlsx` file bytes as
  fetched attachments through a fake connector, ran the result through
  `report_builder.build_report()` unmodified, and confirmed a real 3-section
  report came out the other end — proving the new inbound path and the
  existing, unmodified pipeline actually compose, not just that each half
  works in isolation.
- **New endpoint** `POST /api/generate-report/from-inbox` — same
  background-job pattern (`job_id` + SSE progress) as the existing
  upload-based `/api/generate-report`. Returns 503 with a stated reason
  when `IMAP_USERNAME`/`IMAP_PASSWORD` aren't set, verified directly via
  `TestClient` — never a silent no-op.

**Regression check**: 266 passed (246 + 20 new), same 18 pre-existing
`app/viz/` failures, zero new failures.

# Product Graph — self-running, multi-format, defensible product (F0 → A/B/C/D → E)

Dependency graph: F0 (canonical report object) gates tracks A (chart
intelligence), B (diff → schedule → deliver), C (HTML dashboard), D (export
breadth); E (product shell) stays gated until at least one of A-D ships real
value. One node at a time, topological order, F0 marked
`[interface change — STOP + confirm]` in the mission and confirmed with two
adjustments before building (see below) — plan and adjustments not
reproduced here, only what actually shipped.

## F0 — Canonical report object — GREEN

`app/report_object.py` (new), `app/report_builder.py` (wired),
`app/main.py` (report_id threaded, `report_object.json` persisted),
`tests/test_report_object.py` (new, 20 tests).

- **Single schema, produced once per report, persisted.** `ReportObject`
  (`report_id, period, sources, metrics, series, charts, narrative, qa,
  branding, section_order`) is assembled once in `_finish_report` and
  written to a new `generated/<id>/report_object.json`, alongside (not
  replacing) today's `meta.json`/`metrics.json` — those stay byte-shape
  identical, so no existing reader breaks; the intended convergence is that
  they become views derived from the object, not three independently
  maintained copies. **Deliberate namespace split**: `metrics` stays the
  tight, curated aggregate set `qa.run_qa`'s traceability scan matches
  narrative numbers against; the three DataFrame-backed series
  (`_weekly`, `_weekly_totals`, `_monthly`) that charts are drawn from are
  promoted to a JSON-serializable sibling namespace, `series`, instead of
  into `metrics` — putting them in `metrics` would have silently enlarged
  the haystack a fabricated narrative number could coincidentally match,
  coupling "make charts recoverable" to "weaken fabrication detection."
  Caught in plan review before any code was written.
- **The PDF renderer reads only from this object.** New
  `render_pdf_from_object(obj)` takes the object and nothing else;
  internally calls `obj.to_legacy_report_dict()` (charts rejoined to
  sections by `section_order`) so `report.html` needed zero template
  changes. `_finish_report` returns the html/pdf_bytes that call produced —
  not a separately-rendered copy — so this is actually true of the live
  path, not just an artifact assembled alongside it.
- **Round-trips losslessly; smoke_test still yields a comparable PDF.**
  `ReportObject.to_dict()/.from_dict()` round-trip verified
  (`test_round_trip_object_to_json_to_object_is_lossless`). Render-identity
  is asserted on **HTML string**, not PDF-byte hash: running the real
  pipeline twice against identical sample data was measured to produce PDFs
  ~1KB apart from LLM narrative variance alone (`sha256` differed both
  runs), so a byte-hash assertion would be flaky for reasons unrelated to
  what the test is actually checking — HTML is the renderer's real,
  deterministic output surface once the object's content is fixed.
  `smoke_test.py` run manually against sample data post-change: 3 sections,
  8 PDF pages (unchanged from before this node), cover page visually
  verified via pymupdf render. **Bug caught by this exact live-data check,
  invisible to the hand-built test fixtures**: real `sales_pipeline.xlsx`
  monthly sheets and computed weekly aggregates carry pandas `Timestamp`
  columns; `.to_dict("records")` left them non-JSON-serializable and
  `report_object.json` persistence raised on real data despite all 20 unit
  tests passing against string-only fixtures. Fixed by routing series
  promotion through pandas' own `to_json(date_format="iso")` instead of
  hand-rolling type coercion.
- **Every number carries a trace path; charts reference metric paths, not
  raw literals.** `resolve_path`/`ReportObject.resolve` give every chart's
  `metric_paths` a namespace-qualified, resolvable address
  (`metrics.analytics.by_channel`, `series.sales.monthly`, ...) via a
  hand-authored `_CHART_METRIC_PATHS` table in `report_builder.py`, tested
  for completeness two ways: every table entry has a real chart_type +
  non-empty paths (catches a caption drifting out of sync with the table),
  and — the multi-series case specifically — "Monthly revenue & win rate"
  plots two fields (`revenue_usd`, `win_rate`) from one table, so the test
  asserts both fields are actually present in the resolved records, not
  just that the path resolves to *something*.
  **Aggregation-sanity now runs in-band for real**, not deferred:
  `source_frames` (the raw parsed DataFrames, already in scope moments
  before `_finish_report` in both `build_report` and
  `build_report_from_data_context`) are threaded through to
  `qa.run_qa`, so `aggregation_sanity` can genuinely reconcile instead of
  reporting every source `inconclusive` — confirmed against real sample
  data: `{"ok": true, "mismatches": [], "inconclusive_sources": []}`. This
  was a plan revision: deferring source_frames would have permanently
  capped every in-band badge at PASS-WITH-WARNINGS (`inconclusive` maps to
  the warning tier), teaching consultants to ignore the badge within a
  week — the thing F0 exists to prevent.

**Honest observation, not a regression**: the smoke-test run's overall
badge was `FAIL` — not from aggregation-sanity (confirmed clean above) but
from `traceability`/`unsupported_claims` flagging two numbers in this run's
local-LLM narrative ("28" from "trailing 28 days" phrasing — a bare number
in a date-range description, not a claimed metric; "68" for a `title_length`
issue count that's genuinely correct in `top_issues` but stored as a
tuple-list, which the traceability walker doesn't currently match against).
Both are pre-existing limitations in `qa.py`'s number-matcher — untouched by
this node by design — that were simply invisible until QA started running
in-band. Flagged as a follow-up for whichever node next touches `qa.py`'s
matching logic, not fixed here (out of F0's scope: this node is about the
object's structure, not matcher precision).

**Regression check**: full suite run twice (before/after the Timestamp fix)
— 148 passed, 18 pre-existing failures (unchanged count, all in `app/viz/`'s
still-mid-rebuild subsystem from the prior graph-of-loops mission, unrelated
to this node), 7 passed separately in `test_html_dashboard.py` (Playwright).
Zero new failures either run.

**Graph state**: F0 GREEN. A/B/C/D unblocked, buildable in parallel. E
stays gated (needs F0 + ≥1 of A/B/C/D shipped).

## C1 — Interactive HTML dashboard — GREEN

`app/html_dashboard.py` (signature change), `app/templates/dashboard.html`,
`app/main.py` (`/api/report/{id}/dashboard` route), `tests/test_html_dashboard.py`
(9 tests, 2 new).

- **Single self-contained .html, opens offline, no external calls.**
  Unchanged from the earlier build — still verified by
  `test_dashboard_is_self_contained_no_network_calls` (real headless
  Chromium, network blocked, zero failed requests).
- **Consumes the canonical object only — same numbers/charts as the PDF,
  one source.** `build_dashboard(metrics_payload, branding)` →
  `build_dashboard(obj: ReportObject)`, mirroring F0's
  `render_pdf_from_object(obj)` — one parameter, not several separately-
  threaded dicts a future edit could let drift apart. `main.py`'s
  `/dashboard` route now reads `report_object.json` (or the in-memory
  `result["report_object"]`), not `metrics.json`; reports generated before
  this node ship a clear 404 rather than a best-effort reconstruction.
  All 7 existing test call sites updated to build a `ReportObject` fixture
  via a new `_obj()` helper instead of passing a bare dict.
- **KPI cards + ≥1 filter + drill-down.** Unchanged, still green
  (`test_kpi_cards_filter_and_drilldown_interaction`).
- **Values pass the QA trace; renders correctly on empty + large (~50k row)
  data.** Traceability check unchanged and still green. **New**: the
  dashboard now visibly surfaces `obj.qa["badge"]` (color-coded via
  `theme.status`, the same PASS/warning/critical palette used elsewhere) —
  previously the QA badge existed only in the PDF's out-of-band CLI path;
  now every renderer that reads the canonical object shows it, per the
  mission's invariant that every rendered number passes the badge, not just
  the PDF's. Two new tests cover this: badge text present/absent per
  `obj.qa`, using a real headless-browser render. Perf test unchanged and
  still green (<5s build, <500KB HTML for a 50k-row source).

Verified end-to-end against real sample data (not just hand-built
fixtures): `build_dashboard(report_builder.build_report(...)["report_object"])`
produces a 19.7KB dashboard with a real `PASS` badge and 13 KPI cards.

**Graph state**: F0, C1 GREEN. A, B, D unblocked and in progress.

## A1 — Auto chart-type — GREEN

`app/chart_intelligence.py` (new), `app/report_object.py` (`ChartRef`
gains `suitability_verdict/reason/alternatives`), `app/report_builder.py`
(`_CHART_METRIC_PATHS` → richer `_CHART_SPECS` with shape/x_field/y_field),
`tests/test_chart_intelligence.py` (new, 17 tests).

- **Deterministic map (field types, cardinality, temporality) → best chart
  type + rationale.** `infer_field_type` classifies resolved values as
  numeric/temporal/categorical — deliberately only three types, not the
  ad-hoc engine's five, because every field this module ever sees comes
  from `metrics.py`'s fixed, known-shape output, never an arbitrary
  uploaded column; there's no free-text/identifier ambiguity to resolve
  here. `choose_chart_type` reads the real (x, y) series out of whatever
  `ReportObject.resolve()` returns for a chart's `metric_paths` — three
  data shapes handled (`records`, `dict_counts` for `severity_counts`,
  `pairs` for `top_issues`) — and evaluates against **this report's actual
  cardinality**, not a static per-caption assumption.
- **Reuses the ad-hoc viz suitability rules verbatim; discouraged types
  are downgraded, not rendered blindly.** Calls `viz.suitability.
  evaluate_suitability` directly rather than re-deriving "too many pie
  slices" — that logic is already tested. Verified the mechanism actually
  discriminates, not a rubber stamp: a synthetic 15-lead-source pie chart
  comes back `discouraged` with `bar` suggested; the same shape at 4
  categories comes back `good`. Every chart on real sample data currently
  comes back `good` (chosen types were already sound) — printed and
  inspected per-chart, not assumed. **Scope note**: this slice computes and
  attaches the verdict to `ChartRef` (visible, not silently trusted) but
  does not yet auto-swap which chart function renders when a type is
  discouraged — doing that would mean restructuring chart generation to
  happen after suitability evaluation instead of before, which is a bigger
  change than this first slice; left as a natural extension for whichever
  node next revisits chart rendering order.
- **Falls back safely on ambiguous data — no crash, states why.** Missing
  fields, empty resolved data, `None`, and malformed `pairs` shapes all
  degrade to an explicit `ambiguous_data` verdict with a reason, never an
  exception. **Bug caught by the test suite itself**: a malformed `pairs`
  entry (a dict instead of a 2-tuple) raised `KeyError` on `p[0]`, not the
  `IndexError`/`TypeError` the except clause anticipated — a dict has
  `__getitem__` too, it just fails differently. Fixed by widening the
  caught exception set.

**Regression check**: full suite (minus Playwright) — 165 passed (148 + 17
new), the same 18 pre-existing `app/viz/`-subsystem failures, zero new
failures.

**Graph state**: F0, A1, C1 GREEN. A2 unblocked. B, D in progress.

## B1 — Period-over-period diff — GREEN

`app/period_diff.py` (new), `tests/test_period_diff.py` (new, 15 tests).

- **Every KPI gets abs + % delta vs prior period; new/dropped dimensions
  detected.** `diff_totals(current, prior)` diffs flat numeric dicts
  (`metrics.analytics.totals` and friends); `diff_dimension(current_records,
  prior_records, key_field, value_fields)` classifies each key seen in
  either period as `continuing` (deltas computed), `new` (current only,
  carries `current_values`), or `dropped` (prior only, carries
  `prior_values`) — never silently drops a key that disappeared, which
  would misreport a real change as no-change.
- **Diff numbers are deterministic and traceable; the "why" narrative cites
  only diff metrics.** `describe_delta`/`describe_dimension_change` build
  plain factual sentences from a `MetricDelta`'s own fields only — verified
  directly: a test regexes every number out of a generated sentence and
  asserts it's a subset of `{current, prior, abs(pct_delta)}`, i.e. nothing
  in the sentence didn't come from the diff it was handed.
- **Test fixture with two known consecutive periods asserts the deltas
  exactly.** Hand-built current/prior totals + a by-channel breakdown with
  one genuinely new channel and one genuinely dropped one, asserted exactly
  (not just "changed somehow"). **Independently cross-checked against real
  data**: fed `metrics.py`'s own `totals_recent_half`/`totals_prior_half`
  (already computed inside `analytics_metrics()` for its internal
  `sessions_change_pct`/`revenue_change_pct`) through `diff_totals` and
  confirmed the result matches metrics.py's own independently-computed
  percentages exactly (-20.8%, -21.6% on the real sample data) — two
  separate implementations of "percent change," same answer.

Reuses `metrics.py`'s private `_pct_change` (same rounding/undefined-at-
zero convention) rather than restating the formula, so "20.0%" means the
same thing everywhere in the app.

**Regression check**: 180 passed (165 + 15 new), same 18 pre-existing
`app/viz/` failures, zero new failures.

**Graph state**: F0, A1, B1, C1 GREEN. A2, B2 unblocked. D in progress.

## D1 — PPTX / email-HTML / Google Slides export — GREEN

`app/exports.py` (new), `app/main.py` (`GET /api/report/{id}/export/{fmt}`,
`_load_report_object` factored out of the dashboard route), `requirements.txt`
(+python-pptx), `tests/test_exports.py` (new, 15 tests).

- **PPTX export: branded slides (title, KPI, per-section chart+narrative)
  from the object.** `export_pptx(obj)` — title slide (client/agency/period,
  branding primary color as the header band), a KPI slide reusing
  `html_dashboard._kpi_cards()` (not a second definition of "what's a KPI"),
  one slide per narrative section with its heading, narrative text, and
  first chart image embedded as a real picture shape. Verified against real
  sample data: 5 slides, correct branding text, correct period, chart
  pictures present on section slides — inspected via `python-pptx` reading
  the generated file back, not just "it didn't crash."
- **Email-ready responsive HTML export from the same object.**
  `export_email_html(obj)` — single-column, max-width container, inline
  styles (email-client-safe), one `@media (max-width: 480px)` breakpoint
  stacking the KPI grid to full-width. Surfaces the QA badge inline too
  (same as C1's dashboard) when the object carries one.
- **Google Slides export behind the same interface (optional, needs
  connector).** `export_google_slides(obj)` takes the identical `(obj) ->
  ExportResult` shape as the other two but always returns
  `status="unavailable"` with a stated reason — never a fake success. All
  three are callable uniformly through `export_report(obj, formats=...)`.
- **All exports share one branding/token source; numbers identical across
  formats.** Both PPTX and email-HTML pull KPI figures through the same
  `_kpi_cards()` → `theme.format_currency/format_count` call path — checked
  directly: the same `"5,000"`/`"1,000"` strings appear in both the PPTX
  slide text and the email HTML body, not two independently-formatted
  copies that could drift.

Wired end-to-end through `main.py` and hit over real HTTP via
`TestClient`: `/export/pptx` → 200 (177KB), `/export/email_html` → 200,
`/export/google_slides` → 503 with the stated reason (correctly surfaced
as unavailable, not a silent empty success), `/export/bogus` → 400.

**Regression check**: 195 passed (180 + 15 new), same 18 pre-existing
`app/viz/` failures, zero new failures.

**Graph state**: F0, A1, B1, C1, D1 GREEN. A2, B2 unblocked. A3, B3
still blocked on their deps (B3 additionally needs an email/Slack
connector once reached).

## A2 — On-chart annotation — GREEN

`app/chart_annotation.py` (new), `app/report_object.py` (`ChartRef` gains
`annotation`), `app/report_builder.py` (wired into `_build_chart_refs`,
reusing A1's already-resolved series), `app/templates/report.html`,
`app/html_dashboard.py` (+`_chart_highlights`), `app/templates/dashboard.html`,
`tests/test_chart_annotation.py` (new, 17 tests).

- **Deterministically detects the notable point per chart.** Priority
  outlier → largest_delta → peak. Outlier reuses `viz.outliers.
  detect_outliers_iqr` verbatim (same IQR rule as the ad-hoc engine, not a
  second definition). **Two real gaps caught by the test suite itself, not
  assumed correct**: (1) when IQR flags more than one point, the original
  code picked whichever came first by row index — a coincidence of input
  order, not a signal; fixed to pick whichever deviates furthest beyond the
  fence. (2) "largest adjacent step" was being computed for every chart
  regardless of axis type, but adjacency in a categorical breakdown
  (`lead_source`, `sales_rep`, ...) is arbitrary row order, not a sequence —
  a real jump only means something when x is temporal. Fixed by gating
  that branch on `chart_intelligence.infer_field_type(xs) == "temporal"`;
  categorical charts now correctly fall through to peak.
- **Annotation text's number traces to a computed value, never guessed.**
  The annotation's `y_value` is lifted verbatim from the same resolved
  series A1 already validated — tested directly (`y_value` is asserted to
  be a member of the source records' own values, not independently
  recomputed).
- **Annotations render in PDF + dashboard identically; degrade gracefully
  when nothing is notable.** `report.html` gets a `.chart-annotation`
  caption under each chart image; the dashboard (which deliberately doesn't
  embed chart PNGs — an existing Lever-4 design choice) gets an equivalent
  "Notable points" text list from the same `obj.charts` data. A test
  renders both from the same object and asserts the literal same
  annotation string appears in each. A perfectly flat series (every point
  tied) returns `None`, not a forced/misleading label — the "Notable
  points" panel and the per-chart caption both simply don't appear.
- **Bug caught rendering a real PDF page, not by any unit test**: outlier
  x-labels for weekly series came out as `2026-01-19T00:00:00.000` —
  correct but unreadable. Fixed with a narrow formatter that trims a
  midnight ISO timestamp to its date, and *only* that case (a non-midnight
  timestamp is left untouched, verified by a dedicated test) — display
  formatting on an already-correct string, the same category as
  `theme.py`'s `format_currency`/`format_percent`, never a value change.
- **Scope note, stated plainly**: this slice attaches the annotation as
  structured data + a text caption; it does not yet draw the marker
  directly onto the chart's pixels (a literal arrow/dot on the image).
  Doing that would mean computing the notable point *before* the chart PNG
  is rendered, which today happens in the opposite order (chart-render,
  then suitability/annotation) — the same ordering constraint A1 already
  deferred for auto-swapping chart types. Left as a natural follow-on for
  whichever node next restructures chart-generation order.

Verified against real sample data: all 11 charts produced genuine,
distinct annotations (3 outliers, 1 largest_delta on the one genuinely
temporal multi-point chart, 7 peaks) — visually confirmed via a
pymupdf-rendered page that the caption renders cleanly under the chart,
prompting the timestamp-formatting fix above.

**Regression check**: 212 passed (195 + 17 new), same 18 pre-existing
`app/viz/` failures, zero new failures. Playwright dashboard suite (9
tests, now including the chart-highlights markup) still green.

**Graph state**: F0, A1, A2, B1, C1, D1 GREEN. A3 unblocked. B2 in
progress. B3 still blocked (needs B2 + an email/Slack connector).

## B2 — Scheduler — GREEN

`app/report_store.py` (new — persistence extracted from `main.py`),
`app/scheduler.py` (new), `app/main.py` (`POST /api/schedules`,
`GET /api/schedules`, `POST /api/schedules/run`), `tests/test_scheduler.py`
(new, 10 tests).

- **Prerequisite refactor, done first because B2 needed it to not create a
  circular import**: `main.py`'s `_persist_report`/`_report_dir`/`_load_meta`
  moved to `report_store.py` unchanged (a relocation, not a rewrite — same
  on-disk file shapes, confirmed by re-running `test_run_qa_cli.py`, the
  test most tightly coupled to those exact files). Needed because the
  scheduler must persist what it generates the same way `main.py` does, but
  `main.py` will call the scheduler (for the run-now endpoint below) —
  scheduler importing `main.py` back would be circular.
- **Per-client schedule (cron-like) + template + data-source ref, persisted
  and reloadable.** `Schedule(client_id, data_source_ref, cadence,
  branding, runs)`, one JSON file per client under `schedules/`, mirroring
  `data_context.py`'s own established one-file-per-client convention rather
  than inventing a new persistence style.
- **Reuses Lever-1 warehouse connection + saved schema mapping (no
  re-mapping).** `run_schedule` is a thin wrapper around
  `report_builder.build_report_from_data_context(schedule.data_source_ref,
  ...)` — the identical call the manual "generate now" path makes.
  `save_schedule` refuses to save a schedule whose `data_source_ref` has no
  onboarded `data_context.py` entry, so a schedule can't silently exist
  pointing at nothing. **Verified against a real connector, not mocked**:
  tests build an actual SQLite database (`connectors/sqlite_connector.py` —
  the one connector this project runs end-to-end without live credentials)
  and onboard it through `data_context.save_data_context` for real.
- **Idempotent + dated: re-running the same as-of date reproduces
  byte-identical output.** Stated plainly why this can't mean "the LLM
  produces the same narrative twice" — F0's own CHANGELOG entry measured
  two independent runs of identical input differing by ~1KB in the
  rendered PDF from narrative variance alone. So idempotency here is a
  cache-key guarantee: a schedule that already has a `runs[as_of]` entry
  returns that exact `report_id` and re-generates nothing —
  `status="reused"`, distinct from `"generated"`. Verified end-to-end with
  two real report generations: same `as_of` date returns the same
  `report_id` on the second call; a different `as_of` date genuinely
  generates a new one.
- **Dry-run mode runs the whole cadence for a fixed date with no side
  effects.** `run_due_schedules(as_of, dry_run=True)` evaluates every saved
  schedule's cadence (`is_due` — daily always, weekly on Mondays, monthly
  on the 1st) and reports what would happen (`"dry_run"` if due,
  `"not_due"` if not) without calling `report_builder`, writing to disk, or
  updating the schedule file — verified directly: after a dry run,
  `report_store.GENERATED_DIR` has zero new entries and the schedule's
  `runs` dict is byte-identical to before.
- **Design note on due-ness vs. idempotency**: kept deliberately separate.
  `is_due` answers "should this cadence fire today"; the reuse check
  answers "has it already run for this date." A dry run reports both
  honestly — "this would fire, and it would reuse an existing report" is a
  meaningful, distinct answer from "this would fire and generate new."

Wired into `main.py` behind three endpoints; `POST /api/schedules/run`
defaults `dry_run=True` on purpose — an accidental call must never
side-effect-generate reports for every client.

**Regression check**: 222 passed (212 + 10 new), same 18 pre-existing
`app/viz/` failures, zero new failures. Playwright dashboard suite still
green (9 tests) after the `main.py` refactor.

**Graph state**: F0, A1, A2, B1, B2, C1, D1 GREEN. A3, B3 unblocked (B3's
delivery sub-criterion still needs an email/Slack connector once reached).

## A3 — Narrative ↔ chart link — GREEN

`app/narrative_links.py` (new), `app/chart_annotation.py` (`ChartAnnotation`
gains `direction`), `app/qa.py` (`QAReport` gains `citations`, `run_qa`
gains `charts` param, badge extended), `app/report_builder.py` (wired),
`tests/test_narrative_links.py` (new, 18 tests).

- **Narrative can cite a chart/figure by id; each cited figure exists and
  matches the annotation.** Citation syntax is an explicit
  `[[chart:<chart_id>]]` marker, deliberately not inferred from prose —
  same principle as A2's annotations: a reconciliation check only means
  something if the claim-to-chart link is unambiguous. `find_citations`
  scans every narrative text field (`executive_summary`, `highlights`,
  `watchouts`, `next_steps`, each section's `narrative` and
  `recommendations`). A citation naming a chart id that doesn't exist is
  flagged `unknown_chart_id`, never silently ignored.
- **Scoping decision stated plainly**: this node does not modify `agent.py`
  to make the LLM actually emit these markers. This project's local
  narrative model is a weak one whose output isn't reliably steerable
  enough to test prompt compliance against — building and thoroughly
  verifying the deterministic reconciliation *mechanism* is this node's
  job; getting an LLM to reliably cite charts is prompt-engineering work
  for a separate pass, and conflating the two would make this node's own
  tests depend on LLM behavior neither controlled nor verifiable here.
- **QA badge extended: a narrative claim tied to a chart must reconcile
  with that chart's data.** `run_qa(report, metrics, source_frames,
  charts=...)` now runs `check_chart_citations` as a fourth check; a
  mismatch adds `"chart_citations"` to `failing_checks` and fails the
  badge, same tier logic as the other three checks. `ChartAnnotation`
  gained a `direction` field (`"up"`/`"down"`, set only for
  `largest_delta`) so reconciliation compares structured data, not
  re-parsed annotation text. Citing a chart whose annotation has no
  direction (a peak or outlier) or whose sentence makes no directional
  claim correctly resolves to `no_directional_claim`, not a false mismatch
  — verified explicitly, since a naive implementation would over-flag here.
- **Test: an injected mismatch (narrative says +10%, chart shows -10%) is
  caught as FAIL.** The literal exit-criterion example, plus the word-based
  equivalent ("increased" vs. a chart that shows "down"). **Verified against
  a real report object**, not just hand-built fixtures: built a real report
  from sample data, found its actual `largest_delta` chart (sales monthly
  revenue, real direction `"down"`), injected a citation claiming `"grew"`
  — `run_qa` correctly returned `badge="FAIL"` with the exact chart id and
  both directions named in the reason string; the correctly-worded version
  of the same citation reconciled cleanly.
- Omitting `charts` from `run_qa` doesn't silently skip citation checking —
  a marker in the text with nothing to verify it against still resolves to
  `unknown_chart_id` (fail-safe, not fail-open), tested directly since it's
  the kind of edge case easy to get backwards.

**Regression check**: 240 passed (222 + 18 new), same 18 pre-existing
`app/viz/` failures, zero new failures. `test_run_qa_cli.py` (most tightly
coupled to `QAReport`'s exact shape) and the Playwright dashboard suite
both still green after extending `QAReport`'s fields.

**Graph state**: F0, A1, A2, A3, B1, B2, C1, D1 GREEN. Only B3 remains
(blocked on an email/Slack connector for full delivery, though the
QA-gate/logging mechanics can still be built and tested without one).

## B3 — Delivery — GREEN

`app/delivery.py` (new), `app/scheduler.py` (`Schedule` gains
`client_recipients`/`consultant_recipients`/`delivery_channel`,
`run_schedule`/`run_due_schedules` gain `deliver`), `app/main.py`
(`/api/schedules/run` gains `deliver`), `tests/test_delivery.py` (new, 16
tests), `tests/test_scheduler.py` (+3 B2↔B3 integration tests).

- **Email delivery of the PDF + QA badge summary; templated, per-client
  recipient list.** `deliver_report(obj, recipients, ...)` attaches the
  real rendered PDF (`render_pdf_from_object`, the same F0 renderer
  everything else uses) and reuses D1's `export_email_html(obj)` output
  verbatim as the message body — one email-shaped rendering of the object,
  not a second independent template. Recipients are per-call/per-schedule,
  never a shared default (checked directly: two different objects
  delivered back-to-back land in two distinct, non-overlapping recipient
  lists).
- **Slack delivery optional, behind the same interface.** `channel="slack"`
  runs through the identical `deliver_report` function, gate, and logging —
  only the channel implementation swaps (`EmailChannel`/`SlackChannel`,
  registered in one `CHANNELS` dict, the same pattern
  `connectors/__init__.py` already uses for warehouse connections). An
  unknown channel name raises rather than silently no-op-ing.
- **A FAILing QA badge blocks auto-send (or sends to the consultant for
  review, not the client).** Checked structurally, not by convention: a
  FAIL with no `consultant_recipients` configured never even calls the
  channel (`status="blocked"`, zero send attempts — verified by asserting
  the fake channel recorded no calls at all); a FAIL with consultant
  recipients configured redirects there with a `"[NEEDS REVIEW]"` subject
  prefix and `status="sent_to_consultant"` — the client address is
  confirmed absent from the actual recipient list sent, not just "not the
  primary" one. PASS-WITH-WARNINGS is verified to still send normally —
  only a hard FAIL gates.
- **Delivery attempts logged; success/failure is observable, not silent.**
  Every outcome (`sent`, `sent_to_consultant`, `blocked`, `unavailable`,
  `failed`) is appended as a JSON line to `delivery_logs/<report_id>.jsonl`
  — append-only, so a retried delivery keeps its full history rather than
  overwriting the record of the first failed attempt (tested directly: two
  attempts against the same report_id produce two log entries in order).
- **Channels are genuinely unavailable without a connector — same honesty
  as D1's Google Slides, never a fake success.** `EmailChannel` needs
  `SMTP_HOST`; `SlackChannel` needs `SLACK_WEBHOOK_URL`. Neither is set in
  this environment, so both were verified against their *real* send paths
  returning `status="unavailable"` with a stated reason — not mocked
  around, the actual honest outcome. The QA-gate/logging logic itself is
  tested independently of connector availability via an injected fake
  channel (`channel_impl`), the same dependency-injection shape
  `connectors/__init__.py` already established — so this node's core logic
  is fully verified even though sending itself can't be, in this
  environment.
- **B2 ↔ B3 integration**: `Schedule` now carries recipient lists;
  `run_schedule(..., deliver=True)` hands a freshly-**generated** report to
  `deliver_report` — deliberately never for a `"reused"` result, so an
  idempotent re-run doesn't re-email a client for a no-op. Defaults to
  `deliver=False` (and the API route's `deliver` param defaults to `False`
  too), matching the same accidental-call safety already established for
  `dry_run`. Verified with a real end-to-end schedule run: a fresh
  generation with recipients configured produced exactly one delivery-log
  entry; one with `deliver=True` but no recipients configured attempted
  nothing at all (confirmed empty log); the default (`deliver` omitted)
  attempts nothing either.

**Regression check**: 246 passed (240 + 16 new in `test_delivery.py`) in
the main suite, same 18 pre-existing `app/viz/` failures, zero new
failures. `test_html_dashboard.py` (9, Playwright) and `test_scheduler.py`
(13, including the 3 new B2↔B3 integration tests — 5 real LLM-backed
report generations across the file, ~6.5 min total on this environment's
local 3B model) both fully green.

**Graph state — mission complete for the requested scope: F0, A1, A2, A3,
B1, B2, B3, C1, D1 all GREEN.** Track E (product shell) stays gated per
the mission's explicit instruction, unlocked now that four tracks (A, B, C,
D) have shipped real, tested value — but not started.

# Changelog — Auto-QA layer & HTML dashboard

Dev-facing log, one entry per exit criterion as it goes green. See
`docs/build-plan.md` (Lever 2, Lever 4) for the product framing.

# Lever 5 — Ad-hoc Visualization Engine, rebuilt as a dependency graph (L0-L7)

Rebuild-to-stricter-spec of the app/viz/ engine built earlier, executed one
node at a time (L0 -> L7, topological order, each node's own tests scoped to
that node). Downstream nodes intentionally reference the pre-rebuild type
vocabulary until their own turn comes — expected, not regression; each
node's report below states the exact scope of what's red/green at that point.

## L0 — Schema-agnostic profiling — GREEN

`app/viz/profiler.py` (rewritten), `tests/test_viz_profiler.py` (rewritten, 28 tests).

- **Any CSV/Excel loads; delimiter/encoding/decimal-locale auto-detected.**
  `load_any` now returns `(df, load_meta)`. Encoding: tries
  utf-8-sig -> utf-8 -> cp1252 -> latin-1 (latin-1 never fails, so this
  always terminates). Delimiter: `csv.Sniffer` over `,;\t|`, falls back to
  comma when there's nothing to sniff (e.g. a single-column file). Decimal:
  semicolon-delimited -> comma-decimal, the standard European-locale Excel
  export shape (documented correlation, not a guess). Tested against all
  four paths plus a real non-UTF8-encoded file.
- **Every column typed into 5 categories, numeric IDs never treated as
  quantities.** `numeric_quantity` / `numeric_identifier` / `categorical` /
  `temporal` / `free_text` (`mixed`/`empty` remain as explicit degenerate
  states). `numeric_identifier` carries a disclosed `identifier_reason`:
  `year` (whole number in 1900-2100), `leading_zero_formatting` (e.g.
  "00501" — airtight evidence, a real quantity's canonical text form never
  has a meaningless leading zero), `fixed_width_code` (constant-width
  all-digit strings at classic id widths — 5 = ZIP, 10 = phone — narrow and
  explicit on purpose, not a broad guess), `high_uniqueness` (the original
  near-unique-integer route). **Root-cause fix along the way**: the old
  `load_any` let pandas auto-infer dtypes on read, silently stripping
  leading zeros from zip-like columns before anything ever saw them — now
  everything loads as raw text first, so no formatting evidence is lost
  before it can be used as identifier evidence.
- **Per-column profile** (count/null%/cardinality/min-max/distribution):
  unchanged shape, carried over.
- **Degenerate cases reported, not crashed on**: constant column (flagged
  via a warning), all-null column (`empty` type), single-row file (verified
  no crash across numeric/temporal typing).

Live-verified against real messy production data
(`sample_data/messy-demo/*`), not just synthetic fixtures — and it found a
real, previously-wrong classification: `deal_id` (183 rows, 179 unique —
97.8%) had *just* missed the old 98% high-uniqueness identifier threshold
and was being treated as a plain summable quantity. The new
`fixed_width_code` route (deal_id is a constant-width 5-digit code) catches
it correctly regardless of that threshold.

**Also fixed while verifying**: `_looks_like_prose`/`_looks_like_id_token`
measured raw string length without stripping whitespace first — a messy
export with padding around otherwise-short values could inflate the
free-text/id-token measurement purely from padding, not content. Now strips
before measuring.

Full-suite state at this point: 116/134 passing. The 18 failing are all in
`test_viz_engine.py`/`test_viz_qa_integration.py`/`test_viz_suggestions.py`/
`test_viz_end_to_end.py` — every one of them calling `aggregates.py`/
`suitability.py`/`engine.py`/`suggestions.py`, which still hardcode the old
4-type vocabulary. Expected: those are L2/L3/L4/L6, not yet rebuilt.

## L1 — Value normalization — GREEN

`app/viz/normalize.py` (new), `tests/test_viz_normalize.py` (new, 19 tests).

- **Currency/thousands/percent/"1.2K" strings parse deterministically.**
  `normalize_numeric_column` handles currency symbols, accounting
  parentheses-negative ("(500)" -> -500), K/M/B magnitude suffixes, and
  percent-to-fraction ("12%" -> 0.12 — the spreadsheet convention, logged so
  a caller can multiply back for display), all composable (e.g. "($1,500.00)"
  correctly -> -1500.0). Thousands-separator handling reads
  `load_meta["decimal"]` from L0 so "1.234,56" (comma-decimal locale) and
  "1,234.56" (US locale) both resolve to 1234.56, not each other's answer.
- **Every normalization logged and reversible; raw column preserved.**
  `NumericNormalizationResult.raw` is the untouched original series;
  `.log` is one entry per value that was actually transformed
  (original -> parsed -> rule, e.g. `"currency_symbol_stripped+
  thousands_separator_removed"`) — plain numbers that needed no
  transformation get no log entry (nothing to disclose). `format_back()`
  gives a best-effort reconstruction of a display string for common rules —
  documented as a convenience on top of the real guarantee (raw is always
  kept), not a claim of exact round-trip fidelity.
- **Dates parse from mixed formats + Excel serials; timezone stated.**
  `normalize_temporal_column` tries string-date parsing first (mixed
  formats), then reinterprets a bare number in the plausible Excel-serial
  range (1-60000, using the correct 1899-12-30 epoch — Excel's leap-year
  bug means 1900-01-01 would be off by one) as a date if string parsing
  failed. Timezone rule is a field on the result, not implicit: any
  tz-aware timestamp is converted to UTC then stored naive.
- **Unparseable cells flagged, never silently coerced to 0/fake-NaN.**
  `.unparseable` is a distinct list from ordinary nulls — a blank cell and
  a cell containing "not a number" are different situations (nothing there
  vs. something there that couldn't be read) and are reported separately,
  never conflated into the same NaN with no explanation.

Live-verified against the exact three columns L0 flagged as "mixed" type on
real messy data (`revenue_usd`, `ctr`, `amount_usd` in
`sample_data/messy-demo/*`): 100% parse success, zero unparseable, correct
rules applied (`currency_symbol_stripped`, `percent_to_fraction`,
`thousands_separator_removed`) — confirms L0's "mixed" flag and L1's parser
are actually solving the same real problem, not just passing synthetic
fixtures.

Combined L0+L1 scope: 47/47 tests passing.

## Lever 4 — Interactive HTML dashboard — ALL 6 EXIT CRITERIA GREEN

`app/theme.py` (new), `app/html_dashboard.py` (new),
`app/templates/dashboard.html` (new), `GET /api/report/{id}/dashboard` in
`main.py`.

**Shared theme file**: `charts.py`'s validated palette moved to `theme.py`
(single source now — `charts.py` imports it, doesn't redefine it).
`report.html` *also* switched from its own literal hex duplicates to the
same tokens via the Jinja context — pre-existing debt (two independent
copies of the same colors) that would have become three copies the moment
the dashboard shipped its own. Verified the PDF's rendered HTML is
byte-identical in every color/font value before and after. One snag:
Jinja's autoescaping mangled the quoted font-family list inside `<style>`
(`"Helvetica"` → `&#34;Helvetica&#34;`, which xhtml2pdf's CSS parser then
choked on) — fixed with `|safe` on that one token, since it's static
trusted content, not user input.

**Drill-down uses metrics.py's own pre-computed aggregation levels**
(`by_channel`, `by_device`, `by_rep`, `by_lead_source`, SEO `top_issues`) —
not raw source rows, which aren't persisted anywhere in this app today (not
for the PDF, not for Power BI). This was a deliberate scope decision
(confirmed before building): keeps "one source, no separate query path"
literally true — the dashboard reads exactly the same `metrics_payload`
`metrics.json` already persists — and keeps the 50k-row perf criterion
trivial, since none of those levels grow with source size.

**Two real bugs caught via live Playwright testing, before they became
test fixtures:**
1. The client-side cell formatter in `dashboard.html` checked for the
   substring `"revenue"` before checking the `_pct` suffix, so
   `revenue_change_pct` (a percentage) rendered as a dollar amount
   (`$-34` instead of `-33.6%`). Fixed by checking `_pct`/`"rate"` first —
   the unambiguous signal — before any currency-ish substring check.
2. `insights.py::_lead_source_efficiency` mutates the dicts it's handed in
   place (adds a private `_avg_deal` key). `report_builder.py` only avoids
   this side effect by deep-copying `metrics_payload` before calling
   `compute_insights`; `html_dashboard.py` built its drill-down tables from
   references into the *same* dicts before doing the same deep-copy, so the
   mutation leaked a stray, wrongly-formatted column into the dashboard's
   "Win Rate" drill-down. Fixed by mirroring the same deep-copy guard.

**Verification, all 6 criteria:**
- Self-contained/offline: Playwright with all network requests hard-aborted
  — zero failed or even attempted requests, loaded via a `data:` URL (no
  file server involved at all).
- Same artifact: `build_dashboard()` takes the literal `metrics_payload`
  object `report_builder.py` computes for the PDF path — not a copy, not a
  re-fetch.
- KPI cards/filter/drill-down: live-verified against a real generated
  report, then locked in as automated browser interaction tests (click a
  card → drill table appears with the right row count; apply the channel
  filter → row count narrows correctly).
- Visual language: verified via `getComputedStyle` in a real browser
  (`rgb(42, 120, 214)` for `branding.primary_color`, `rgb(252, 252, 251)`
  for `theme.SURFACE`), not just string-matching the HTML source.
- Traceability: every KPI card's displayed string run through
  `qa.check_traceability` against the same `metrics_payload` — `ok`.
- Empty/large: empty `metrics_payload` renders a clean placeholder with zero
  JS errors; a 50k-row synthetic source produces a ~19KB dashboard in well
  under a second, because drill-down only ever touches metrics.py's
  already-small pre-computed levels.

Regression: `powerbi/validate_pbip.py` and `check_field_references.py`
re-run and green (26/26 schema validation, 20 visuals resolve) — confirms
this lever didn't touch the Power BI deliverable, as the build plan
requires. Full backend suite: 42/42 passing (7 new for the dashboard).

`pytest`/`playwright` moved to a new `requirements-dev.txt` rather than
`requirements.txt` — the running app never needs a browser engine, only the
test suite does.

---

## Number traceability — DONE

`app/qa.py::check_traceability(report, metrics_payload)`.

Extracts every numeric literal ($, %, comma-grouped, K/M/B-abbreviated) from
the narrative fields agent.py writes (`executive_summary`, `highlights`,
`watchouts`, `sections[].narrative`, `sections[].recommendations`,
`next_steps`), and matches each against every numeric leaf in
`metrics_payload`.

Three tiers:
- **exact** — literal equals a metric value.
- **rounded** — literal is a plausible display-rounding of a metric value.
  Rounding granularity is inferred from the literal itself (decimal places
  shown, or trailing zeros for whole numbers/abbreviations), capped at 5% of
  the candidate's magnitude — this is what lets "$45,000" pass against a true
  value of $45,231.50 while still failing "$99,999" against that same value,
  without hardcoding per-field tolerance.
- **fail** — no metric value is close enough at any tier. Catches both wrong
  totals and fully fabricated numbers.

Bare 4-digit numbers in 1900-2100 are excluded (calendar years in prose, not
metrics) — a narrow, explicit exception rather than a broad heuristic.

Tests: `tests/test_qa.py`, 8/8 passing — clean report (all-trace, includes a
legitimate rounding case), broken report (wrong total + fabricated number +
still-correct rounded number, asserts exactly 2 FAIL findings and 0 false
positives on the correct one), a bare-year sentence, and a near-miss count
that must fail despite being "close" (12,000 claimed vs. 12,345 actual).

## Aggregation sanity — DONE

`app/qa.py::check_aggregation_sanity(metrics_payload, source_frames)`.

Recomputes each present source ("analytics"/"seo"/"sales") by calling
`metrics.py`'s own aggregation functions again on the source rows, then
deep-diffs every numeric leaf against the persisted `metrics_payload` with
tolerance 0 for int-vs-int and 1e-6 otherwise. Deliberately reuses
`metrics.py` rather than reimplementing "how to sum/average" a second time —
a second implementation would drift from the first and the check would stop
meaning anything. A source with no rows supplied is marked
`inconclusive_sources`, not folded into pass/fail — "couldn't check" and
"checked and it's wrong" are different outcomes and the badge (once built)
needs to say which one happened.

**Source fingerprint** (`compute_source_fingerprint`): row count + sha256
over the source rows, computed at generation time in both
`report_builder.py` front doors (`build_report`, `build_report_from_data_context`)
and persisted to `generated/<id>/metrics.json` via `main.py`. Exists because
aggregation sanity re-derives source rows independently at QA time, and if
QA runs decoupled in time from generation (e.g. on a schedule, once Lever 3
lands) the source may have moved in between — the fingerprint is what lets a
later check tell "source changed, this check is inconclusive" apart from "the
report's numbers are actually wrong." Not yet consumed anywhere (that's the
headless runner's job, still red below) — built now because it has to be
computed at generation time, and generation-time code only gets touched once
per slice.

Verified live end-to-end against `sample_data/` (not just unit fixtures):
`build_report()` with all three real sample files still produces a valid PDF
(`smoke_test.py`, unchanged output), and `_persist_report` now writes a
`metrics.json` with real fingerprints and real recomputable metrics.

Tests: `tests/test_qa.py`, 13/13 passing (5 new: pass on real recomputation,
catch on a tampered total, missing-source is inconclusive not failed,
fingerprint stability, fingerprint changes on row drift).

## Unsupported-claim scan — DONE

`app/qa.py::check_unsupported_claims(report, metrics_payload)`.

Splits each narrative field into sentences. A sentence with a number is
governed by that number's traceability (reusing the same matcher as
criterion 1). A sentence with no number but a trend word ("grew",
"declined") must be consistent with the sign of some `*_change_pct`/
`*_momentum_pct` field somewhere in metrics_payload. A sentence with a
comparative/ranking word ("top", "highest", "higher-performing") must have
some ranked/grouped list (`by_channel`, `by_rep`, ...) in metrics_payload to
plausibly draw from. Everything else (no number, no keyword) isn't a
checkable claim and isn't flagged.

**Found and fixed three real bugs by running this against a live,
non-deterministic local-model-generated report** (not just synthetic test
fixtures) — this is the reason that verification step is worth the extra
time on a check like this one:
1. "Double down on X" (the agent's own stock recommendation phrasing) was
   being read as a claim that X is declining, because "down" is a literal
   substring match. Fixed with a narrow idiom guard stripped before
   tokenizing.
2. `metrics.py` stores `sessions_change_pct` signed (e.g. -20.8); correct
   narrative prose writes that as unsigned magnitude + a direction word
   ("a 20.8% decrease"), never a literal minus sign. Every legitimately
   correct percentage-change sentence in a real report was failing
   traceability. Fixed by letting percent literals match a metric's
   absolute value (`_match_number(..., allow_sign_flip=True)` for
   percentages) — and, so this doesn't just paper over a real sign error,
   the claim scan separately checks that the sentence's direction *word*
   actually agrees with the sign of whatever it matched
   (`test_percent_sign_word_contradicting_actual_metric_sign_is_unlinked`).
3. "higher-performing lead sources" was being read as a time-trend claim
   (needs a positive `change_pct`) when it's actually a static ranking
   comparison among sources (needs ranked data, same as "top"/"best").
   Reclassified "higher"/"lower" out of the trend-word set into the
   comparative/ranking set.

A literal minus sign in the model's own output ("-28.7%") is also covered —
the number regex doesn't capture the sign as part of the match, so it only
traces because of the same sign-flip allowance from fix #2.

Tests: `tests/test_qa.py`, 25/25 passing (12 new, including regression tests
for all three live-caught bugs).

## Badge output — DONE

`app/qa.py::run_qa(report, metrics_payload, source_frames=None) -> QAReport`,
`QAReport.to_dict()` for the machine-readable form.

Combines all three checks. Badge tiers:
- **FAIL** — any of the three checks found something concretely wrong
  (`failing_checks` names which). Overrides everything else.
- **PASS-WITH-WARNINGS** — nothing wrong, but something couldn't be fully
  confirmed: a legitimately-rounded display number (criterion 1's WARNING
  tier), or a source with no rows supplied so aggregation sanity couldn't
  run against it (`inconclusive_sources`).
- **PASS** — every check found nothing to flag, no warnings either.

`source_frames` is optional; omitting it just means aggregation sanity marks
every source inconclusive rather than skipping silently — the badge can
never read a bare PASS you can't actually back up.

Verified live end-to-end against `sample_data/` with a real (non-deterministic,
local-model) generated report: this run's badge correctly came back FAIL
because the model wrote "an increase of 5.5%" for Paid Social sessions when
the real metric is -5.5% (an actual decline) — a genuine model error, not a
QA-checker bug, caught exactly as the feature is meant to.

Tests: `tests/test_qa.py`, 31/31 passing (6 new: PASS/FAIL/PASS-WITH-WARNINGS
for each trigger path, multiple simultaneous failing checks all reported —
not just the first one found — and a JSON round-trip check on `to_dict()`).

## Headless CLI — DONE

`scripts/run_qa.py`.

Loads `meta.json` (the narrative) and `metrics.json` (metrics_payload +
source_fingerprints) from a `generated/<id>/` directory, re-derives source
rows from caller-supplied files via the exact same `parsers.py` functions
`report_builder.py` uses at generation time (not a second parsing path that
could diverge), and runs `qa.run_qa`.

Consumes the source fingerprint from criterion 2: before trusting a
re-derived source for aggregation sanity, it's fingerprinted again and
compared to what was recorded at generation time. A mismatch excludes that
source from aggregation sanity (inconclusive, not failed) and adds it to
`drifted_sources` in the output — this is what keeps a source that moved
between generation and a later headless QA run (the scenario that matters
once Lever 3's scheduling lands) from producing either a false FAIL or a
false confidence-inspiring PASS.

Exit codes, most severe first: `1` badge is FAIL, `2` no FAIL but a source
drifted, `0` clean (PASS or PASS-WITH-WARNINGS, no drift). FAIL takes
priority over drift by design — a confirmed bad number is more actionable
than "we couldn't double-check this one source," and verified this way: with
both a tampered metric *and* a drifted source present simultaneously, exit
code is 1, not 2.

Writes `qa.json` into the report directory by default (`--no-write` to skip).

Verified three ways, live against `generated/` before being locked in as
automated tests: a clean run (exit 0), a tampered `metrics.json` (exit 1,
mismatch precisely identified), and a source swapped for a modified copy
(exit 2, correctly excluded from aggregation rather than false-failing).

Tests: `tests/test_run_qa_cli.py`, run as real subprocesses (not direct
function calls) so exit codes are checked the way an actual caller would see
them — 4 tests, all passing, covering all three exit codes plus the
`--no-write` default.

**All 5 exit criteria are green.** Full suite: `tests/`, 35/35 passing.
`smoke_test.py` (full real pipeline, unaffected by any of this work)
still produces a valid PDF end-to-end.
