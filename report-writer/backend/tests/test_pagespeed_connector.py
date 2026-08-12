"""
Tests for app/connectors/pagespeed_connector.py -- the one Google-API
connector needing no OAuth/service account, so no get_access_token faking is
needed here, just the PageSpeed Insights HTTP response.
"""
import json
import urllib.error
from io import BytesIO

import pytest

from app.connectors.base import ConnectorError
from app.connectors.pagespeed_connector import PageSpeedConnector


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _lighthouse_payload(speed_index_ms, viewport_score):
    audits = {"speed-index": {"numericValue": speed_index_ms}}
    if viewport_score is not None:
        audits["viewport"] = {"score": viewport_score}
    return {"lighthouseResult": {"audits": audits}}


def test_requires_at_least_one_url():
    with pytest.raises(ConnectorError, match="at least one URL"):
        PageSpeedConnector(urls=[])


def test_list_tables_and_columns():
    connector = PageSpeedConnector(urls=["https://example.com/"])
    assert connector.list_tables() == ["pagespeed_audit"]
    names = {c["name"] for c in connector.list_columns("pagespeed_audit")}
    assert names == {"url", "load_time_ms", "mobile_friendly"}


def test_run_query_audits_each_url_once(monkeypatch):
    responses = [_lighthouse_payload(1200.0, 1.0), _lighthouse_payload(3400.0, 0.0)]
    call_count = {"n": 0}

    def handler(req, timeout=None):  # noqa: ARG001
        resp = responses[call_count["n"]]
        call_count["n"] += 1
        return _FakeResponse(json.dumps(resp).encode())

    monkeypatch.setattr("app.connectors.pagespeed_connector.urllib.request.urlopen", handler)
    connector = PageSpeedConnector(urls=["https://example.com/a", "https://example.com/b"])
    df = connector.run_query(
        "SELECT url AS url, load_time_ms AS load_time_ms, mobile_friendly AS mobile_friendly FROM pagespeed_audit"
    )

    assert call_count["n"] == 2
    assert df.iloc[0]["url"] == "https://example.com/a"
    assert df.iloc[0]["load_time_ms"] == 1200.0
    assert bool(df.iloc[0]["mobile_friendly"]) is True
    assert bool(df.iloc[1]["mobile_friendly"]) is False


def test_api_key_included_when_provided(monkeypatch):
    seen = {}

    def handler(req, timeout=None):  # noqa: ARG001
        seen["url"] = req.full_url
        return _FakeResponse(json.dumps(_lighthouse_payload(1000.0, 1.0)).encode())

    monkeypatch.setattr("app.connectors.pagespeed_connector.urllib.request.urlopen", handler)
    connector = PageSpeedConnector(urls=["https://example.com/"], api_key="test-key-123")
    connector.run_query("SELECT url AS url FROM pagespeed_audit")
    assert "key=test-key-123" in seen["url"]


def test_no_api_key_omits_key_param(monkeypatch):
    seen = {}

    def handler(req, timeout=None):  # noqa: ARG001
        seen["url"] = req.full_url
        return _FakeResponse(json.dumps(_lighthouse_payload(1000.0, 1.0)).encode())

    monkeypatch.setattr("app.connectors.pagespeed_connector.urllib.request.urlopen", handler)
    connector = PageSpeedConnector(urls=["https://example.com/"])
    connector.run_query("SELECT url AS url FROM pagespeed_audit")
    assert "key=" not in seen["url"]


def test_fetch_only_called_once_across_multiple_queries(monkeypatch):
    calls = []

    def handler(req, timeout=None):  # noqa: ARG001
        calls.append(1)
        return _FakeResponse(json.dumps(_lighthouse_payload(1000.0, 1.0)).encode())

    monkeypatch.setattr("app.connectors.pagespeed_connector.urllib.request.urlopen", handler)
    connector = PageSpeedConnector(urls=["https://example.com/"])
    connector.run_query("SELECT url AS url FROM pagespeed_audit")
    connector.run_query("SELECT url AS url FROM pagespeed_audit")
    assert len(calls) == 1


def test_missing_viewport_audit_yields_null_mobile_friendly(monkeypatch):
    def handler(req, timeout=None):  # noqa: ARG001
        return _FakeResponse(json.dumps(_lighthouse_payload(900.0, None)).encode())

    monkeypatch.setattr("app.connectors.pagespeed_connector.urllib.request.urlopen", handler)
    connector = PageSpeedConnector(urls=["https://example.com/"])
    df = connector.run_query("SELECT mobile_friendly AS mobile_friendly FROM pagespeed_audit")
    assert df.iloc[0]["mobile_friendly"] is None


def test_api_error_raises_connector_error(monkeypatch):
    def handler(req, timeout=None):  # noqa: ARG001
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", None,
            BytesIO(b'{"error":{"message":"invalid url"}}'),
        )

    monkeypatch.setattr("app.connectors.pagespeed_connector.urllib.request.urlopen", handler)
    connector = PageSpeedConnector(urls=["https://not-a-real-url"])
    with pytest.raises(ConnectorError, match="invalid url"):
        connector.run_query("SELECT url AS url FROM pagespeed_audit")


def test_close_is_a_safe_noop():
    PageSpeedConnector(urls=["https://example.com/"]).close()
