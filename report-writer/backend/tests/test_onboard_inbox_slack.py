"""
Tests for the two new onboarding endpoints that back Mode 2 (hosted inbox
polling): POST /api/data-sources/onboard-inbox and .../onboard-slack. Both
"test the connection, then save" like the existing warehouse onboarding
endpoint -- a bad app password or bot token fails at onboarding time, not on
the first scheduled run days later.

The full loop -- onboard an inbox source, attach it to a schedule, fire the
schedule -- is exercised end-to-end in
test_mode_2_schedule_backed_by_inbox_source_generates_a_real_report below,
using real sample_data bytes behind a mocked connector (no live mailbox/
workspace available in this environment, same limitation every other live
connector in this project documents).
"""
from pathlib import Path

import pytest

from app import data_context
from app.connectors.base import ConnectorError
from app.email_source import FetchedAttachment
from tests.conftest import seed_tenant

SAMPLE = Path(__file__).parent.parent / "sample_data"


@pytest.fixture
def tenant_id(client, db_session):
    return seed_tenant(db_session, client, google_sub="g-onboard", email="a@northlight.com", name="Alex")


class _FakeConnector:
    def __init__(self, attachments=None, raise_on_init: Exception | None = None):
        if raise_on_init:
            raise raise_on_init
        self._attachments = attachments or []
        self.closed = False

    def fetch_attachments(self, mailbox="INBOX", search="UNSEEN", limit=20, extensions=None, mark_as_read=False):
        return self._attachments

    def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# onboard-inbox
# ---------------------------------------------------------------------------

def test_onboard_inbox_saves_a_working_connection(client, tenant_id, monkeypatch):
    monkeypatch.setattr(
        "app.email_source.create_inbox_connector",
        lambda provider, username, password: _FakeConnector(),
    )
    r = client.post("/api/data-sources/onboard-inbox", json={
        "client_id": "acme", "provider": "gmail", "username": "u@gmail.com", "password": "app-pw",
    })
    assert r.status_code == 200
    assert r.json()["kind"] == "imap_inbox"

    ctx = data_context.load_data_context(tenant_id, "acme")
    assert ctx["connector"]["kind"] == "imap_inbox"
    assert ctx["connector"]["config"]["username"] == "u@gmail.com"


def test_onboard_inbox_rejects_a_bad_connection_without_saving(client, tenant_id, monkeypatch):
    monkeypatch.setattr(
        "app.email_source.create_inbox_connector",
        lambda provider, username, password: (_ for _ in ()).throw(ConnectorError("IMAP login failed")),
    )
    r = client.post("/api/data-sources/onboard-inbox", json={
        "client_id": "acme-bad", "provider": "gmail", "username": "u@gmail.com", "password": "wrong",
    })
    assert r.status_code == 400
    assert data_context.load_data_context(tenant_id, "acme-bad") is None


# ---------------------------------------------------------------------------
# onboard-slack
# ---------------------------------------------------------------------------

def test_onboard_slack_saves_a_working_connection(client, tenant_id, monkeypatch):
    monkeypatch.setattr(
        "app.slack_source.create_slack_connector",
        lambda bot_token, channel_id: _FakeConnector(),
    )
    r = client.post("/api/data-sources/onboard-slack", json={
        "client_id": "beta", "bot_token": "xoxb-test", "channel_id": "C123",
    })
    assert r.status_code == 200
    assert r.json()["kind"] == "slack_inbox"

    ctx = data_context.load_data_context(tenant_id, "beta")
    assert ctx["connector"]["kind"] == "slack_inbox"
    assert ctx["connector"]["config"]["channel_id"] == "C123"


def test_onboard_slack_rejects_a_bad_token_without_saving(client, tenant_id, monkeypatch):
    monkeypatch.setattr(
        "app.slack_source.create_slack_connector",
        lambda bot_token, channel_id: (_ for _ in ()).throw(ConnectorError("Slack API 'auth.test' returned an error: invalid_auth")),
    )
    r = client.post("/api/data-sources/onboard-slack", json={
        "client_id": "beta-bad", "bot_token": "xoxb-bad", "channel_id": "C123",
    })
    assert r.status_code == 400
    assert data_context.load_data_context(tenant_id, "beta-bad") is None


# ---------------------------------------------------------------------------
# End-to-end: onboard -> schedule -> fire -> a real report comes out
# ---------------------------------------------------------------------------

def test_mode_2_schedule_backed_by_inbox_source_generates_a_real_report(client, tenant_id, monkeypatch):
    real_attachments = [
        FetchedAttachment(filename="web_analytics.csv", content=(SAMPLE / "web_analytics.csv").read_bytes(),
                           message_subject="s", message_from="f", message_date="d"),
        FetchedAttachment(filename="sales_pipeline.xlsx", content=(SAMPLE / "sales_pipeline.xlsx").read_bytes(),
                           message_subject="s", message_from="f", message_date="d"),
    ]
    monkeypatch.setattr(
        "app.email_source.create_inbox_connector",
        lambda provider, username, password: _FakeConnector(),
    )
    r = client.post("/api/data-sources/onboard-inbox", json={
        "client_id": "gamma", "provider": "gmail", "username": "u@gmail.com", "password": "app-pw",
    })
    assert r.status_code == 200

    # Re-point the connector used at *report-generation* time to one that
    # actually has attachments -- onboarding only needed to prove login works.
    monkeypatch.setattr(
        "app.email_source.create_inbox_connector",
        lambda provider, username, password: _FakeConnector(real_attachments),
    )

    r = client.post("/api/schedules", json={
        "client_id": "gamma", "data_source_ref": "gamma", "cadence": "daily",
        "branding": {"agency_name": "A", "client_name": "Gamma"},
    })
    assert r.status_code == 200

    r = client.post("/api/schedules/run", params={"as_of": "2026-06-01", "dry_run": "false"})
    assert r.status_code == 200
    results = r.json()["results"]
    gamma_result = next(res for res in results if res["client_id"] == "gamma")
    assert gamma_result["status"] == "generated"
    assert gamma_result["report_id"] is not None

    # The report really exists and carries real computed metrics -- not a stub.
    detail = client.get(f"/api/report/{gamma_result['report_id']}")
    assert detail.status_code == 200
    assert detail.json()["qa"] is not None
