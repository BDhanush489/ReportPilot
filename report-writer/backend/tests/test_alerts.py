"""
Tests for B4 — KPI alerts. GOAL: flag a drop when it happens, not on report day.

Exit criteria proven here:
  - Per-client thresholds, deterministic breach detection --
    test_evaluate_rules_* below.
  - Alert numbers pass QA / trace to F0-B1's own numbers, never re-derived --
    test_alert_values_are_lifted_verbatim_from_the_period_comparison_delta.
  - Rate-limited/deduped -- test_check_alerts_never_refires_the_same_rule_for_the_same_as_of.
  - Small-N suppressed, not shouted -- test_small_prior_value_is_suppressed_not_alerted.
  - A FAIL-badge report never alerts -- test_a_fail_badge_report_alerts_on_nothing.
"""
import sqlite3
from dataclasses import dataclass
from datetime import date

import pytest

from app import alerts, data_context, delivery, scheduler
from app.report_object import Period, ReportObject


def _obj_with_comparison(period_comparison: dict, badge: str = "PASS") -> ReportObject:
    return ReportObject(
        report_id="r1", period=Period(label="p"), sources={}, metrics={}, series={}, charts=[],
        narrative={}, qa={"badge": badge}, branding={}, period_comparison=period_comparison,
    )


# ---------------------------------------------------------------------------
# AlertRule validation + persistence
# ---------------------------------------------------------------------------

def test_alert_rule_rejects_unknown_direction():
    with pytest.raises(ValueError, match="direction must be one of"):
        alerts.AlertRule(id="r1", metric_path="analytics.revenue_usd", direction="sideways", threshold_pct=10)


def test_alert_rule_rejects_non_positive_threshold():
    with pytest.raises(ValueError, match="threshold_pct must be positive"):
        alerts.AlertRule(id="r1", metric_path="analytics.revenue_usd", direction="pct_drop", threshold_pct=-5)


def test_save_and_load_alert_config_round_trips(db_session):
    config = alerts.AlertConfig(tenant_id="t1", client_id="acme", rules=[
        alerts.AlertRule(id="rev-drop", metric_path="analytics.revenue_usd", direction="pct_drop",
                          threshold_pct=15.0, label="Revenue drop"),
    ])
    alerts.save_alert_config(config)
    loaded = alerts.load_alert_config("t1", "acme")
    assert loaded.client_id == "acme"
    assert loaded.rules[0].id == "rev-drop"
    assert loaded.rules[0].threshold_pct == 15.0


def test_load_alert_config_missing_client_returns_none(db_session):
    assert alerts.load_alert_config("t1", "never-configured") is None


# ---------------------------------------------------------------------------
# Breach detection (pure function, hand-built period_comparison)
# ---------------------------------------------------------------------------

def _delta(current, prior, pct):
    return {"field": "revenue_usd", "current": current, "prior": prior,
            "abs_delta": current - prior, "pct_delta": pct}


def test_evaluate_rules_triggers_on_a_real_drop_past_threshold():
    obj = _obj_with_comparison({"analytics": {"revenue_usd": _delta(800.0, 1000.0, -20.0)}})
    rule = alerts.AlertRule(id="r1", metric_path="analytics.revenue_usd", direction="pct_drop", threshold_pct=15.0)
    triggered = alerts.evaluate_rules(obj, [rule], "2026-03-01")
    assert len(triggered) == 1
    assert triggered[0].pct_delta == -20.0
    assert triggered[0].current == 800.0
    assert triggered[0].prior == 1000.0


def test_evaluate_rules_does_not_trigger_below_threshold():
    obj = _obj_with_comparison({"analytics": {"revenue_usd": _delta(950.0, 1000.0, -5.0)}})
    rule = alerts.AlertRule(id="r1", metric_path="analytics.revenue_usd", direction="pct_drop", threshold_pct=15.0)
    assert alerts.evaluate_rules(obj, [rule], "2026-03-01") == []


def test_evaluate_rules_pct_rise_direction_ignores_a_drop():
    obj = _obj_with_comparison({"analytics": {"revenue_usd": _delta(500.0, 1000.0, -50.0)}})
    rule = alerts.AlertRule(id="r1", metric_path="analytics.revenue_usd", direction="pct_rise", threshold_pct=15.0)
    assert alerts.evaluate_rules(obj, [rule], "2026-03-01") == []


def test_evaluate_rules_missing_metric_path_does_not_crash():
    obj = _obj_with_comparison({"analytics": {}})
    rule = alerts.AlertRule(id="r1", metric_path="analytics.revenue_usd", direction="pct_drop", threshold_pct=15.0)
    assert alerts.evaluate_rules(obj, [rule], "2026-03-01") == []


def test_evaluate_rules_no_prior_period_does_not_crash():
    assert alerts.evaluate_rules(_obj_with_comparison({}), [
        alerts.AlertRule(id="r1", metric_path="analytics.revenue_usd", direction="pct_drop", threshold_pct=15.0)
    ], "2026-03-01") == []


def test_small_prior_value_is_suppressed_not_alerted():
    # 1 session -> 2 sessions is technically +100%, but 1 is noise, not signal.
    obj = _obj_with_comparison({"analytics": {"sessions": _delta(2.0, 1.0, 100.0)}})
    rule = alerts.AlertRule(id="r1", metric_path="analytics.sessions", direction="pct_rise", threshold_pct=15.0)
    assert alerts.evaluate_rules(obj, [rule], "2026-03-01") == []


def test_alert_values_are_lifted_verbatim_from_the_period_comparison_delta():
    """Traceability by construction: an alert never re-derives a number,
    it's the exact same float period_diff.py (B1) already computed."""
    delta = _delta(823.45, 1050.0, round((823.45 - 1050.0) / 1050.0 * 100, 1))
    obj = _obj_with_comparison({"sales": {"revenue_usd": delta}})
    rule = alerts.AlertRule(id="r1", metric_path="sales.revenue_usd", direction="pct_drop", threshold_pct=10.0)
    triggered = alerts.evaluate_rules(obj, [rule], "2026-03-01")
    assert triggered[0].current == delta["current"]
    assert triggered[0].prior == delta["prior"]
    assert triggered[0].pct_delta == delta["pct_delta"]


def test_a_fail_badge_report_alerts_on_nothing(db_session):
    obj = _obj_with_comparison({"analytics": {"revenue_usd": _delta(500.0, 1000.0, -50.0)}}, badge="FAIL")
    alerts.save_alert_config(alerts.AlertConfig(tenant_id="t1", client_id="acme", rules=[
        alerts.AlertRule(id="r1", metric_path="analytics.revenue_usd", direction="pct_drop", threshold_pct=10.0),
    ]))
    assert alerts.check_alerts(obj, "t1", "acme", "2026-03-01") == []


def test_check_alerts_with_no_configured_rules_returns_empty(db_session):
    obj = _obj_with_comparison({"analytics": {"revenue_usd": _delta(500.0, 1000.0, -50.0)}})
    assert alerts.check_alerts(obj, "t1", "never-configured", "2026-03-01") == []


def test_check_alerts_never_refires_the_same_rule_for_the_same_as_of(db_session):
    obj = _obj_with_comparison({"analytics": {"revenue_usd": _delta(500.0, 1000.0, -50.0)}})
    alerts.save_alert_config(alerts.AlertConfig(tenant_id="t1", client_id="acme", rules=[
        alerts.AlertRule(id="r1", metric_path="analytics.revenue_usd", direction="pct_drop", threshold_pct=10.0),
    ]))
    first = alerts.check_alerts(obj, "t1", "acme", "2026-03-01")
    assert len(first) == 1
    second = alerts.check_alerts(obj, "t1", "acme", "2026-03-01")  # same as_of -- already fired
    assert second == []
    # A DIFFERENT as_of with the same breach is a genuinely new alert.
    third = alerts.check_alerts(obj, "t1", "acme", "2026-04-01")
    assert len(third) == 1


# ---------------------------------------------------------------------------
# Delivery -- reuses delivery.py's channel_impl injection pattern verbatim
# ---------------------------------------------------------------------------

@dataclass
class _FakeChannel:
    sent: list = None

    def __post_init__(self):
        self.sent = []

    def send(self, to, subject, body_html, attachments):
        self.sent.append({"to": to, "subject": subject, "body_html": body_html})
        return delivery.ChannelSendResult(status="sent")


def test_deliver_alerts_sends_through_the_injected_channel(db_session):
    triggered = [alerts.Alert(rule_id="r1", label="Revenue drop", metric_path="analytics.revenue_usd",
                               current=500.0, prior=1000.0, pct_delta=-50.0, as_of="2026-03-01")]
    fake = _FakeChannel()
    attempt = alerts.deliver_alerts(triggered, ["consultant@agency.com"], "t1", "acme", channel_impl=fake)
    assert attempt.status == "sent"
    assert fake.sent[0]["to"] == ["consultant@agency.com"]
    assert "1 KPI breach" in fake.sent[0]["subject"]
    assert "Revenue drop" in fake.sent[0]["body_html"]
    # Logged via the SAME delivery.py log mechanism, not a parallel one.
    logged = delivery.list_delivery_log("t1", f"alerts-acme-2026-03-01")
    assert logged[0].status == "sent"


def test_deliver_alerts_with_no_alerts_raises():
    with pytest.raises(ValueError, match="no alerts"):
        alerts.deliver_alerts([], ["x@y.com"], "t1", "acme")


# ---------------------------------------------------------------------------
# End-to-end via scheduler.run_schedule: a real revenue drop between two
# real periods actually fires an alert through the real pipeline.
# ---------------------------------------------------------------------------

def _make_sqlite_client(tmp_path, client_id: str, rows: list[tuple]) -> None:
    db_path = tmp_path / f"{client_id}.db"
    db_path.unlink(missing_ok=True)  # rewritten mid-test to simulate a new period landing
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE analytics (date TEXT, channel_group TEXT, device_category TEXT, "
        "sessions INTEGER, new_users INTEGER, conversions INTEGER, revenue_usd REAL)"
    )
    conn.executemany("INSERT INTO analytics VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    fields = ["date", "channel_group", "device_category", "sessions", "new_users", "conversions", "revenue_usd"]
    data_context.save_data_context(
        "t1", client_id, "sqlite", {"path": str(db_path)},
        {"analytics": {"table": "analytics", "column_map": {f: f for f in fields}}},
    )


@pytest.fixture
def alert_client(tmp_path, monkeypatch, db_session):
    cid = "b4-sched-client"
    monkeypatch.setattr("app.agent._ollama_available", lambda: False)
    return cid


def test_run_schedule_fires_a_real_alert_on_a_real_revenue_drop(alert_client, tmp_path):
    # First period: healthy revenue. Second period (a different schedule
    # data set, since run_schedule always reads the client's CURRENT table)
    # -- to simulate drift between periods without a second table, use two
    # sequential schedules against tables with different data, diffed via
    # scheduler's own prior-report lookup.
    _make_sqlite_client(tmp_path, alert_client, [
        ("2026-01-01", "Organic Search", "desktop", 500, 300, 40, 5000.0),
        ("2026-01-08", "Organic Search", "desktop", 520, 310, 42, 5200.0),
    ])
    sched = scheduler.Schedule(tenant_id="t1", client_id=alert_client, data_source_ref=alert_client, cadence="weekly",
                                branding={"agency_name": "A", "client_name": "B"})
    first = scheduler.run_schedule(sched, date(2026, 2, 1), dry_run=False)
    assert first.status == "generated"
    assert first.alerts_fired == 0  # no prior period yet -- nothing to diff against

    alerts.save_alert_config(alerts.AlertConfig(tenant_id="t1", client_id=alert_client, rules=[
        alerts.AlertRule(id="rev-drop", metric_path="analytics.revenue_usd", direction="pct_drop",
                          threshold_pct=20.0, label="Revenue drop"),
    ]))

    # Overwrite the table with a much smaller revenue figure -- the same
    # "regenerates from whatever's newly in the table" contract
    # build_report_from_data_context's own docstring describes.
    _make_sqlite_client(tmp_path, alert_client, [
        ("2026-01-01", "Organic Search", "desktop", 500, 300, 40, 1000.0),
        ("2026-01-08", "Organic Search", "desktop", 520, 310, 42, 1100.0),
    ])
    second = scheduler.run_schedule(sched, date(2026, 2, 8), dry_run=False)
    assert second.status == "generated"
    assert second.alerts_fired == 1
    assert "1 KPI alert" in second.detail
