"""
Tests for app/delivery.py (Track B3 — delivery).

The QA-gate and logging logic is tested directly and deterministically by
injecting a fake channel (channel_impl) — the same dependency-injection
pattern connectors/__init__.py already uses for warehouse connections.
EmailChannel/SlackChannel's real send() paths are also tested, but only for
their honest "unavailable" behavior: this environment has no SMTP_HOST or
SLACK_WEBHOOK_URL configured, so that's the real, correct outcome to
verify — not a mock standing in for credentials that don't exist here.
"""
import json

import pytest

from app.delivery import (
    EmailChannel,
    SlackChannel,
    deliver_report,
    list_delivery_log,
)
from app.report_object import Period, ReportObject, SourceInfo
from app.store_models import DeliveryLogEntry

METRICS = {
    "analytics": {"totals": {"sessions": 1000, "revenue_usd": 5000.0}},
    "period_label": "2026-01-01 to 2026-06-30",
}

NARRATIVE = {
    "report_title": "Aurora — Performance Report",
    "period_label": METRICS["period_label"],
    "executive_summary": "Sessions grew to 1,000.",
    "highlights": [], "watchouts": [],
    "sections": [{"heading": "Web Analytics", "narrative": "n", "recommendations": []}],
    "next_steps": [],
}

BRANDING = {"agency_name": "Northlight", "client_name": "Aurora", "primary_color": "#2a78d6", "accent_color": "#eda100"}


@pytest.fixture(autouse=True)
def _isolated_delivery_log(db_session):
    """Every test in this file that calls deliver_report writes a delivery
    log entry -- db_session gives each test its own throwaway DB (see
    tests/conftest.py), autoused so no test can forget it."""
    return db_session


def _obj(report_id: str, badge: str) -> ReportObject:
    qa = {
        "badge": badge, "failing_checks": [] if badge != "FAIL" else ["traceability"],
        "traceability": {}, "aggregation_sanity": {}, "unsupported_claims": {}, "chart_citations": {},
    }
    return ReportObject(
        report_id=report_id, period=Period(label=METRICS["period_label"]),
        sources={"analytics": SourceInfo(row_count=10, sha256="a" * 64)},
        metrics=METRICS, series={}, charts=[], narrative=NARRATIVE, qa=qa,
        branding=BRANDING, section_order=["analytics"],
    )


class FakeChannel:
    """Records every call; lets a test dictate what "sending" returns
    without touching a real network connection."""
    def __init__(self, status="sent", reason=""):
        self.status, self.reason = status, reason
        self.calls: list[dict] = []

    def send(self, to, subject, body_html, attachments):
        self.calls.append({"to": to, "subject": subject, "body_html": body_html, "attachments": attachments})
        from app.delivery import ChannelSendResult
        return ChannelSendResult(status=self.status, reason=self.reason)


# ---------------------------------------------------------------------------
# Email delivery of the PDF + QA badge summary; templated, per-client
# recipient list
# ---------------------------------------------------------------------------

def test_passing_badge_sends_to_the_client_recipients():
    obj = _obj("report-pass-1", "PASS")
    fake = FakeChannel(status="sent")
    attempt = deliver_report("t1", obj, ["client@example.com"], channel_impl=fake)

    assert attempt.status == "sent"
    assert fake.calls[0]["to"] == ["client@example.com"]
    assert "Aurora" in fake.calls[0]["subject"]


def test_body_is_the_same_email_html_export_d1_already_builds():
    from app.exports import export_email_html

    obj = _obj("report-pass-2", "PASS")
    fake = FakeChannel()
    deliver_report("t1", obj, ["client@example.com"], channel_impl=fake)

    assert fake.calls[0]["body_html"] == export_email_html(obj).content


def test_pdf_is_attached():
    obj = _obj("report-pass-3", "PASS")
    fake = FakeChannel()
    deliver_report("t1", obj, ["client@example.com"], channel_impl=fake)
    filenames = [a[0] for a in fake.calls[0]["attachments"]]
    assert f"report-{obj.report_id}.pdf" in filenames
    pdf_bytes = fake.calls[0]["attachments"][0][1]
    assert len(pdf_bytes) > 0


def test_recipient_list_is_per_client_not_a_shared_default():
    obj_a = _obj("report-a", "PASS")
    obj_b = _obj("report-b", "PASS")
    fake = FakeChannel()
    deliver_report("t1", obj_a, ["a@example.com"], channel_impl=fake)
    deliver_report("t1", obj_b, ["b@example.com"], channel_impl=fake)
    assert fake.calls[0]["to"] == ["a@example.com"]
    assert fake.calls[1]["to"] == ["b@example.com"]


# ---------------------------------------------------------------------------
# Slack delivery optional, behind the same interface
# ---------------------------------------------------------------------------

def test_slack_channel_uses_the_identical_deliver_report_interface():
    obj = _obj("report-slack-1", "PASS")
    fake = FakeChannel(status="sent")
    attempt = deliver_report("t1", obj, ["#client-reports"], channel="slack", channel_impl=fake)
    assert attempt.status == "sent"
    assert attempt.channel == "slack"


def test_unknown_channel_name_raises_not_silently_no_ops():
    obj = _obj("report-x", "PASS")
    with pytest.raises(ValueError, match="Unknown channel"):
        deliver_report("t1", obj, ["x@example.com"], channel="carrier_pigeon")


# ---------------------------------------------------------------------------
# A FAILing QA badge blocks auto-send (or redirects to the consultant, not
# the client)
# ---------------------------------------------------------------------------

def test_failing_badge_with_no_consultant_recipients_blocks_entirely():
    obj = _obj("report-fail-1", "FAIL")
    fake = FakeChannel()
    attempt = deliver_report("t1", obj, ["client@example.com"], channel_impl=fake)

    assert attempt.status == "blocked"
    assert fake.calls == []  # never even attempted to send


def test_failing_badge_with_consultant_recipients_redirects_not_to_the_client():
    obj = _obj("report-fail-2", "FAIL")
    fake = FakeChannel(status="sent")
    attempt = deliver_report(
        "t1", obj, ["client@example.com"], consultant_recipients=["consultant@agency.com"], channel_impl=fake,
    )

    assert attempt.status == "sent_to_consultant"
    assert fake.calls[0]["to"] == ["consultant@agency.com"]
    assert "client@example.com" not in fake.calls[0]["to"]
    assert "NEEDS REVIEW" in fake.calls[0]["subject"]


def test_passing_with_warnings_badge_still_sends_to_the_client():
    """Only a hard FAIL gates delivery -- PASS-WITH-WARNINGS is still a
    real pass, not a reason to withhold the report."""
    obj = _obj("report-warn-1", "PASS-WITH-WARNINGS")
    fake = FakeChannel(status="sent")
    attempt = deliver_report("t1", obj, ["client@example.com"], channel_impl=fake)
    assert attempt.status == "sent"
    assert fake.calls[0]["to"] == ["client@example.com"]


# ---------------------------------------------------------------------------
# Delivery attempts logged; success/failure is observable, not silent
# ---------------------------------------------------------------------------

def test_every_outcome_is_logged_sent_blocked_and_failed():
    deliver_report("t1", _obj("r-sent", "PASS"), ["a@x.com"], channel_impl=FakeChannel(status="sent"))
    deliver_report("t1", _obj("r-blocked", "FAIL"), ["a@x.com"], channel_impl=FakeChannel(status="sent"))
    deliver_report("t1", _obj("r-failed", "PASS"), ["a@x.com"], channel_impl=FakeChannel(status="failed", reason="SMTP timeout"))

    assert list_delivery_log("t1", "r-sent")[0].status == "sent"
    assert list_delivery_log("t1", "r-blocked")[0].status == "blocked"
    failed_entry = list_delivery_log("t1", "r-failed")[0]
    assert failed_entry.status == "failed"
    assert failed_entry.reason == "SMTP timeout"


def test_delivery_log_is_append_only_across_multiple_attempts():
    obj = _obj("r-retry", "PASS")
    deliver_report("t1", obj, ["a@x.com"], channel_impl=FakeChannel(status="failed", reason="first attempt failed"))
    deliver_report("t1", obj, ["a@x.com"], channel_impl=FakeChannel(status="sent"))

    log = list_delivery_log("t1", "r-retry")
    assert len(log) == 2
    assert log[0].status == "failed"
    assert log[1].status == "sent"


def test_delivery_log_entries_are_json_serializable(db_session):
    deliver_report("t1", _obj("r-json", "PASS"), ["a@x.com"], channel_impl=FakeChannel(status="sent"))
    row = db_session.query(DeliveryLogEntry).filter_by(tenant_id="t1", report_id="r-json").one()
    json.dumps(row.attempt)  # raises if the persisted value isn't JSON-safe


def test_no_delivery_log_for_a_report_that_was_never_attempted():
    assert list_delivery_log("t1", "never-attempted-report-id") == []


# ---------------------------------------------------------------------------
# Real channel implementations: honest "unavailable" without a connector,
# never a fake success
# ---------------------------------------------------------------------------

def test_email_channel_is_unavailable_without_smtp_configured(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    result = EmailChannel().send(["a@x.com"], "subject", "<p>body</p>", [])
    assert result.status == "unavailable"
    assert "SMTP_HOST" in result.reason


def test_slack_channel_is_unavailable_without_webhook_configured(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    result = SlackChannel().send(["#channel"], "subject", "<p>body</p>", [])
    assert result.status == "unavailable"
    assert "SLACK_WEBHOOK_URL" in result.reason


def test_deliver_report_end_to_end_with_real_unconfigured_email_channel(monkeypatch):
    """No channel_impl injected -- uses the real EmailChannel. Confirms the
    whole path (gate -> render -> attempt real send -> log) is honest about
    being unavailable end to end, not just at the channel layer alone."""
    monkeypatch.delenv("SMTP_HOST", raising=False)

    attempt = deliver_report("t1", _obj("r-real-email", "PASS"), ["a@x.com"])
    assert attempt.status == "unavailable"
    assert list_delivery_log("t1", "r-real-email")[0].status == "unavailable"
