"""
Track B2 — scheduler: per-client cadence that regenerates reports with no
one running it by hand.

Reuses Lever-1's saved warehouse connection + column mapping verbatim --
running a schedule is exactly report_builder.build_report_from_data_context
(client_id, branding), the same call the manual "generate now" path makes.
A schedule never re-asks for or re-derives a schema mapping; data_context.py
already owns that, onboarded once (see data_context.py's own docstring).

Idempotency: "re-running the same as-of date reproduces byte-identical
output" can't mean "call the LLM again and get the same narrative" -- two
independent runs of the exact same pipeline were measured (see F0's
CHANGELOG entry) to differ by ~1KB in the rendered PDF from narrative
variance alone. So idempotency here is a cache/key guarantee, not a
determinism claim about the LLM: a schedule keyed by (client_id, as_of)
that already has a persisted report for that exact key returns the
existing report_id and generates nothing new. Re-running is a genuine
no-op, not "regenerate and hope it comes out the same."

Schedule persistence (save_schedule/load_schedule/etc.) is backed by the
`schedules` table (see app/store_models.py), each function opening and
committing its own short-lived DB session internally rather than taking a
`db: Session` parameter. This is necessary, not just simpler: two real call
paths here have no FastAPI request to draw a `Depends(get_db)` session
from at all -- main.py's run_job() (report generation on a background
thread, fire-and-forget from the request handler) and this module's own
_run_loop (the autonomous scheduler's daemon thread, started once at
startup, running forever with no request in flight). app/db.py's engine
already sets `check_same_thread=False` for exactly this reason.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone

from . import data_context, period_diff, report_builder, report_store
from . import db as db_mod
from .store_models import ScheduleRecord

CADENCES = ("daily", "weekly", "monthly")


@dataclass
class Schedule:
    #: Track E1 — which agency workspace owns this schedule. Required, no
    #: default: a forgotten tenant_id should be a TypeError at the call
    #: site, never a silent shared bucket.
    tenant_id: str
    client_id: str
    #: References data_context.py's saved connection for this same
    #: (tenant_id, client_id) -- not a second data-source registration.
    data_source_ref: str
    cadence: str  # "daily" | "weekly" | "monthly"
    branding: dict = field(default_factory=dict)
    created_at: str = ""
    #: as_of dates this schedule has already produced a report for,
    #: mapped to that report's id -- the idempotency ledger.
    runs: dict[str, str] = field(default_factory=dict)
    #: B3 — who a freshly-generated report goes to (client_recipients), and
    #: who a FAILing-badge report gets redirected to instead
    #: (consultant_recipients) -- see delivery.py. Empty means "don't
    #: auto-deliver," not "deliver to no one silently."
    client_recipients: list[str] = field(default_factory=list)
    consultant_recipients: list[str] = field(default_factory=list)
    delivery_channel: str = "email"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Schedule":
        return Schedule(
            tenant_id=d["tenant_id"],
            client_id=d["client_id"], data_source_ref=d["data_source_ref"], cadence=d["cadence"],
            branding=d.get("branding", {}), created_at=d.get("created_at", ""), runs=d.get("runs", {}),
            client_recipients=d.get("client_recipients", []),
            consultant_recipients=d.get("consultant_recipients", []),
            delivery_channel=d.get("delivery_channel", "email"),
        )


def _schedule_from_row(row: ScheduleRecord) -> Schedule:
    return Schedule(
        tenant_id=row.tenant_id, client_id=row.client_id, data_source_ref=row.data_source_ref,
        cadence=row.cadence, branding=row.branding, created_at=row.created_at, runs=row.runs,
        client_recipients=row.client_recipients, consultant_recipients=row.consultant_recipients,
        delivery_channel=row.delivery_channel,
    )


def save_schedule(schedule: Schedule) -> None:
    if schedule.cadence not in CADENCES:
        raise ValueError(f"cadence must be one of {CADENCES}, got {schedule.cadence!r}")
    if not data_context.load_data_context(schedule.tenant_id, schedule.data_source_ref):
        raise ValueError(
            f"No data context saved for data_source_ref={schedule.data_source_ref!r} — "
            "onboard the client's warehouse connection first (data_context.py)."
        )
    if not schedule.created_at:
        schedule.created_at = datetime.now(timezone.utc).isoformat()

    with db_mod.SessionLocal() as session:
        row = ScheduleRecord(
            tenant_id=schedule.tenant_id, client_id=schedule.client_id,
            data_source_ref=schedule.data_source_ref, cadence=schedule.cadence,
            branding=schedule.branding, created_at=schedule.created_at, runs=schedule.runs,
            client_recipients=schedule.client_recipients,
            consultant_recipients=schedule.consultant_recipients, delivery_channel=schedule.delivery_channel,
        )
        session.merge(row)
        session.commit()


def load_schedule(tenant_id: str, client_id: str) -> Schedule | None:
    with db_mod.SessionLocal() as session:
        row = session.query(ScheduleRecord).filter_by(tenant_id=tenant_id, client_id=client_id).one_or_none()
        return _schedule_from_row(row) if row else None


def delete_schedule(tenant_id: str, client_id: str) -> bool:
    """False (not a raised error) when there was nothing to delete -- same
    honest-missing-state posture as data_context.delete_data_context. Ends
    this client's "active client" status (see count_active_clients_for_tenant)
    but does not touch the data context itself -- a caller that wants the
    connection gone too calls data_context.delete_data_context separately
    (see auth.py's admin_remove_client, which does both)."""
    with db_mod.SessionLocal() as session:
        row = session.query(ScheduleRecord).filter_by(tenant_id=tenant_id, client_id=client_id).one_or_none()
        if row is None:
            return False
        session.delete(row)
        session.commit()
        return True


def list_schedules_for_tenant(tenant_id: str) -> list[Schedule]:
    """User-session-authenticated callers use THIS, never list_all_schedules
    -- including a session-authenticated call to POST /api/schedules/run
    itself, which must only ever touch the caller's own tenant (see
    list_all_schedules's docstring for the privilege-escalation this split
    exists to prevent)."""
    with db_mod.SessionLocal() as session:
        rows = (
            session.query(ScheduleRecord)
            .filter_by(tenant_id=tenant_id)
            .order_by(ScheduleRecord.client_id)
            .all()
        )
        return [_schedule_from_row(r) for r in rows]


def count_active_clients_for_tenant(tenant_id: str) -> int:
    """Plan enforcement (see app/plans.py's module docstring for why a
    schedule -- not a one-off upload -- is what "active client" means
    here). One schedule file per (tenant_id, client_id) by construction
    (see _path), so this is just the tenant's schedule count -- a named
    function of its own so a caller checking a plan limit doesn't need to
    know that detail."""
    return len(list_schedules_for_tenant(tenant_id))


def list_all_schedules() -> list[Schedule]:
    """Infra-only: every schedule across every tenant. Used EXCLUSIVELY by
    the cron/service-token path (POST /api/schedules/run with a valid
    SCHEDULER_SERVICE_TOKEN, no human session) and the autonomous background
    loop -- both legitimately need to fire every tenant's due schedules.
    A normal user-session-authenticated request must never reach this
    function; it would let Tenant A trigger report generation/delivery
    (LLM calls, warehouse queries, real client emails) for every OTHER
    tenant's schedules too. See main.py's /api/schedules/run for the
    enforced split."""
    with db_mod.SessionLocal() as session:
        rows = session.query(ScheduleRecord).order_by(ScheduleRecord.tenant_id, ScheduleRecord.client_id).all()
        return [_schedule_from_row(r) for r in rows]


def is_due(schedule: Schedule, as_of: date) -> bool:
    """Deterministic, stated cadence rule -- no cron-string parsing needed
    for three fixed cadences: daily fires every day; weekly fires Mondays;
    monthly fires on the 1st. A schedule that's due but was already run for
    this as_of (see run_schedule's idempotency) is still "due" -- due-ness
    and idempotency are separate questions on purpose, so a dry run can
    honestly report "yes, this would fire" even when it would also reuse."""
    if schedule.cadence == "daily":
        return True
    if schedule.cadence == "weekly":
        return as_of.isoweekday() == 1  # Monday
    if schedule.cadence == "monthly":
        return as_of.day == 1
    raise ValueError(f"unknown cadence {schedule.cadence!r}")


@dataclass
class RunResult:
    tenant_id: str
    client_id: str
    as_of: str
    report_id: str | None
    status: str  # "generated" | "reused" | "dry_run" | "error" | "regenerated"
    detail: str = ""
    #: B4 — how many KPI-alert rules breached on this run (0 for "reused"/
    #: "dry_run"/"error", or a client with no rules configured).
    alerts_fired: int = 0


#: Backward-compatible alias -- the real constant now lives in period_diff.py
#: (W2 reuses it for on-demand report-to-report diffing too).
_DIFFABLE_SOURCES = period_diff.DIFFABLE_SOURCES


def _prior_report_id(schedule: Schedule, before_as_of: str) -> str | None:
    """The most recent report this schedule produced strictly before the
    given as_of date, by date order -- None for the first-ever run, when
    there's nothing to diff against yet."""
    earlier = sorted(d for d in schedule.runs if d < before_as_of)
    return schedule.runs[earlier[-1]] if earlier else None


def _attach_period_comparison(obj, schedule: Schedule, as_of_str: str) -> bool:
    """B1 -> B2: if this schedule has a prior report, diff this one against
    it (via period_diff.diff_report_objects -- the same core W2's on-demand
    report-to-report diff uses, not a second implementation) and attach the
    result to obj.period_comparison. Returns True if a comparison was
    attached (so the caller knows whether a re-persist is actually needed)
    -- False for a first-ever run, or if the prior report's object can't be
    loaded."""
    prior_report_id = _prior_report_id(schedule, as_of_str)
    if prior_report_id is None:
        return False
    prior_obj = report_store.load_report_object(schedule.tenant_id, prior_report_id)
    if prior_obj is None:
        return False

    comparison = period_diff.diff_report_objects(obj, prior_obj)

    obj.period_comparison = comparison
    return True


def run_schedule(schedule: Schedule, as_of: date, dry_run: bool = False, deliver: bool = False) -> RunResult:
    """Runs (or simulates) one cadence firing for a fixed as_of date.

    Idempotent: if this schedule already has a report for this exact
    as_of, that report_id is returned and nothing is regenerated --
    "reused", not "generated". dry_run=True never calls the pipeline or
    touches disk at all, regardless of whether a prior run exists; it only
    reports what WOULD happen.

    deliver=True (default off, so existing callers/tests are unaffected)
    hands a freshly-GENERATED report to delivery.deliver_report using this
    schedule's recipients -- never for a "reused" result, since that's the
    same report a prior run would already have delivered; re-delivering it
    on every idempotent re-run would spam the client for a no-op.
    """
    as_of_str = as_of.isoformat()
    existing_report_id = schedule.runs.get(as_of_str)

    if dry_run:
        if existing_report_id:
            return RunResult(schedule.tenant_id, schedule.client_id, as_of_str, existing_report_id, "dry_run",
                              detail="a report for this as_of already exists; a real run would reuse it")
        return RunResult(schedule.tenant_id, schedule.client_id, as_of_str, None, "dry_run",
                          detail="a real run would generate a new report")

    if existing_report_id and report_store.report_exists(schedule.tenant_id, existing_report_id):
        return RunResult(schedule.tenant_id, schedule.client_id, as_of_str, existing_report_id, "reused",
                          detail="idempotent: this schedule already produced a report for this as_of date")

    report_id = f"{schedule.client_id}-{as_of_str}-{uuid.uuid4().hex[:8]}"
    try:
        result = report_builder.build_report_from_data_context(
            schedule.tenant_id, schedule.data_source_ref, schedule.branding, report_id=report_id,
        )
    except Exception as exc:  # noqa: BLE001
        return RunResult(schedule.tenant_id, schedule.client_id, as_of_str, None, "error", detail=str(exc))

    report_store.persist_report(schedule.tenant_id, report_id, result, schedule.branding)

    # B1 -> B2: diff against this schedule's most recent prior report, if
    # one exists, and re-persist with the comparison attached. Must happen
    # after the first persist (report_store.load_report_object needs the
    # *prior* report_id's file to already exist on disk) but before
    # schedule.runs is updated with today's id (irrelevant to correctness
    # here since _prior_report_id excludes today's own date either way, but
    # keeps "prior" meaning what it says at the point this call is made).
    if _attach_period_comparison(result["report_object"], schedule, as_of_str):
        report_store.persist_report(schedule.tenant_id, report_id, result, schedule.branding)

    schedule.runs[as_of_str] = report_id
    save_schedule(schedule)

    # B4: threshold breaches, checked against the SAME period_comparison
    # deltas just attached above -- never a second recompute. Gated on this
    # client having rules configured at all (most won't), and dedup'd
    # per-as_of inside check_alerts so a re-run never re-fires.
    from . import alerts as alerts_mod
    fired = alerts_mod.check_alerts(result["report_object"], schedule.tenant_id, schedule.client_id, as_of_str)

    detail_parts = []
    if deliver and schedule.client_recipients:
        from . import delivery
        attempt = delivery.deliver_report(
            schedule.tenant_id, result["report_object"], schedule.client_recipients, channel=schedule.delivery_channel,
            consultant_recipients=schedule.consultant_recipients,
        )
        detail_parts.append(
            f"delivery: {attempt.status} ({attempt.reason})" if attempt.reason else f"delivery: {attempt.status}"
        )
    if fired:
        detail_parts.append(f"{len(fired)} KPI alert(s) fired")
        if deliver and schedule.client_recipients:
            alerts_mod.deliver_alerts(fired, schedule.client_recipients, schedule.tenant_id, schedule.client_id,
                                       channel=schedule.delivery_channel)

    return RunResult(schedule.tenant_id, schedule.client_id, as_of_str, report_id, "generated",
                      detail="; ".join(detail_parts), alerts_fired=len(fired))


def regenerate_run(schedule: Schedule, as_of: date) -> RunResult:
    """T3 — force a genuine regeneration of an already-generated period,
    pinned to the EXACT template version that produced it (read off the old
    report's own ReportObject.template_version), not whatever's latest on
    disk by the time this runs. Unlike run_schedule's idempotent reuse (which
    never regenerates at all, so template drift can't touch it), this is for
    the case where a regeneration is genuinely wanted -- e.g. a bug fix in
    the pipeline -- without letting an unrelated template bump silently
    change what an already-delivered period looks like.

    Never mutates or deletes the old report_id's files (every generated
    report stays retained, W2) -- this supersedes schedule.runs[as_of] to
    point at the new one, leaving the old one on disk for history/diffing."""
    as_of_str = as_of.isoformat()
    old_report_id = schedule.runs.get(as_of_str)
    if not old_report_id:
        return RunResult(schedule.tenant_id, schedule.client_id, as_of_str, None, "error",
                          detail=f"no existing report for {as_of_str} to regenerate")

    old_obj = report_store.load_report_object(schedule.tenant_id, old_report_id)
    if old_obj is None:
        return RunResult(schedule.tenant_id, schedule.client_id, as_of_str, None, "error",
                          detail=f"report {old_report_id} has no stored report_object to pin a template version from")

    report_id = f"{schedule.client_id}-{as_of_str}-{uuid.uuid4().hex[:8]}"
    try:
        result = report_builder.build_report_from_data_context(
            schedule.tenant_id, schedule.data_source_ref, schedule.branding, report_id=report_id,
            template_id=old_obj.template_id, template_version=old_obj.template_version,
        )
    except Exception as exc:  # noqa: BLE001
        return RunResult(schedule.tenant_id, schedule.client_id, as_of_str, None, "error", detail=str(exc))

    report_store.persist_report(schedule.tenant_id, report_id, result, schedule.branding)
    if _attach_period_comparison(result["report_object"], schedule, as_of_str):
        report_store.persist_report(schedule.tenant_id, report_id, result, schedule.branding)

    schedule.runs[as_of_str] = report_id
    save_schedule(schedule)
    return RunResult(schedule.tenant_id, schedule.client_id, as_of_str, report_id, "regenerated",
                      detail=f"pinned to template {old_obj.template_id!r} v{old_obj.template_version} "
                             f"(superseded {old_report_id})")


def run_due_schedules(as_of: date, dry_run: bool = False, deliver: bool = False,
                       tenant_id: str | None = None) -> list[RunResult]:
    """The actual "no one has to run it" entry point: every saved schedule
    whose cadence says this as_of date is a firing day (is_due), and only
    those -- a monthly schedule doesn't regenerate on a Tuesday just
    because someone called this function then. Schedules not due today
    are reported as "not_due", not silently omitted, so a dry run's output
    accounts for every schedule that exists.

    tenant_id: Track E1's service-token-vs-session privilege boundary lives
    here, not just in main.py's route. tenant_id=None (the default) means
    "every tenant" -- correct for the autonomous background loop and a
    valid-service-token call, both infra paths with no human session to
    scope to. A session-authenticated caller (main.py's POST
    /api/schedules/run without a service token) MUST pass its own
    tenant_id explicitly; this is not a forgotten-argument footgun the way
    the store-layer's required tenant_id is, because the two callers
    genuinely want different scopes on purpose."""
    results = []
    schedules = list_all_schedules() if tenant_id is None else list_schedules_for_tenant(tenant_id)
    for schedule in schedules:
        if not is_due(schedule, as_of):
            results.append(RunResult(schedule.tenant_id, schedule.client_id, as_of.isoformat(), None, "not_due",
                                      detail=f"cadence={schedule.cadence!r} does not fire on {as_of.isoformat()}"))
            continue
        results.append(run_schedule(schedule, as_of, dry_run=dry_run, deliver=deliver))
    return results


# ---------------------------------------------------------------------------
# Autonomous background loop -- the actual "no one has to run it" mechanism.
# Everything above this line can correctly answer "what's due" and "run it,"
# but nothing calls that on a timer without this: previously the only way to
# fire run_due_schedules() was a human or an external cron hitting
# POST /api/schedules/run by hand. Opt-in (main.py only starts this when
# AUTO_SCHEDULER_ENABLED is set) -- an app starting up should not silently
# begin generating reports (LLM calls, warehouse queries) for every saved
# schedule just because someone ran it locally to poke at the API.
# ---------------------------------------------------------------------------

def _run_loop(interval_seconds: int, deliver: bool, stop_event) -> None:
    import logging
    import time

    logger = logging.getLogger("reportpilot.scheduler")
    logger.info("Autonomous scheduler loop started (interval=%ss, deliver=%s)", interval_seconds, deliver)
    while not stop_event.is_set():
        try:
            results = run_due_schedules(date.today(), dry_run=False, deliver=deliver)
            fired = [r for r in results if r.status != "not_due"]
            if fired:
                logger.info("Autonomous run: %s", [(r.client_id, r.status) for r in fired])
        except Exception:  # noqa: BLE001 -- one bad cycle must never kill the loop
            logger.exception("Autonomous scheduler cycle failed; will retry next interval")
        stop_event.wait(interval_seconds)
    logger.info("Autonomous scheduler loop stopped")


def start_background_loop(interval_seconds: int = 3600, deliver: bool = False):
    """Starts a daemon thread that calls run_due_schedules() once per
    interval (default hourly -- fine-grained enough for daily/weekly/
    monthly cadences without polling absurdly often), forever, until the
    process exits or the returned stop_event is set. Runs one cycle
    immediately on start (not after the first full interval), so a schedule
    that's already due doesn't sit waiting for up to an hour after a
    restart.

    Returns (thread, stop_event) -- call stop_event.set() to shut it down
    cleanly (used by tests; a real process just exits and the daemon thread
    goes with it)."""
    import threading

    stop_event = threading.Event()
    thread = threading.Thread(
        target=_run_loop, args=(interval_seconds, deliver, stop_event), daemon=True,
        name="reportpilot-scheduler-loop",
    )
    thread.start()
    return thread, stop_event
