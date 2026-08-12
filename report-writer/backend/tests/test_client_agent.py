"""
Tests for client_agent/agent.py (Ingestion Mode 1's standalone Windows CLI).

DPAPI round-trip and config save/load are exercised for real (this test
suite already runs on a real Windows machine, per the smoke test that
proved CryptProtectData/CryptUnprotectData work here before agent.py was
built) -- not mocked, since the whole point of DPAPI is a real OS-level
guarantee a mock can't verify. IMAP/Slack fetch and the HTTP submit/poll
loop are mocked at their respective boundaries (imaplib, requests), same
posture as tests/test_email_source.py and tests/test_slack_source.py:
no live mailbox, workspace, or hosted backend is available here.
"""
import json
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pytest
import requests

from client_agent import agent


# ---------------------------------------------------------------------------
# DPAPI + config: real, not mocked -- this machine really is Windows.
# ---------------------------------------------------------------------------

def test_dpapi_round_trips_arbitrary_bytes():
    secret = b'{"password": "correct-horse-battery-staple", "unicode": "caf\xc3\xa9"}'
    ciphertext = agent.dpapi_protect(secret)
    assert ciphertext != secret
    assert agent.dpapi_unprotect(ciphertext) == secret


def test_config_round_trips_through_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(agent, "CONFIG_PATH", tmp_path / "config.bin")
    config = {"source_kind": "gmail", "username": "u@gmail.com", "password": "app-pw",
              "api_base_url": "https://reports.example.com"}
    path = agent.save_config(config)
    assert path.exists()
    # The bytes on disk must never be the plaintext JSON.
    assert b"app-pw" not in path.read_bytes()
    assert agent.load_config() == config


def test_load_config_without_setup_raises_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(agent, "CONFIG_PATH", tmp_path / "missing.bin")
    with pytest.raises(agent.AgentError, match="run with `setup`"):
        agent.load_config()


def test_corrupted_config_raises_a_clear_error_not_a_raw_traceback(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "AGENT_DIR", tmp_path)
    config_path = tmp_path / "config.bin"
    config_path.write_bytes(b"not a real DPAPI blob")
    monkeypatch.setattr(agent, "CONFIG_PATH", config_path)
    with pytest.raises(agent.AgentError, match="Could not decrypt"):
        agent.load_config()


# ---------------------------------------------------------------------------
# guess_upload_slot / slot_files
# ---------------------------------------------------------------------------

def test_guess_upload_slot_matches_known_hints():
    assert agent.guess_upload_slot("web_analytics_export.csv") == "analytics"
    assert agent.guess_upload_slot("seo_audit_june.csv") == "seo"
    assert agent.guess_upload_slot("sales_pipeline.xlsx") == "sales"
    assert agent.guess_upload_slot("random_document.csv") is None


def test_slot_files_first_match_wins_rest_go_unmatched():
    attachments = [
        ("web_analytics_v1.csv", b"first"),
        ("web_analytics_v2.csv", b"second"),
        ("random.csv", b"third"),
    ]
    slotted, unmatched = agent.slot_files(attachments)
    assert slotted["analytics"] == ("web_analytics_v1.csv", b"first")
    assert unmatched == ["web_analytics_v2.csv", "random.csv"]


# ---------------------------------------------------------------------------
# IMAP fetch: pure MIME parsing (duplicated from email_source.py) + a fake
# imaplib.IMAP4_SSL for the connection itself.
# ---------------------------------------------------------------------------

def _message_with_attachment(filename: str, content: bytes) -> bytes:
    msg = MIMEMultipart()
    msg["Subject"] = "Weekly export"
    msg.attach(MIMEText("See attached.", "plain"))
    part = MIMEApplication(content, Name=filename)
    part["Content-Disposition"] = f'attachment; filename="{filename}"'
    msg.attach(part)
    return msg.as_bytes()


class _FakeIMAP:
    def __init__(self, raw_messages: dict[bytes, bytes]):
        self._raw_messages = raw_messages
        self.logged_in = False
        self.closed = False
        self.logged_out = False

    def login(self, username, password):
        if password == "wrong":
            import imaplib
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        self.logged_in = True

    def select(self, mailbox, readonly=True):
        return "OK", [b"1"]

    def search(self, charset, criteria):
        return "OK", [b" ".join(self._raw_messages.keys())]

    def fetch(self, msg_id, parts):
        return "OK", [(b"1 (RFC822 {n})", self._raw_messages[msg_id])]

    def close(self):
        self.closed = True

    def logout(self):
        self.logged_out = True


def test_fetch_from_imap_returns_real_attachment_bytes(monkeypatch):
    raw = _message_with_attachment("web_analytics.csv", b"date,sessions\n2026-01-01,100\n")
    fake = _FakeIMAP({b"1": raw})
    monkeypatch.setattr(agent.imaplib, "IMAP4_SSL", lambda host: fake)

    config = {"source_kind": "gmail", "username": "u@gmail.com", "password": "app-pw"}
    results = agent.fetch_from_imap(config)

    assert results == [("web_analytics.csv", b"date,sessions\n2026-01-01,100\n")]
    assert fake.closed and fake.logged_out  # connection always torn down


def test_fetch_from_imap_bad_login_raises_agent_error(monkeypatch):
    fake = _FakeIMAP({})
    monkeypatch.setattr(agent.imaplib, "IMAP4_SSL", lambda host: fake)
    config = {"source_kind": "gmail", "username": "u@gmail.com", "password": "wrong"}
    with pytest.raises(agent.AgentError, match="IMAP login failed"):
        agent.fetch_from_imap(config)


def test_fetch_from_imap_rejects_unknown_provider():
    with pytest.raises(agent.AgentError, match="Unknown IMAP provider"):
        agent.fetch_from_imap({"source_kind": "yahoo", "username": "u", "password": "p"})


# ---------------------------------------------------------------------------
# Slack fetch: mocked at requests boundary.
# ---------------------------------------------------------------------------

def test_fetch_from_slack_downloads_matching_files(monkeypatch):
    files_payload = {"ok": True, "files": [
        {"name": "sales_pipeline.xlsx", "url_private_download": "https://files.slack.com/x.xlsx"},
        {"name": "notes.txt", "url_private_download": "https://files.slack.com/y.txt"},
    ]}

    class _Resp:
        def __init__(self, json_body=None, content=b""):
            self._json_body, self.content = json_body, content

        def json(self):
            return self._json_body

        def raise_for_status(self):
            pass

    def fake_get(url, params=None, headers=None, timeout=None):  # noqa: ARG001
        if url.endswith("files.list"):
            return _Resp(json_body=files_payload)
        assert url == "https://files.slack.com/x.xlsx"
        return _Resp(content=b"sales-bytes")

    monkeypatch.setattr(agent.requests, "get", fake_get)
    results = agent.fetch_from_slack({"bot_token": "xoxb-test", "channel_id": "C123"})
    assert results == [("sales_pipeline.xlsx", b"sales-bytes")]


def test_fetch_from_slack_raises_on_api_error(monkeypatch):
    class _Resp:
        def json(self):
            return {"ok": False, "error": "channel_not_found"}

    monkeypatch.setattr(agent.requests, "get", lambda *a, **k: _Resp())
    with pytest.raises(agent.AgentError, match="channel_not_found"):
        agent.fetch_from_slack({"bot_token": "xoxb-test", "channel_id": "C_bad"})


# ---------------------------------------------------------------------------
# submit_report / wait_for_job: mocked HTTP, real multipart field shape.
# ---------------------------------------------------------------------------

def test_submit_report_posts_the_right_fields(monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"job_id": "job-123"}

    def fake_post(url, data=None, files=None, headers=None, timeout=None):  # noqa: ARG001
        captured.update(url=url, data=data, files=files, headers=headers)
        return _Resp()

    monkeypatch.setattr(agent.requests, "post", fake_post)
    config = {"api_base_url": "https://reports.example.com/", "api_key": "secret",
              "agency_name": "Acme Co", "client_name": "Acme", "primary_color": "#111", "accent_color": "#222"}
    slotted = {"analytics": ("web_analytics.csv", b"data")}

    job_id = agent.submit_report(config, slotted)

    assert job_id == "job-123"
    assert captured["url"] == "https://reports.example.com/api/generate-report"
    assert captured["headers"] == {"X-API-Key": "secret"}
    assert captured["data"]["agency_name"] == "Acme Co"
    assert "analytics_file" in captured["files"]


def test_submit_report_raises_on_network_failure(monkeypatch):
    def fake_post(*a, **k):  # noqa: ARG001
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(agent.requests, "post", fake_post)
    with pytest.raises(agent.AgentError, match="Submitting the report"):
        agent.submit_report({"api_base_url": "https://x", "api_key": ""}, {})


def test_wait_for_job_stops_on_done_event(monkeypatch):
    events = [
        'data: {"stage": "Parsing", "status": "running", "error": null}',
        'data: {"stage": "Done", "status": "done", "error": null}',
    ]

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def iter_lines(self, decode_unicode=True):
            return iter(events)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(agent.requests, "get", lambda *a, **k: _Resp())
    result = agent.wait_for_job({"api_base_url": "https://x", "api_key": ""}, "job-123")
    assert result["status"] == "done"


def test_wait_for_job_surfaces_a_failed_job(monkeypatch):
    events = ['data: {"stage": "Parsing", "status": "error", "error": "bad file"}']

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def iter_lines(self, decode_unicode=True):
            return iter(events)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(agent.requests, "get", lambda *a, **k: _Resp())
    result = agent.wait_for_job({"api_base_url": "https://x", "api_key": ""}, "job-123")
    assert result == {"stage": "Parsing", "status": "error", "error": "bad file"}


# ---------------------------------------------------------------------------
# CLI wiring: setup writes a usable config; run's dry-run path never submits.
# ---------------------------------------------------------------------------

def test_cli_setup_then_run_dry_run_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(agent, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(agent, "CONFIG_PATH", tmp_path / "config.bin")
    monkeypatch.setattr(agent, "LOG_PATH", tmp_path / "agent.log")

    rc = agent.main([
        "setup", "--source", "gmail", "--username", "u@gmail.com", "--password", "app-pw",
        "--api-base-url", "https://reports.example.com",
    ])
    assert rc == 0

    raw = _message_with_attachment("web_analytics.csv", b"date,sessions\n2026-01-01,100\n")
    fake = _FakeIMAP({b"1": raw})
    monkeypatch.setattr(agent.imaplib, "IMAP4_SSL", lambda host: fake)

    def fail_if_called(*a, **k):
        raise AssertionError("dry-run must never actually submit")

    monkeypatch.setattr(agent.requests, "post", fail_if_called)

    rc = agent.main(["run", "--dry-run"])
    assert rc == 0
    assert "Dry run" in capsys.readouterr().out
