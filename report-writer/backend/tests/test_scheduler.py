"""
Tests for app/scheduler.py (Track B2 — scheduler).

Uses a real SQLite database (via connectors/sqlite_connector.py, the one
connector this project actually exercises end-to-end without live
credentials — see its own docstring) registered through data_context.py's
real onboarding path, exactly like a client's warehouse connection would
be. Only one test drives the full pipeline (a real report generation, which
calls the LLM narrative path and is slow) -- everything else tests
scheduling/persistence logic directly against hand-built Schedule objects.
"""
import sqlite3
from datetime import date

import pytest

from app import data_context, delivery, report_store, scheduler

ANALYTICS_ROWS = [
    ("2026-01-01", "Organic Search", "desktop", 100, 80, 5, 500.0),
    ("2026-01-08", "Paid Search", "mobile", 150, 90, 8, 800.0),
    ("2026-01-15", "Organic Search", "desktop", 200, 130, 12, 1200.0),
    ("2026-01-22", "Paid Search", "desktop", 160, 95, 7, 700.0),
]


TENANT = "t1"


def _make_sqlite_client(tmp_path, tenant_id: str, client_id: str) -> None:
    db_path = tmp_path / f"{client_id}.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE analytics (date TEXT, channel_group TEXT, device_category TEXT, "
        "sessions INTEGER, new_users INTEGER, conversions INTEGER, revenue_usd REAL)"
    )
    conn.executemany("INSERT INTO analytics VALUES (?, ?, ?, ?, ?, ?, ?)", ANALYTICS_ROWS)
    conn.commit()
    conn.close()

    fields = ["date", "channel_group", "device_category", "sessions", "new_users", "conversions", "revenue_usd"]
    data_context.save_data_context(
        tenant_id, client_id, "sqlite", {"path": str(db_path)},
        {"analytics": {"table": "analytics", "column_map": {f: f for f in fields}}},
    )


@pytest.fixture
def client_id(tmp_path, db_session):
    cid = "sched-test-client"
    _make_sqlite_client(tmp_path, TENANT, cid)
    return cid


# ---------------------------------------------------------------------------
# Persistence: per-client schedule + template + data-source ref, reloadable
# ---------------------------------------------------------------------------

def test_save_and_load_schedule_round_trips(client_id):
    sched = scheduler.Schedule(
        tenant_id=TENANT, client_id=client_id, data_source_ref=client_id, cadence="weekly",
        branding={"agency_name": "A", "client_name": "B"},
    )
    scheduler.save_schedule(sched)

    loaded = scheduler.load_schedule(TENANT, client_id)
    assert loaded.client_id == client_id
    assert loaded.data_source_ref == client_id
    assert loaded.cadence == "weekly"
    assert loaded.branding == {"agency_name": "A", "client_name": "B"}
    assert loaded.created_at  # stamped automatically


def test_save_schedule_rejects_unknown_data_source_ref(client_id):
    sched = scheduler.Schedule(tenant_id=TENANT, client_id="x", data_source_ref="never-onboarded", cadence="daily")
    with pytest.raises(ValueError, match="No data context saved"):
        scheduler.save_schedule(sched)


def test_save_schedule_rejects_invalid_cadence(client_id):
    sched = scheduler.Schedule(tenant_id=TENANT, client_id=client_id, data_source_ref=client_id, cadence="hourly")
    with pytest.raises(ValueError, match="cadence must be one of"):
        scheduler.save_schedule(sched)


def test_list_schedules_returns_every_saved_schedule(client_id):
    scheduler.save_schedule(scheduler.Schedule(tenant_id=TENANT, client_id=client_id, data_source_ref=client_id, cadence="daily"))
    schedules = scheduler.list_schedules_for_tenant(TENANT)
    assert [s.client_id for s in schedules] == [client_id]


# ---------------------------------------------------------------------------
# Cadence: deterministic due-date rule
# ---------------------------------------------------------------------------

def test_daily_cadence_is_always_due():
    sched = scheduler.Schedule(tenant_id=TENANT, client_id="c", data_source_ref="c", cadence="daily")
    assert scheduler.is_due(sched, date(2026, 3, 4))  # a Wednesday
    assert scheduler.is_due(sched, date(2026, 3, 9))  # a Monday


def test_weekly_cadence_is_due_only_on_monday():
    sched = scheduler.Schedule(tenant_id=TENANT, client_id="c", data_source_ref="c", cadence="weekly")
    assert scheduler.is_due(sched, date(2026, 3, 9))       # Monday
    assert not scheduler.is_due(sched, date(2026, 3, 10))  # Tuesday


def test_monthly_cadence_is_due_only_on_the_first():
    sched = scheduler.Schedule(tenant_id=TENANT, client_id="c", data_source_ref="c", cadence="monthly")
    assert scheduler.is_due(sched, date(2026, 3, 1))
    assert not scheduler.is_due(sched, date(2026, 3, 2))


# ---------------------------------------------------------------------------
# Dry-run: no side effects, for the whole cadence, on a fixed date
# ---------------------------------------------------------------------------

def test_dry_run_creates_no_report_and_does_not_touch_the_schedule_file(client_id):
    sched = scheduler.Schedule(tenant_id=TENANT, client_id=client_id, data_source_ref=client_id, cadence="daily")
    scheduler.save_schedule(sched)
    before = scheduler.load_schedule(TENANT, client_id).runs.copy()

    result = scheduler.run_schedule(sched, date(2026, 3, 4), dry_run=True)

    assert result.status == "dry_run"
    assert result.report_id is None
    assert scheduler.load_schedule(TENANT, client_id).runs == before  # untouched on disk
    assert report_store.list_reports_for_tenant(TENANT) == []  # nothing generated


def test_run_due_schedules_dry_run_reports_not_due_for_off_cadence_schedules(client_id):
    scheduler.save_schedule(scheduler.Schedule(tenant_id=TENANT, client_id=client_id, data_source_ref=client_id, cadence="monthly"))
    results = scheduler.run_due_schedules(date(2026, 3, 15), dry_run=True, tenant_id=TENANT)  # not the 1st
    assert len(results) == 1
    assert results[0].status == "not_due"
    assert report_store.list_reports_for_tenant(TENANT) == []


# ---------------------------------------------------------------------------
# Real generation + idempotency -- the one slow, end-to-end test. Confirms
# a second run for the SAME as_of date reuses the same report_id rather
# than generating (and does not touch the LLM/pipeline a second time).
# ---------------------------------------------------------------------------

def test_run_schedule_generates_once_then_reuses_for_the_same_as_of(client_id):
    sched = scheduler.Schedule(
        tenant_id=TENANT, client_id=client_id, data_source_ref=client_id, cadence="daily",
        branding={"agency_name": "Test Agency", "client_name": "Test Client"},
    )
    scheduler.save_schedule(sched)
    as_of = date(2026, 3, 4)

    first = scheduler.run_schedule(sched, as_of, dry_run=False)
    assert first.status == "generated"
    assert first.report_id is not None
    assert report_store.report_exists(TENANT, first.report_id)

    reloaded = scheduler.load_schedule(TENANT, client_id)
    assert reloaded.runs[as_of.isoformat()] == first.report_id

    second = scheduler.run_schedule(reloaded, as_of, dry_run=False)
    assert second.status == "reused"
    assert second.report_id == first.report_id

    # A different as_of date is a genuinely new report, not reused.
    third = scheduler.run_schedule(reloaded, date(2026, 3, 5), dry_run=False)
    assert third.status == "generated"
    assert third.report_id != first.report_id

    # B1 -> B2: this schedule now has a prior report (the first one), so
    # the third report should carry a real period_comparison against it --
    # period_diff.py actually feeding a generated report, not just tested
    # in isolation.
    third_obj = report_store.load_report_object(TENANT, third.report_id)
    assert third_obj.period_comparison is not None
    assert third_obj.period_comparison["prior_report_id"] == first.report_id
    assert "analytics" in third_obj.period_comparison

    # The very first report for a schedule has no prior to diff against.
    first_obj = report_store.load_report_object(TENANT, first.report_id)
    assert first_obj.period_comparison is None


# ---------------------------------------------------------------------------
# B2 -> B3 integration: a schedule with recipients configured hands a
# freshly-generated report to delivery.deliver_report. No fake channel is
# injected here on purpose -- the real EmailChannel correctly reporting
# "unavailable" (no SMTP_HOST in this environment) is the honest, correct
# outcome to verify, the same way D1's Google Slides export is tested.
# ---------------------------------------------------------------------------

def test_run_schedule_with_deliver_true_attempts_delivery_for_a_fresh_generation(client_id, monkeypatch):
    """Delivery is genuinely attempted for a fresh generation with
    recipients configured -- verified via the actual log entry, not the
    RunResult.detail string. Which *outcome* it lands on (unavailable, since
    no SMTP_HOST is configured here; or blocked, if this run's real LLM
    narrative happens to fail QA on this tiny synthetic fixture) is
    real-pipeline-dependent and not something to assert a specific one of —
    both are honest, both prove the QA-gate/delivery wiring actually fired."""
    monkeypatch.delenv("SMTP_HOST", raising=False)
    sched = scheduler.Schedule(
        tenant_id=TENANT, client_id=client_id, data_source_ref=client_id, cadence="daily",
        branding={"agency_name": "Test Agency", "client_name": "Test Client"},
        client_recipients=["client@example.com"],
    )
    scheduler.save_schedule(sched)

    result = scheduler.run_schedule(sched, date(2026, 4, 1), dry_run=False, deliver=True)

    assert result.status == "generated"
    assert result.detail.startswith("delivery:")
    log = delivery.list_delivery_log(TENANT, result.report_id)
    assert len(log) == 1
    assert log[0].status in ("unavailable", "blocked")


# ---------------------------------------------------------------------------
# Autonomous background loop -- the actual "no one has to run it" mechanism
# ---------------------------------------------------------------------------

def test_background_loop_fires_immediately_without_waiting_a_full_interval(client_id):
    """A daily schedule is always due -- the loop's first cycle runs
    immediately on start, not after the first interval, so this generates
    a real report within a couple seconds rather than needing to wait out
    a full (real-world-sized) interval in a test."""
    sched = scheduler.Schedule(tenant_id=TENANT, client_id=client_id, data_source_ref=client_id, cadence="daily")
    scheduler.save_schedule(sched)

    thread, stop_event = scheduler.start_background_loop(interval_seconds=3600, deliver=False)
    try:
        import time
        # Polling, not a fixed sleep -- real LLM narrative calls in this
        # environment have been observed taking anywhere from ~15s in
        # isolation to well over a minute when the full suite runs many
        # sequential real generations and contends for the same local
        # Ollama process (confirmed: a full test_scheduler.py run averaged
        # well over 100s per generation under that contention). Budgeted
        # generously rather than risk a flaky timeout under load.
        for _ in range(300):
            reloaded = scheduler.load_schedule(TENANT, client_id)
            if reloaded.runs:
                break
            time.sleep(1)
        else:
            pytest.fail("background loop did not generate a report within the timeout")

        reloaded = scheduler.load_schedule(TENANT, client_id)
        assert len(reloaded.runs) == 1
        report_id = next(iter(reloaded.runs.values()))
        assert report_store.report_exists(TENANT, report_id)
    finally:
        stop_event.set()
        thread.join(timeout=5)


def test_background_loop_survives_a_failing_cycle(client_id, monkeypatch):
    """One bad cycle (e.g. a transient warehouse/LLM error) must not kill
    the loop -- it should log and retry on the next interval, not die
    silently and leave scheduling looking "on" when it's actually stopped."""
    calls = []

    def flaky_run_due_schedules(as_of, dry_run=False, deliver=False):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("simulated transient failure")
        return []

    monkeypatch.setattr(scheduler, "run_due_schedules", flaky_run_due_schedules)

    thread, stop_event = scheduler.start_background_loop(interval_seconds=0.05, deliver=False)
    try:
        import time
        time.sleep(0.5)
        assert len(calls) >= 2  # survived the first (raising) call and ran again
        assert thread.is_alive()
    finally:
        stop_event.set()
        thread.join(timeout=5)


def test_background_loop_stops_cleanly_when_stop_event_is_set(client_id):
    thread, stop_event = scheduler.start_background_loop(interval_seconds=3600, deliver=False)
    assert thread.is_alive()
    stop_event.set()
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_run_schedule_with_deliver_true_but_no_recipients_does_not_attempt_delivery(client_id):
    sched = scheduler.Schedule(
        tenant_id=TENANT, client_id=client_id, data_source_ref=client_id, cadence="daily",
        branding={"agency_name": "Test Agency", "client_name": "Test Client"},
        # client_recipients deliberately left empty
    )
    scheduler.save_schedule(sched)

    result = scheduler.run_schedule(sched, date(2026, 4, 2), dry_run=False, deliver=True)

    assert result.status == "generated"
    assert result.detail == ""  # never attempted -- nowhere to send to
    assert delivery.list_delivery_log(TENANT, result.report_id) == []


def test_run_schedule_deliver_defaults_to_off(client_id):
    """deliver=False by default -- a caller upgrading to a deliver-capable
    scheduler doesn't suddenly start emailing clients without opting in."""
    sched = scheduler.Schedule(
        tenant_id=TENANT, client_id=client_id, data_source_ref=client_id, cadence="daily",
        client_recipients=["client@example.com"],
    )
    scheduler.save_schedule(sched)

    result = scheduler.run_schedule(sched, date(2026, 4, 3), dry_run=False)  # no deliver kwarg
    assert delivery.list_delivery_log(TENANT, result.report_id) == []


# ---------------------------------------------------------------------------
# B1 -> B2 diff-attachment logic, tested directly against hand-built
# ReportObjects -- fast, no real report generation needed for the wiring
# logic itself (the real end-to-end version lives in the idempotency test
# above, which already does two real generations for the same schedule).
# ---------------------------------------------------------------------------

from app.report_object import ChartRef, Period, ReportObject, SourceInfo  # noqa: E402


def _fake_report_object(report_id: str, period_label: str, revenue: float, sessions: int) -> ReportObject:
    return ReportObject(
        report_id=report_id,
        period=Period(label=period_label),
        sources={},
        metrics={
            "analytics": {"totals": {"sessions": sessions, "revenue_usd": revenue}},
            "sales": {"totals": {"revenue_usd": revenue * 2}},
        },
        series={}, charts=[],
        narrative={"report_title": "t", "period_label": period_label, "executive_summary": "",
                   "highlights": [], "watchouts": [], "sections": [], "next_steps": []},
        qa={"badge": "PASS", "failing_checks": []},
        branding={}, section_order=["analytics", "sales"],
    )


@pytest.fixture
def isolated_report_store(db_session):
    return db_session


def test_prior_report_id_is_none_for_the_first_run():
    sched = scheduler.Schedule(tenant_id=TENANT, client_id="c", data_source_ref="c", cadence="daily", runs={})
    assert scheduler._prior_report_id(sched, "2026-03-01") is None


def test_prior_report_id_finds_the_most_recent_earlier_run():
    sched = scheduler.Schedule(
        tenant_id=TENANT, client_id="c", data_source_ref="c", cadence="daily",
        runs={"2026-01-01": "report-jan", "2026-02-01": "report-feb", "2026-03-01": "report-mar"},
    )
    # Looking for the prior report as of April -- most recent earlier one is March.
    assert scheduler._prior_report_id(sched, "2026-04-01") == "report-mar"


def test_prior_report_id_ignores_runs_on_or_after_the_given_date():
    sched = scheduler.Schedule(
        tenant_id=TENANT, client_id="c", data_source_ref="c", cadence="daily",
        runs={"2026-03-01": "report-a", "2026-03-05": "report-b"},
    )
    assert scheduler._prior_report_id(sched, "2026-03-01") is None  # nothing strictly earlier
    assert scheduler._prior_report_id(sched, "2026-03-05") == "report-a"


def test_attach_period_comparison_computes_a_real_diff(isolated_report_store):
    prior = _fake_report_object("report-jan", "2026-01", revenue=1000.0, sessions=100)
    report_store.persist_report(TENANT, "report-jan", {
        "html": "<html></html>", "pdf_bytes": b"%PDF", "report": prior.narrative,
        "metrics": prior.metrics, "source_fingerprints": {}, "report_object": prior,
    }, {})

    current = _fake_report_object("report-feb", "2026-02", revenue=1500.0, sessions=120)
    sched = scheduler.Schedule(tenant_id=TENANT, client_id="c", data_source_ref="c", cadence="daily",
                                runs={"2026-01": "report-jan"})

    attached = scheduler._attach_period_comparison(current, sched, "2026-02")

    assert attached is True
    assert current.period_comparison["prior_report_id"] == "report-jan"
    assert current.period_comparison["prior_period_label"] == "2026-01"
    revenue_delta = current.period_comparison["analytics"]["revenue_usd"]
    assert revenue_delta["current"] == 1500.0
    assert revenue_delta["prior"] == 1000.0
    assert revenue_delta["abs_delta"] == 500.0
    assert revenue_delta["pct_delta"] == 50.0
    # sales.totals only has revenue_usd in this fixture -- still diffed.
    assert current.period_comparison["sales"]["revenue_usd"]["abs_delta"] == 1000.0


def test_attach_period_comparison_returns_false_with_nothing_to_diff_against():
    current = _fake_report_object("report-first", "2026-01", revenue=1000.0, sessions=100)
    sched = scheduler.Schedule(tenant_id=TENANT, client_id="c", data_source_ref="c", cadence="daily", runs={})
    attached = scheduler._attach_period_comparison(current, sched, "2026-01")
    assert attached is False
    assert current.period_comparison is None


def test_attach_period_comparison_handles_a_missing_prior_report_object_gracefully(isolated_report_store):
    """runs references a report_id with no persisted report_object.json
    (e.g. a pre-F0 report, or a corrupted entry) -- must degrade to "no
    comparison," never raise."""
    current = _fake_report_object("report-current", "2026-02", revenue=1500.0, sessions=120)
    sched = scheduler.Schedule(tenant_id=TENANT, client_id="c", data_source_ref="c", cadence="daily",
                                runs={"2026-01": "report-that-does-not-exist"})
    attached = scheduler._attach_period_comparison(current, sched, "2026-02")
    assert attached is False
    assert current.period_comparison is None
