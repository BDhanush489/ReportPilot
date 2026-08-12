"""
Tests for report_builder.build_report_from_data_context()'s inbox branch --
a data_context whose connector kind is "imap_inbox" or "slack_inbox" fetches
fresh attachments and runs the exact same build_report() pipeline a file
upload would, instead of querying a SQL warehouse. This is what makes "Mode
2: hosted inbox polling" work: scheduler.py's cadence/idempotency machinery
is completely unchanged, it just calls build_report_from_data_context()
like it always has.

Uses real sample_data/*.csv,*.xlsx bytes as the "fetched attachment"
content (not synthetic placeholders) so the full parse -> metrics ->
narrative -> QA pipeline runs against real data end-to-end, same as every
other live-verified path in this project. The IMAP/Slack connection itself
is mocked (no live mailbox/workspace available here, same limitation
test_email_source.py / test_slack_source.py already document) -- what's
real is everything build_report() does once it has the bytes.
"""
from pathlib import Path

import pytest

from app import data_context, report_builder
from app.email_source import FetchedAttachment

SAMPLE = Path(__file__).parent.parent / "sample_data"
BRANDING = {"agency_name": "Test Agency", "client_name": "Test Client",
            "primary_color": "#2a78d6", "accent_color": "#eda100"}
TENANT = "t1"


@pytest.fixture
def isolated_dir(db_session):
    return db_session


class _FakeConnector:
    """Stands in for IMAPInboxConnector/SlackInboxConnector -- same
    fetch_attachments()/close() shape, real sample-file bytes."""

    def __init__(self, attachments):
        self._attachments = attachments
        self.closed = False

    def fetch_attachments(self, mailbox="INBOX", search="UNSEEN", limit=20, extensions=None, mark_as_read=False):
        return self._attachments

    def close(self):
        self.closed = True


def _real_attachments() -> list[FetchedAttachment]:
    return [
        FetchedAttachment(filename="web_analytics.csv", content=(SAMPLE / "web_analytics.csv").read_bytes(),
                           message_subject="Weekly export", message_from="client@example.com", message_date="d"),
        FetchedAttachment(filename="sales_pipeline.xlsx", content=(SAMPLE / "sales_pipeline.xlsx").read_bytes(),
                           message_subject="Weekly export", message_from="client@example.com", message_date="d"),
    ]


# ---------------------------------------------------------------------------
# imap_inbox
# ---------------------------------------------------------------------------

def test_imap_inbox_context_generates_a_real_report(isolated_dir, monkeypatch):
    data_context.save_data_context(
        TENANT, "client-imap", "imap_inbox",
        {"provider": "gmail", "username": "u@gmail.com", "password": "app-password"}, {},
    )
    fake = _FakeConnector(_real_attachments())
    monkeypatch.setattr(
        "app.email_source.create_inbox_connector",
        lambda provider, username, password: fake,
    )

    result = report_builder.build_report_from_data_context(TENANT, "client-imap", BRANDING)

    assert result["report_object"].metrics.get("analytics")
    assert result["report_object"].metrics.get("sales")
    assert fake.closed is True  # connection is always closed, even on the happy path


def test_imap_inbox_context_closes_connector_even_on_failure(isolated_dir, monkeypatch):
    data_context.save_data_context(
        TENANT, "client-imap-fail", "imap_inbox",
        {"provider": "gmail", "username": "u@gmail.com", "password": "app-password"}, {},
    )
    fake = _FakeConnector([])  # empty inbox -> build_uploads_from_inbox returns {}
    monkeypatch.setattr("app.email_source.create_inbox_connector", lambda provider, username, password: fake)

    with pytest.raises(ValueError, match="No matching attachments"):
        report_builder.build_report_from_data_context(TENANT, "client-imap-fail", BRANDING)
    assert fake.closed is True


# ---------------------------------------------------------------------------
# slack_inbox
# ---------------------------------------------------------------------------

def test_slack_inbox_context_generates_a_real_report(isolated_dir, monkeypatch):
    data_context.save_data_context(
        TENANT, "client-slack", "slack_inbox", {"bot_token": "xoxb-test", "channel_id": "C123"}, {},
    )
    fake = _FakeConnector(_real_attachments())
    monkeypatch.setattr("app.slack_source.create_slack_connector", lambda bot_token, channel_id: fake)

    result = report_builder.build_report_from_data_context(TENANT, "client-slack", BRANDING)

    assert result["report_object"].metrics.get("analytics")
    assert result["report_object"].metrics.get("sales")
    assert fake.closed is True


# ---------------------------------------------------------------------------
# Unknown data context / kind
# ---------------------------------------------------------------------------

def test_missing_data_context_raises_a_clear_error(isolated_dir):
    with pytest.raises(ValueError, match="No data context saved"):
        report_builder.build_report_from_data_context(TENANT, "no-such-client", BRANDING)
