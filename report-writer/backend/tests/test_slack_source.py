"""
Tests for app/slack_source.py (inbound Slack-as-a-data-source).

No live Slack workspace is available in this environment (same limitation
D1/B3's other live connectors have), so SlackInboxConnector's HTTP calls are
mocked at the urllib.request.urlopen boundary — a fake that inspects the
requested URL and returns canned Slack API JSON, the smallest surface that
still exercises the real request-building/response-parsing/error-handling
code, not a mock of SlackInboxConnector itself.
"""
import json
import urllib.error
from io import BytesIO

import pytest

from app.connectors.base import ConnectorError
from app.email_source import build_uploads_from_inbox
from app.slack_source import (
    SlackInboxConnector,
    create_slack_connector,
    slack_connector_from_env,
)


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _json_response(payload: dict) -> _FakeResponse:
    return _FakeResponse(json.dumps(payload).encode("utf-8"))


def _install_fake_urlopen(monkeypatch, handler):
    """handler(request) -> _FakeResponse, or raises for a simulated network error."""
    monkeypatch.setattr("app.slack_source.urllib.request.urlopen", handler)


def _auth_ok_handler(files=None, download_bytes=b""):
    files = files or []

    def handler(req, timeout=None):  # noqa: ARG001
        url = req.full_url
        if "auth.test" in url:
            return _json_response({"ok": True, "user": "reportpilot-bot"})
        if "files.list" in url:
            return _json_response({"ok": True, "files": files})
        return _FakeResponse(download_bytes)

    return handler


# ---------------------------------------------------------------------------
# Connection: fail fast, honestly, same posture as IMAPInboxConnector
# ---------------------------------------------------------------------------

def test_connector_verifies_auth_on_init(monkeypatch):
    calls = []

    def handler(req, timeout=None):  # noqa: ARG001
        calls.append(req.full_url)
        return _json_response({"ok": True})

    _install_fake_urlopen(monkeypatch, handler)
    SlackInboxConnector(bot_token="xoxb-test", channel_id="C123")
    assert any("auth.test" in url for url in calls)


def test_bad_token_raises_connector_error_immediately(monkeypatch):
    def handler(req, timeout=None):  # noqa: ARG001
        return _json_response({"ok": False, "error": "invalid_auth"})

    _install_fake_urlopen(monkeypatch, handler)
    with pytest.raises(ConnectorError, match="invalid_auth"):
        SlackInboxConnector(bot_token="xoxb-bad", channel_id="C123")


def test_network_failure_raises_connector_error(monkeypatch):
    def handler(req, timeout=None):  # noqa: ARG001
        raise urllib.error.URLError("no route to host")

    _install_fake_urlopen(monkeypatch, handler)
    with pytest.raises(ConnectorError, match="auth.test"):
        SlackInboxConnector(bot_token="xoxb-test", channel_id="C123")


# ---------------------------------------------------------------------------
# fetch_attachments: filtering, downloading, FetchedAttachment shape
# ---------------------------------------------------------------------------

def test_fetch_attachments_downloads_matching_files(monkeypatch):
    files = [
        {"name": "web_analytics.csv", "url_private_download": "https://files.slack.com/a.csv",
         "title": "Analytics export", "user": "U1", "timestamp": 1717200000},
        {"name": "cover_letter.pdf", "url_private_download": "https://files.slack.com/b.pdf",
         "title": "Not data", "user": "U2", "timestamp": 1717200001},
    ]

    def handler(req, timeout=None):  # noqa: ARG001
        url = req.full_url
        if "auth.test" in url:
            return _json_response({"ok": True})
        if "files.list" in url:
            return _json_response({"ok": True, "files": files})
        assert url == "https://files.slack.com/a.csv"
        return _FakeResponse(b"date,sessions\n2026-01-01,100\n")

    _install_fake_urlopen(monkeypatch, handler)
    connector = SlackInboxConnector(bot_token="xoxb-test", channel_id="C123")
    results = connector.fetch_attachments()

    assert len(results) == 1  # the .pdf never matched an extension, never downloaded
    assert results[0].filename == "web_analytics.csv"
    assert results[0].content == b"date,sessions\n2026-01-01,100\n"
    assert results[0].message_from == "U1"


def test_fetch_attachments_passes_channel_and_limit(monkeypatch):
    seen_urls = []

    def handler(req, timeout=None):  # noqa: ARG001
        seen_urls.append(req.full_url)
        if "auth.test" in req.full_url:
            return _json_response({"ok": True})
        return _json_response({"ok": True, "files": []})

    _install_fake_urlopen(monkeypatch, handler)
    connector = SlackInboxConnector(bot_token="xoxb-test", channel_id="C999")
    connector.fetch_attachments(limit=5)

    files_list_call = next(u for u in seen_urls if "files.list" in u)
    assert "channel=C999" in files_list_call
    assert "count=5" in files_list_call


def test_files_list_error_raises_connector_error(monkeypatch):
    def handler(req, timeout=None):  # noqa: ARG001
        if "auth.test" in req.full_url:
            return _json_response({"ok": True})
        return _json_response({"ok": False, "error": "channel_not_found"})

    _install_fake_urlopen(monkeypatch, handler)
    connector = SlackInboxConnector(bot_token="xoxb-test", channel_id="C_missing")
    with pytest.raises(ConnectorError, match="channel_not_found"):
        connector.fetch_attachments()


def test_files_with_no_download_url_are_skipped_not_errored(monkeypatch):
    files = [{"name": "sales_pipeline.csv", "title": "", "user": "U1", "timestamp": 1}]  # no url_private_download

    def handler(req, timeout=None):  # noqa: ARG001
        if "auth.test" in req.full_url:
            return _json_response({"ok": True})
        if "files.list" in req.full_url:
            return _json_response({"ok": True, "files": files})
        raise AssertionError("should never attempt a download for a file with no URL")

    _install_fake_urlopen(monkeypatch, handler)
    connector = SlackInboxConnector(bot_token="xoxb-test", channel_id="C123")
    assert connector.fetch_attachments() == []


# ---------------------------------------------------------------------------
# build_uploads_from_inbox() works unchanged against a Slack connector --
# the whole point of matching IMAPInboxConnector's call shape.
# ---------------------------------------------------------------------------

def test_slack_connector_works_with_build_uploads_from_inbox(monkeypatch):
    files = [
        {"name": "web_analytics.csv", "url_private_download": "https://files.slack.com/a.csv",
         "title": "", "user": "U1", "timestamp": 1},
        {"name": "sales_pipeline.xlsx", "url_private_download": "https://files.slack.com/b.xlsx",
         "title": "", "user": "U1", "timestamp": 2},
    ]
    downloads = {"https://files.slack.com/a.csv": b"analytics-bytes",
                 "https://files.slack.com/b.xlsx": b"sales-bytes"}

    def handler(req, timeout=None):  # noqa: ARG001
        url = req.full_url
        if "auth.test" in url:
            return _json_response({"ok": True})
        if "files.list" in url:
            return _json_response({"ok": True, "files": files})
        return _FakeResponse(downloads[url])

    _install_fake_urlopen(monkeypatch, handler)
    connector = SlackInboxConnector(bot_token="xoxb-test", channel_id="C123")
    uploads, unmatched = build_uploads_from_inbox(connector)

    assert set(uploads.keys()) == {"analytics", "sales"}
    assert unmatched == []
    filename, buf = uploads["analytics"]
    assert filename == "web_analytics.csv"
    assert isinstance(buf, BytesIO)
    assert buf.read() == b"analytics-bytes"


# ---------------------------------------------------------------------------
# create_slack_connector / slack_connector_from_env: honest not-configured state
# ---------------------------------------------------------------------------

def test_slack_connector_from_env_returns_none_when_not_configured(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_CHANNEL_ID", raising=False)
    assert slack_connector_from_env() is None


def test_slack_connector_from_env_delegates_when_configured(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C123")

    def handler(req, timeout=None):  # noqa: ARG001
        return _json_response({"ok": True})

    _install_fake_urlopen(monkeypatch, handler)
    connector = slack_connector_from_env()
    assert isinstance(connector, SlackInboxConnector)
    assert connector.channel_id == "C123"


def test_create_slack_connector_close_is_a_safe_noop(monkeypatch):
    _install_fake_urlopen(monkeypatch, _auth_ok_handler())
    connector = create_slack_connector("xoxb-test", "C123")
    connector.close()  # must not raise -- stateless HTTP, nothing to tear down
