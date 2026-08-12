"""
Tests for app/email_source.py (inbound email-as-a-data-source).

MIME parsing / slot-guessing / uploads-dict assembly are tested directly
against hand-built email.message.Message objects and a fake connector — no
live mailbox needed for that logic, same dependency-injection shape as
B3's delivery tests. IMAPInboxConnector's real connection path is tested
only for its honest failure behavior (a DNS-guaranteed-invalid host, per
RFC 2606's .invalid TLD, fails fast without a live server) — there is no
real Gmail/Outlook mailbox available in this environment to round-trip
against, the same limitation D1/B3 already have for their live connectors.
"""
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO

import pytest

from app.connectors.base import ConnectorError
from app.email_source import (
    FetchedAttachment,
    IMAPInboxConnector,
    attachments_from_message,
    build_uploads_from_inbox,
    create_graph_api_connector,
    create_inbox_connector,
    guess_upload_slot,
    inbox_connector_from_env,
)


def _message_with_attachments(subject: str, attachments: list[tuple[str, bytes]]) -> MIMEMultipart:
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = "client@example.com"
    msg["Date"] = "Mon, 1 Jun 2026 09:00:00 +0000"
    msg.attach(MIMEText("Please find attached.", "plain"))
    for filename, content in attachments:
        part = MIMEApplication(content, Name=filename)
        part["Content-Disposition"] = f'attachment; filename="{filename}"'
        msg.attach(part)
    return msg


# ---------------------------------------------------------------------------
# guess_upload_slot
# ---------------------------------------------------------------------------

def test_guesses_analytics_slot():
    assert guess_upload_slot("web_analytics_export.csv") == "analytics"
    assert guess_upload_slot("GA4_sessions.csv") == "analytics"


def test_guesses_seo_slot():
    assert guess_upload_slot("seo_audit_june.csv") == "seo"


def test_guesses_sales_slot():
    assert guess_upload_slot("sales_pipeline.xlsx") == "sales"
    assert guess_upload_slot("crm_deals_export.xlsx") == "sales"


def test_unrecognized_filename_has_no_slot():
    assert guess_upload_slot("random_document.csv") is None


# ---------------------------------------------------------------------------
# attachments_from_message: pure MIME parsing, no live mailbox
# ---------------------------------------------------------------------------

def test_extracts_a_single_matching_attachment():
    msg = _message_with_attachments("Q2 report data", [("web_analytics.csv", b"date,sessions\n2026-01-01,100\n")])
    results = attachments_from_message(msg)
    assert len(results) == 1
    assert results[0].filename == "web_analytics.csv"
    assert results[0].content == b"date,sessions\n2026-01-01,100\n"
    assert results[0].message_subject == "Q2 report data"
    assert results[0].message_from == "client@example.com"


def test_extracts_multiple_attachments_from_one_message():
    msg = _message_with_attachments("Monthly data", [
        ("web_analytics.csv", b"a"), ("seo_audit.csv", b"b"), ("sales_pipeline.xlsx", b"c"),
    ])
    results = attachments_from_message(msg)
    assert {r.filename for r in results} == {"web_analytics.csv", "seo_audit.csv", "sales_pipeline.xlsx"}


def test_non_matching_extension_is_skipped():
    msg = _message_with_attachments("With a PDF", [("cover_letter.pdf", b"%PDF-1.4")])
    assert attachments_from_message(msg) == []


def test_message_with_no_attachments_returns_empty_list():
    msg = MIMEMultipart()
    msg["Subject"] = "No attachments here"
    msg.attach(MIMEText("Just text.", "plain"))
    assert attachments_from_message(msg) == []


def test_custom_extensions_filter_is_respected():
    msg = _message_with_attachments("Custom", [("data.tsv", b"a\tb\n1\t2\n")])
    assert attachments_from_message(msg) == []  # .tsv not in default extensions
    assert len(attachments_from_message(msg, extensions=(".tsv",))) == 1


# ---------------------------------------------------------------------------
# build_uploads_from_inbox: assembles the exact dict shape
# report_builder.build_report() already expects
# ---------------------------------------------------------------------------

class FakeInboxConnector:
    def __init__(self, attachments: list[FetchedAttachment]):
        self._attachments = attachments
        self.fetch_calls: list[dict] = []

    def fetch_attachments(self, mailbox="INBOX", search="UNSEEN", limit=20, extensions=None, mark_as_read=False):
        self.fetch_calls.append({"mailbox": mailbox, "search": search, "limit": limit, "mark_as_read": mark_as_read})
        return self._attachments


def _att(filename: str, content: bytes) -> FetchedAttachment:
    return FetchedAttachment(filename=filename, content=content, message_subject="s", message_from="f", message_date="d")


def test_build_uploads_maps_each_attachment_to_its_slot():
    fake = FakeInboxConnector([
        _att("web_analytics.csv", b"analytics-bytes"),
        _att("seo_audit.csv", b"seo-bytes"),
        _att("sales_pipeline.xlsx", b"sales-bytes"),
    ])
    uploads, unmatched = build_uploads_from_inbox(fake)

    assert set(uploads.keys()) == {"analytics", "seo", "sales"}
    assert unmatched == []
    filename, buf = uploads["analytics"]
    assert filename == "web_analytics.csv"
    assert isinstance(buf, BytesIO)
    assert buf.read() == b"analytics-bytes"


def test_uploads_dict_is_directly_usable_as_report_builder_upload_values():
    """The exact shape report_builder.build_report(uploads, branding)
    expects: {"analytics": (filename, filelike), ...} with a .name
    attribute on the filelike, since parsers.py reads it."""
    fake = FakeInboxConnector([_att("web_analytics.csv", b"x")])
    uploads, _ = build_uploads_from_inbox(fake)
    filename, buf = uploads["analytics"]
    assert buf.name == filename


def test_unrecognized_filename_goes_to_unmatched_not_silently_dropped():
    fake = FakeInboxConnector([_att("mystery_export.csv", b"?")])
    uploads, unmatched = build_uploads_from_inbox(fake)
    assert uploads == {}
    assert len(unmatched) == 1
    assert unmatched[0].filename == "mystery_export.csv"


def test_second_attachment_for_an_already_filled_slot_goes_to_unmatched():
    """First match per slot wins -- a second "analytics"-named attachment
    in the same fetch doesn't silently overwrite the first."""
    fake = FakeInboxConnector([
        _att("web_analytics_v1.csv", b"first"),
        _att("web_analytics_v2.csv", b"second"),
    ])
    uploads, unmatched = build_uploads_from_inbox(fake)
    assert uploads["analytics"][0] == "web_analytics_v1.csv"
    assert len(unmatched) == 1
    assert unmatched[0].filename == "web_analytics_v2.csv"


def test_build_uploads_passes_search_params_through_to_the_connector():
    fake = FakeInboxConnector([])
    build_uploads_from_inbox(fake, mailbox="Reports", search="ALL", limit=5, mark_as_read=True)
    assert fake.fetch_calls[0] == {"mailbox": "Reports", "search": "ALL", "limit": 5, "mark_as_read": True}


def test_empty_inbox_produces_empty_uploads_not_an_error():
    uploads, unmatched = build_uploads_from_inbox(FakeInboxConnector([]))
    assert uploads == {} and unmatched == []


# ---------------------------------------------------------------------------
# Real connector: provider registry + honest failure behavior
# ---------------------------------------------------------------------------

def test_create_inbox_connector_rejects_unknown_provider():
    with pytest.raises(ConnectorError, match="Unknown provider"):
        create_inbox_connector("yahoo", "user@example.com", "password")


def test_imap_connector_raises_connector_error_on_unreachable_host():
    # .invalid is reserved by RFC 2606 to never resolve -- fails fast via
    # DNS, not a live-server timeout.
    with pytest.raises(ConnectorError, match="IMAP login failed"):
        IMAPInboxConnector(host="nonexistent-mailhost.invalid", username="u", password="p")


def test_inbox_connector_from_env_returns_none_when_not_configured(monkeypatch):
    monkeypatch.delenv("IMAP_USERNAME", raising=False)
    monkeypatch.delenv("IMAP_PASSWORD", raising=False)
    assert inbox_connector_from_env("gmail") is None


def test_inbox_connector_from_env_delegates_to_create_inbox_connector_when_configured(monkeypatch):
    """When configured, it should attempt a real connection via the same
    path create_inbox_connector uses -- verified against the unreachable
    .invalid host (no live-network dependency, no risk of tripping a real
    mail provider's automated-login-attempt rate limiting) rather than a
    real provider host with fake credentials."""
    monkeypatch.setattr("app.email_source.PROVIDER_IMAP_HOSTS", {"gmail": "nonexistent-mailhost.invalid"})
    monkeypatch.setenv("IMAP_USERNAME", "u")
    monkeypatch.setenv("IMAP_PASSWORD", "p")
    with pytest.raises(ConnectorError, match="IMAP login failed"):
        inbox_connector_from_env("gmail")


# ---------------------------------------------------------------------------
# Graph API / Outlook modern-auth: honestly unbuilt, never a fake success
# ---------------------------------------------------------------------------

def test_graph_api_connector_is_explicitly_not_implemented():
    with pytest.raises(ConnectorError, match="Azure AD"):
        create_graph_api_connector()
