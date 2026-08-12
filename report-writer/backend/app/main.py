from __future__ import annotations

import asyncio
import io
import json
import os
import threading
import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

from . import auth, html_dashboard, report_builder, report_store, template_specs
from .db import get_db
from .exports import export_report
from .report_object import ReportObject
from .settings import settings


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Autonomous scheduling is opt-in: starting the app locally to poke at
    # the API must never silently start generating reports (LLM calls,
    # warehouse queries) for every saved schedule. Set AUTO_SCHEDULER_ENABLED
    # to turn it on; AUTO_SCHEDULER_INTERVAL_SECONDS/AUTO_SCHEDULER_DELIVER
    # tune it, same env-var convention as SMTP_*/IMAP_*/SLACK_WEBHOOK_URL.
    stop_event = None
    if os.environ.get("AUTO_SCHEDULER_ENABLED", "").lower() in ("1", "true", "yes"):
        from . import scheduler
        interval = int(os.environ.get("AUTO_SCHEDULER_INTERVAL_SECONDS", "3600"))
        deliver = os.environ.get("AUTO_SCHEDULER_DELIVER", "").lower() in ("1", "true", "yes")
        _, stop_event = scheduler.start_background_loop(interval_seconds=interval, deliver=deliver)
    yield
    if stop_event is not None:
        stop_event.set()


# E1 Slice 5 -- CSRF is enforced here, once, for every route on the app
# (including auth.router's, via include_router below), not per-route: a
# newly added POST/PUT/PATCH/DELETE endpoint is protected by default.
# auth.require_csrf itself no-ops on GET/HEAD/OPTIONS and on any request
# that carries no session cookie (a machine caller's X-API-Key/service-token
# request was never CSRF-vulnerable in the first place -- see its own
# docstring), so this is safe to apply globally.
app = FastAPI(title="AI Report Writer API", lifespan=lifespan, dependencies=[Depends(auth.require_csrf)])

# E1 -- CORS origins/credentials now come from app.settings, not a hardcoded
# localhost-only list: the frontend needs allow_credentials=True to send the
# session cookie cross-origin at all, and the allowlist has to be exact (no
# "*") the moment credentials are allowed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# E1 -- Authlib's Google OAuth redirect handshake needs somewhere to stash
# state/nonce/PKCE verifier between /google/login and /google/callback.
# This is a SEPARATE, short-lived, itsdangerous-signed cookie
# ("session" by Starlette's own default name) -- it is NOT this app's real
# user session (that's app/auth.py's own DB-backed AuthSession/cookie).
app.add_middleware(SessionMiddleware, secret_key=settings.oauth_state_secret_key)
app.include_router(auth.router)

# E1 -- the old blanket, all-or-nothing X-API-Key middleware is gone: every
# tenant-scoped route below now requires a real session
# (Depends(auth.get_tenant_id)) instead. The one remaining non-human caller
# -- a cron/service trigger hitting POST /api/schedules/run with no browser
# session -- gets its own narrow, scoped check (_resolve_schedule_run_scope,
# defined next to that route), not a global gate on every request.

# Fast-path in-memory cache for the current process. The real source of
# truth is the `generated_reports` table (see report_store.py/store_models.py),
# so reports survive a --reload restart and a "recent reports" list works
# without depending on this cache at all. Track E1: every entry is stamped
# with the tenant_id that created it, and every read checks it -- the DB
# store's tenant namespacing means nothing if this cache is a second,
# unchecked access path to the same data (see the plan's finding 2).
_REPORTS: dict[str, dict] = {}

# In-memory progress tracking for in-flight generations, polled by the SSE
# endpoint below. Single-process demo scale — no pub/sub needed. Same
# tenant-stamping rule as _REPORTS.
_JOBS: dict[str, dict] = {}


def _cached_report(report_id: str, tenant_id: str) -> dict | None:
    """The tenant-checked read for _REPORTS -- a hit for another tenant's
    report_id is treated exactly like a cache miss, falling through to the
    (also tenant-namespaced) disk path, which 404s correctly rather than
    ever handing back another tenant's cached bytes."""
    cached = _REPORTS.get(report_id)
    if cached and cached.get("tenant_id") == tenant_id:
        return cached
    return None

_persist_report = report_store.persist_report
_load_meta = report_store.load_meta


class ConnectorTestRequest(BaseModel):
    kind: str
    config: dict


class OnboardRequest(BaseModel):
    client_id: str
    kind: str
    config: dict
    table_map: dict[str, str]  # source_type ("analytics"/"seo"/"sales") -> real table name
    # AI-proposed mappings aren't always complete (small local models especially
    # miss non-obvious renames) — this lets a human fill in or correct specific
    # fields before the mapping is saved, e.g. {"sales": {"sales_rep": "owner"}}
    manual_overrides: dict[str, dict[str, str]] | None = None


@app.post("/api/data-sources/test")
def test_data_source(req: ConnectorTestRequest, tenant_id: str = Depends(auth.get_tenant_id)):
    """Verify a connection and list what's there — the first step of onboarding
    a client's warehouse instead of asking them for CSV exports every month.
    tenant_id isn't used for scoping here (nothing is persisted yet), but the
    endpoint still requires a real session -- no anonymous connector-probing
    endpoint (a real SSRF/credential-probing surface if left open)."""
    from .connectors import create_connector
    from .connectors.base import ConnectorError
    try:
        connector = create_connector(req.kind, req.config)
        tables = connector.list_tables()
        connector.close()
    except ConnectorError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "tables": tables}


@app.post("/api/data-sources/onboard")
def onboard_data_source(req: OnboardRequest, tenant_id: str = Depends(auth.get_tenant_id)):
    """Discovers each mapped table's real schema and proposes a column mapping
    (AI-assisted if a provider is reachable, deterministic fuzzy-match
    otherwise — see data_context.py), then saves it so every future report for
    this client pulls live from the warehouse with no re-discovery step."""
    from . import data_context
    from .connectors import create_connector
    from .connectors.base import ConnectorError
    try:
        connector = create_connector(req.kind, req.config)
    except ConnectorError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        sources = data_context.discover_and_propose(connector, req.table_map)
    except ConnectorError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        connector.close()

    for source_type, overrides in (req.manual_overrides or {}).items():
        if source_type in sources:
            sources[source_type]["column_map"].update(overrides)
            sources[source_type]["mapping_method"] = "ai+manual" if sources[source_type]["mapping_method"] == "ai" else "fuzzy_match+manual"

    data_context.save_data_context(tenant_id, req.client_id, req.kind, req.config, sources)
    return {"client_id": req.client_id, "sources": sources}


class OnboardInboxRequest(BaseModel):
    client_id: str
    provider: str = "gmail"  # "gmail" | "outlook" (basic-auth-enabled accounts only, see email_source.py)
    username: str
    password: str  # an app password, not the account password
    mailbox: str = "INBOX"
    search: str = "UNSEEN"


@app.post("/api/data-sources/onboard-inbox")
def onboard_inbox_source(req: OnboardInboxRequest, tenant_id: str = Depends(auth.get_tenant_id)):
    """Ingestion mode 2 (hosted inbox polling): saves this client's mailbox
    credentials (encrypted at rest — see data_context.py) so a schedule can
    fetch fresh attachments from it automatically on every cadence firing,
    with no manual /api/generate-report/from-inbox call needed once this is
    onboarded. Verifies the connection actually works before saving, same
    "test then save" shape /api/data-sources/onboard already uses for
    warehouses — a typo'd app password fails loudly here, not on the first
    scheduled run days later."""
    from . import data_context, email_source
    from .connectors.base import ConnectorError
    try:
        connector = email_source.create_inbox_connector(req.provider, req.username, req.password)
        connector.close()
    except ConnectorError as exc:
        raise HTTPException(400, str(exc)) from exc

    config = {"provider": req.provider, "username": req.username, "password": req.password,
              "mailbox": req.mailbox, "search": req.search}
    data_context.save_data_context(tenant_id, req.client_id, "imap_inbox", config, {})
    return {"client_id": req.client_id, "kind": "imap_inbox"}


class OnboardSlackRequest(BaseModel):
    client_id: str
    bot_token: str  # xoxb-... with files:read + channels:history (or groups:history) scopes
    channel_id: str


@app.post("/api/data-sources/onboard-slack")
def onboard_slack_source(req: OnboardSlackRequest, tenant_id: str = Depends(auth.get_tenant_id)):
    """Ingestion mode 2 (hosted inbox polling), Slack variant — same shape
    as onboard-inbox, sourced from a channel's shared files instead of a
    mailbox. See slack_source.py's module docstring for how to create the
    bot token."""
    from . import data_context, slack_source
    from .connectors.base import ConnectorError
    try:
        connector = slack_source.create_slack_connector(req.bot_token, req.channel_id)
        connector.close()
    except ConnectorError as exc:
        raise HTTPException(400, str(exc)) from exc

    config = {"bot_token": req.bot_token, "channel_id": req.channel_id}
    data_context.save_data_context(tenant_id, req.client_id, "slack_inbox", config, {})
    return {"client_id": req.client_id, "kind": "slack_inbox"}


@app.get("/api/data-sources")
def list_data_sources(tenant_id: str = Depends(auth.get_tenant_id)):
    from . import data_context
    return {"data_sources": data_context.list_data_contexts(tenant_id)}


@app.get("/api/data-sources/{client_id}")
def get_data_source(client_id: str, tenant_id: str = Depends(auth.get_tenant_id)):
    from . import data_context
    ctx = data_context.load_data_context(tenant_id, client_id)
    if not ctx:
        raise HTTPException(404, "No data source saved for this client_id")
    return ctx


@app.delete("/api/data-sources/{client_id}")
def delete_data_source(client_id: str, tenant_id: str = Depends(auth.get_tenant_id)):
    """Undoes /api/data-sources/onboard[-inbox|-slack] -- e.g. a re-do after
    pasting the wrong service account key. Any Schedule still pointing at
    this client_id isn't touched here; its next run fails loudly instead
    (see data_context.delete_data_context's docstring)."""
    from . import data_context
    if not data_context.delete_data_context(tenant_id, client_id):
        raise HTTPException(404, "No data source saved for this client_id")
    return {"ok": True}


class CreateScheduleRequest(BaseModel):
    client_id: str
    data_source_ref: str
    cadence: str
    branding: dict = {}


@app.post("/api/schedules")
def create_schedule(req: CreateScheduleRequest, tenant_id: str = Depends(auth.get_tenant_id), db: Session = Depends(get_db)):
    from . import plans, scheduler
    from .models import Tenant

    tenant = db.get(Tenant, tenant_id)
    plan = plans.get_plan(tenant.plan)
    if not plan.can_schedule:
        raise HTTPException(402, f"Recurring schedules aren't included in the {plan.label} plan. Upgrade to add one.")

    # Only a genuinely NEW client counts against the cap -- re-saving an
    # already-scheduled client_id (updating its cadence/branding) is an
    # update, not a new active-client relationship.
    already_scheduled = {s.client_id for s in scheduler.list_schedules_for_tenant(tenant_id)}
    if req.client_id not in already_scheduled and plan.max_active_clients is not None:
        if len(already_scheduled) >= plan.max_active_clients:
            raise HTTPException(
                402,
                f"Your {plan.label} plan allows up to {plan.max_active_clients} active clients "
                "(recurring-schedule relationships). Upgrade your plan to add more.",
            )

    sched = scheduler.Schedule(
        tenant_id=tenant_id, client_id=req.client_id, data_source_ref=req.data_source_ref,
        cadence=req.cadence, branding=req.branding,
    )
    try:
        scheduler.save_schedule(sched)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return sched.to_dict()


@app.get("/api/schedules")
def list_schedules(tenant_id: str = Depends(auth.get_tenant_id)):
    from . import scheduler
    return {"schedules": [s.to_dict() for s in scheduler.list_schedules_for_tenant(tenant_id)]}


def _resolve_schedule_run_scope(request: Request, db: Session = Depends(get_db)) -> str | None:
    """POST /api/schedules/run has two legitimate kinds of caller, and they
    get different scopes on purpose (see scheduler.run_due_schedules's own
    docstring):
      - a cron/service trigger with a valid X-Scheduler-Token matching
        settings.scheduler_service_token -> None, meaning "every tenant"
        (this is the intentional infra path, not a bypass).
      - a real human session -> that session's own tenant_id, never another
        tenant's.
    Neither present -> 401. An empty/unset scheduler_service_token never
    matches a blank header (the `and` short-circuits), so leaving it
    unconfigured can't accidentally open the all-tenants path."""
    token = request.headers.get("X-Scheduler-Token")
    if settings.scheduler_service_token and token == settings.scheduler_service_token:
        return None
    raw_cookie = request.cookies.get(settings.session_cookie_name)
    session = auth.get_current_session(db, raw_cookie)
    if session is not None:
        return session.tenant_id
    raise HTTPException(401, "Not authenticated.")


@app.post("/api/schedules/run")
def run_schedules(as_of: str | None = None, dry_run: bool = True, deliver: bool = False,
                   scope_tenant_id: str | None = Depends(_resolve_schedule_run_scope)):
    """Fires the whole cadence for a given as_of date (today by default).
    dry_run defaults to True — an accidental call to this endpoint should
    never side-effect-generate reports for every client; a real run needs
    the caller to explicitly opt out of dry-run. deliver defaults to False
    for the same reason — generating without also emailing every client is
    the safer accidental-call outcome. scope_tenant_id is None only for a
    valid service-token call (every tenant's due schedules); a
    session-authenticated caller is always scoped to just their own tenant."""
    from datetime import date as date_cls

    from . import scheduler
    as_of_date = date_cls.fromisoformat(as_of) if as_of else date_cls.today()
    results = scheduler.run_due_schedules(as_of_date, dry_run=dry_run, deliver=deliver, tenant_id=scope_tenant_id)
    return {"as_of": as_of_date.isoformat(), "dry_run": dry_run,
            "results": [r.__dict__ for r in results]}


class AlertRuleRequest(BaseModel):
    id: str
    metric_path: str  # "source.field", e.g. "analytics.revenue_usd"
    direction: str  # "pct_drop" | "pct_rise"
    threshold_pct: float
    label: str = ""


class SaveAlertConfigRequest(BaseModel):
    client_id: str
    rules: list[AlertRuleRequest]


@app.post("/api/alerts")
def save_alerts(req: SaveAlertConfigRequest, tenant_id: str = Depends(auth.get_tenant_id)):
    """B4 — per-client KPI alert thresholds. Breach detection itself runs
    inside scheduler.run_schedule (see alerts.check_alerts), reusing B1's
    period_diff deltas — this endpoint only persists which rules to check."""
    from . import alerts as alerts_mod
    try:
        rules = [alerts_mod.AlertRule(**r.model_dump()) for r in req.rules]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    config = alerts_mod.AlertConfig(tenant_id=tenant_id, client_id=req.client_id, rules=rules)
    alerts_mod.save_alert_config(config)
    return {"client_id": config.client_id, "rules": [r.__dict__ for r in config.rules]}


@app.get("/api/alerts/{client_id}")
def get_alerts(client_id: str, tenant_id: str = Depends(auth.get_tenant_id)):
    from . import alerts as alerts_mod
    config = alerts_mod.load_alert_config(tenant_id, client_id)
    if not config:
        return {"client_id": client_id, "rules": []}
    return {"client_id": config.client_id, "rules": [r.__dict__ for r in config.rules]}


@app.get("/api/health")
def health():
    import os
    from . import agent
    return {
        "status": "ok",
        "ai_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "ollama_available": agent._ollama_available(),
    }


@app.get("/api/templates")
def list_templates():
    """T4 — every non-hidden declarative template (app/template_specs/*.json),
    for the frontend's template picker. Adding a template is a JSON file, so
    this list grows with zero changes here."""
    return {"templates": template_specs.list_templates()}


@app.post("/api/generate-report")
async def generate(
    agency_name: str = Form(""),
    client_name: str = Form(""),
    primary_color: str = Form("#2a78d6"),
    accent_color: str = Form("#eda100"),
    logo_data_uri: str = Form(""),
    font_family: str = Form(""),
    footer_text: str = Form(""),
    signature_name: str = Form(""),
    signature_title: str = Form(""),
    disclaimer_text: str = Form(""),
    client_id: str | None = Form(None),
    template_id: str = Form("default"),
    analytics_file: UploadFile | None = File(None),
    seo_file: UploadFile | None = File(None),
    sales_file: UploadFile | None = File(None),
    tenant_id: str = Depends(auth.get_tenant_id),
):
    """Two ways to get data in: upload files (below), or pass client_id for a
    client that's already been onboarded to a live data source (see
    /api/data-sources/onboard) — same pipeline either way from this point on."""
    uploads = {}
    if analytics_file is not None and analytics_file.filename:
        uploads["analytics"] = (analytics_file.filename, io.BytesIO(await analytics_file.read()))
    if seo_file is not None and seo_file.filename:
        uploads["seo"] = (seo_file.filename, io.BytesIO(await seo_file.read()))
    if sales_file is not None and sales_file.filename:
        name = sales_file.filename
        buf = io.BytesIO(await sales_file.read())
        buf.name = name  # report_builder/parsers inspect .name to detect xlsx vs csv
        uploads["sales"] = (name, buf)

    if not uploads and not client_id:
        raise HTTPException(400, "Upload at least one file, or provide a client_id with a saved data source.")

    branding = {
        "agency_name": agency_name or "Your Agency",
        "client_name": client_name or "Client",
        "primary_color": primary_color,
        "accent_color": accent_color,
        "logo_data_uri": logo_data_uri or None,
        # W1 -- full white-label: every field optional, None means "use the
        # product default" at the template layer, never a forced override.
        "font_family": font_family or None,
        "footer_text": footer_text or None,
        "signature_name": signature_name or None,
        "signature_title": signature_title or None,
        "disclaimer_text": disclaimer_text or None,
    }

    # Files must be fully read before this request handler returns (the
    # UploadFile streams are tied to this request), so parsing/generation
    # itself runs in a background thread while we hand the client a job_id
    # to stream real progress against via /api/jobs/{job_id}/events.
    job_id = uuid.uuid4().hex[:12]
    _JOBS[job_id] = {"tenant_id": tenant_id, "stage": "Queued", "status": "running", "error": None}

    def run_job() -> None:
        try:
            def on_stage(label: str) -> None:
                _JOBS[job_id]["stage"] = label

            if client_id:
                result = report_builder.build_report_from_data_context(
                    tenant_id, client_id, branding, on_stage=on_stage, report_id=job_id, template_id=template_id
                )
            else:
                result = report_builder.build_report(
                    uploads, branding, on_stage=on_stage, report_id=job_id, template_id=template_id
                )
            # Disk is the source of truth; a poller that sees this job as
            # "done" must always be able to read it back -- so persist
            # BEFORE the in-memory cache goes live, never after (a cache
            # hit that races ahead of the disk write is a real, if narrow,
            # window otherwise).
            _persist_report(tenant_id, job_id, result, branding)
            _REPORTS[job_id] = {**result, "tenant_id": tenant_id}
            _JOBS[job_id]["stage"] = "Done"
            _JOBS[job_id]["status"] = "done"
        except ValueError as exc:
            _JOBS[job_id]["status"] = "error"
            _JOBS[job_id]["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            _JOBS[job_id]["status"] = "error"
            _JOBS[job_id]["error"] = f"Report generation failed: {exc}"

    threading.Thread(target=run_job, daemon=True).start()
    return {"job_id": job_id}


class GenerateFromInboxRequest(BaseModel):
    provider: str = "gmail"  # "gmail" | "outlook" (basic-auth-enabled accounts only, see email_source.py)
    mailbox: str = "INBOX"
    search: str = "UNSEEN"
    limit: int = 20
    mark_as_read: bool = False
    agency_name: str = "Your Agency"
    client_name: str = "Client"
    primary_color: str = "#2a78d6"
    accent_color: str = "#eda100"


@app.post("/api/generate-report/from-inbox")
def generate_from_inbox(req: GenerateFromInboxRequest, tenant_id: str = Depends(auth.get_tenant_id)):
    """Fetches CSV/Excel attachments straight from a mailbox and generates
    a report from them — same pipeline as /api/generate-report's file
    upload path, just sourced from email instead of a browser upload.
    Requires IMAP_USERNAME/IMAP_PASSWORD (an app password, not the account
    password) — see email_source.py's module docstring for Gmail/Outlook
    setup. 503, not a silent no-op, when those aren't configured."""
    from . import email_source

    connector = email_source.inbox_connector_from_env(req.provider)
    if connector is None:
        raise HTTPException(
            503,
            "IMAP_USERNAME/IMAP_PASSWORD are not configured — inbox fetching is not connected yet. "
            "See app/email_source.py's module docstring for setup.",
        )
    try:
        uploads, unmatched = email_source.build_uploads_from_inbox(
            connector, mailbox=req.mailbox, search=req.search, limit=req.limit, mark_as_read=req.mark_as_read,
        )
    finally:
        connector.close()

    if not uploads:
        return {
            "job_id": None,
            "message": "No matching attachments found in the inbox for this search.",
            "unmatched": [{"filename": a.filename, "from": a.message_from, "subject": a.message_subject} for a in unmatched],
        }

    branding = {
        "agency_name": req.agency_name, "client_name": req.client_name,
        "primary_color": req.primary_color, "accent_color": req.accent_color,
    }
    job_id = uuid.uuid4().hex[:12]
    _JOBS[job_id] = {"tenant_id": tenant_id, "stage": "Queued", "status": "running", "error": None}

    def run_job() -> None:
        try:
            def on_stage(label: str) -> None:
                _JOBS[job_id]["stage"] = label

            result = report_builder.build_report(uploads, branding, on_stage=on_stage, report_id=job_id)
            _persist_report(tenant_id, job_id, result, branding)
            _REPORTS[job_id] = {**result, "tenant_id": tenant_id}
            _JOBS[job_id]["stage"] = "Done"
            _JOBS[job_id]["status"] = "done"
        except ValueError as exc:
            _JOBS[job_id]["status"] = "error"
            _JOBS[job_id]["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            _JOBS[job_id]["status"] = "error"
            _JOBS[job_id]["error"] = f"Report generation failed: {exc}"

    threading.Thread(target=run_job, daemon=True).start()
    return {
        "job_id": job_id,
        "sources_matched": list(uploads.keys()),
        "unmatched": [{"filename": a.filename, "from": a.message_from, "subject": a.message_subject} for a in unmatched],
    }


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, tenant_id: str = Depends(auth.get_tenant_id)):
    """Server-Sent Events stream of real pipeline stages for one generation
    job — not a simulated timer, each event reflects an actual stage change
    in report_builder.build_report(). A job_id belonging to another tenant
    gets the exact same "job not found" frame as a genuinely nonexistent
    one -- never a real stage from someone else's run."""
    async def event_stream():
        last_sent = None
        while True:
            job = _JOBS.get(job_id)
            if not job or job.get("tenant_id") != tenant_id:
                yield f"data: {json.dumps({'status': 'error', 'error': 'job not found', 'stage': None})}\n\n"
                return
            snapshot = (job["stage"], job["status"])
            if snapshot != last_sent:
                last_sent = snapshot
                yield f"data: {json.dumps({'stage': job['stage'], 'status': job['status'], 'error': job.get('error')})}\n\n"
            if job["status"] in ("done", "error"):
                return
            await asyncio.sleep(0.2)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/reports")
def list_reports(tenant_id: str = Depends(auth.get_tenant_id)):
    """Every previously generated report for this tenant, newest first —
    read from disk so it survives backend restarts (e.g. --reload picking
    up a code change)."""
    return {"reports": report_store.list_reports_for_tenant(tenant_id)}


@app.get("/api/clients/{client_name}/reports")
def list_client_reports(client_name: str, tenant_id: str = Depends(auth.get_tenant_id)):
    """W2 — every report retained and listable per client, by period (the
    same on-disk data /api/reports reads, scoped to one client)."""
    return {"client_name": client_name, "reports": report_store.list_reports_for_client(tenant_id, client_name)}


@app.get("/api/reports/diff")
def diff_reports(report_id_a: str, report_id_b: str, tenant_id: str = Depends(auth.get_tenant_id)):
    """W2 — "what changed between July and August," rendered from B1's own
    diff_report_objects (the exact function scheduler.py's automatic
    current-vs-prior comparison already uses) — reused, not a second differ.
    report_id_a is treated as "current," report_id_b as "prior": pass the
    later period first if you want a positive delta to mean "grew"."""
    from . import period_diff
    obj_a = _load_report_object(tenant_id, report_id_a)
    obj_b = _load_report_object(tenant_id, report_id_b)
    return period_diff.diff_report_objects(obj_a, obj_b)


@app.get("/api/report/{report_id}")
def get_report(report_id: str, tenant_id: str = Depends(auth.get_tenant_id)):
    """`qa` is None for a report generated before the canonical object
    shipped (no report_object.json on disk for it) — an old report is still
    viewable, it just predates having a badge to show, which the frontend
    should treat as "unknown," not "failed"."""
    cached = _cached_report(report_id, tenant_id)
    if cached:
        return {
            "report_id": report_id,
            "report": cached["report"],
            "ai_generated": cached["report"].get("_ai_generated", False),
            "ai_provider": cached["report"].get("_ai_provider"),
            "ai_error": cached["report"].get("_ai_error"),
            "qa": cached.get("report_object").qa if cached.get("report_object") else None,
        }
    meta = _load_meta(tenant_id, report_id)
    if not meta:
        raise HTTPException(404, "Report not found")
    obj = report_store.load_report_object(tenant_id, report_id)
    return {
        "report_id": report_id,
        "report": meta["report"],
        "ai_generated": meta.get("ai_generated", False),
        "ai_provider": meta.get("ai_provider"),
        "ai_error": meta.get("ai_error"),
        "qa": obj.qa if obj else None,
    }


@app.get("/api/report/{report_id}/pdf")
def get_pdf(report_id: str, tenant_id: str = Depends(auth.get_tenant_id)):
    result = _cached_report(report_id, tenant_id)
    if result:
        pdf_bytes = result["pdf_bytes"]
    else:
        pdf_bytes = report_store.load_pdf_bytes(tenant_id, report_id)
        if pdf_bytes is None:
            raise HTTPException(404, "Report not found")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="report-{report_id}.pdf"'},
    )


@app.get("/api/report/{report_id}/html")
def get_html(report_id: str, tenant_id: str = Depends(auth.get_tenant_id)):
    result = _cached_report(report_id, tenant_id)
    if result:
        html = result["html"]
    else:
        html = report_store.load_html(tenant_id, report_id)
        if html is None:
            raise HTTPException(404, "Report not found")
    return Response(content=html, media_type="text/html")


def _load_report_object(tenant_id: str, report_id: str) -> ReportObject:
    """Same shared artifact every object-based renderer (dashboard, exports)
    reads from — the in-memory result if this process generated it,
    otherwise the persisted report_object. Never a second query path or a
    separate copy of the numbers."""
    result = _cached_report(report_id, tenant_id)
    if result and "report_object" in result:
        return result["report_object"]
    obj = report_store.load_report_object(tenant_id, report_id)
    if obj is None:
        raise HTTPException(
            404,
            "Report not found, or generated before the canonical report object shipped "
            "(no report_object stored for this report_id).",
        )
    return obj


@app.get("/api/report/{report_id}/dashboard")
def get_dashboard(report_id: str, tenant_id: str = Depends(auth.get_tenant_id)):
    html = html_dashboard.build_dashboard(_load_report_object(tenant_id, report_id))
    return Response(content=html, media_type="text/html")


@app.get("/api/report/{report_id}/export/{fmt}")
def get_export(report_id: str, fmt: str, tenant_id: str = Depends(auth.get_tenant_id), db: Session = Depends(get_db)):
    if fmt == "pbip":
        from . import plans
        from .models import Tenant

        tenant = db.get(Tenant, tenant_id)
        plan = plans.get_plan(tenant.plan)
        if not plan.can_export_pbip:
            raise HTTPException(402, f"Power BI export isn't included in the {plan.label} plan. Upgrade to Agency or higher.")

    obj = _load_report_object(tenant_id, report_id)
    results = export_report(obj, formats=[fmt])
    result = results.get(fmt)
    if result is None:
        raise HTTPException(400, f"Unknown export format {fmt!r}. Choose one of: pptx, email_html, google_slides, pbip.")
    if result.status != "ok":
        raise HTTPException(503, result.reason or f"{fmt} export is not available.")
    extension = {"pptx": "pptx", "email_html": "html", "pbip": "zip"}.get(fmt, fmt)
    return Response(
        content=result.content,
        media_type=result.content_type,
        headers={"Content-Disposition": f'attachment; filename="report-{report_id}.{extension}"'},
    )
