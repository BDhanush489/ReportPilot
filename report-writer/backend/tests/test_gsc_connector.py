"""
Tests for app/connectors/gsc_connector.py. get_access_token() itself is
tested in test_google_auth.py -- here it's monkeypatched to a canned token so
each test only has to fake the Search Console HTTP response, not also sign a
real JWT.
"""
import json
import urllib.error
from datetime import date, timedelta
from io import BytesIO

import pytest

from app.connectors.base import ConnectorError
from app.connectors.gsc_connector import GSCConnector


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _fake_token(monkeypatch):
    monkeypatch.setattr("app.connectors.gsc_connector.get_access_token", lambda info, scope: "fake-token")


def _make_connector(**kwargs):
    return GSCConnector(service_account_info={"client_email": "x", "private_key": "y"},
                         site_url="https://example.com/", **kwargs)


def test_default_date_window_ends_3_days_ago_spans_28_days():
    connector = _make_connector()
    expected_end = date.today() - timedelta(days=3)
    expected_start = expected_end - timedelta(days=27)
    assert connector.end_date == expected_end.isoformat()
    assert connector.start_date == expected_start.isoformat()


def test_explicit_date_window_is_honored():
    connector = _make_connector(start_date="2026-06-01", end_date="2026-06-28")
    assert connector.start_date == "2026-06-01"
    assert connector.end_date == "2026-06-28"


def test_list_tables_and_columns():
    connector = _make_connector()
    assert connector.list_tables() == ["search_analytics"]
    names = {c["name"] for c in connector.list_columns("search_analytics")}
    assert names == {"url", "clicks_28d", "impressions_28d", "ctr", "avg_position"}


def test_run_query_maps_gsc_rows_to_canonical_columns(monkeypatch):
    rows = [
        {"keys": ["https://example.com/a"], "clicks": 10, "impressions": 200, "ctr": 0.05, "position": 4.2},
        {"keys": ["https://example.com/b"], "clicks": 3, "impressions": 90, "ctr": 0.033, "position": 11.7},
    ]

    def handler(req, timeout=None):  # noqa: ARG001
        assert req.full_url.startswith("https://www.googleapis.com/webmasters/v3/sites/")
        assert req.headers["Authorization"] == "Bearer fake-token"
        return _FakeResponse(json.dumps({"rows": rows}).encode())

    monkeypatch.setattr("app.connectors.gsc_connector.urllib.request.urlopen", handler)
    connector = _make_connector(start_date="2026-07-01", end_date="2026-07-28")
    df = connector.run_query(
        "SELECT url AS url, clicks_28d AS clicks_28d, avg_position AS avg_position FROM search_analytics"
    )

    assert list(df.columns) == ["url", "clicks_28d", "avg_position"]
    assert df.iloc[0]["url"] == "https://example.com/a"
    assert df.iloc[0]["clicks_28d"] == 10
    assert df.iloc[1]["avg_position"] == 11.7


def test_fetch_only_called_once_across_multiple_queries(monkeypatch):
    calls = []

    def handler(req, timeout=None):  # noqa: ARG001
        calls.append(1)
        return _FakeResponse(json.dumps({"rows": []}).encode())

    monkeypatch.setattr("app.connectors.gsc_connector.urllib.request.urlopen", handler)
    connector = _make_connector()
    connector.run_query("SELECT url AS url FROM search_analytics")
    connector.run_query("SELECT url AS url FROM search_analytics")
    assert len(calls) == 1


def test_empty_rows_still_produce_the_right_columns(monkeypatch):
    def handler(req, timeout=None):  # noqa: ARG001
        return _FakeResponse(json.dumps({"rows": []}).encode())

    monkeypatch.setattr("app.connectors.gsc_connector.urllib.request.urlopen", handler)
    connector = _make_connector()
    df = connector.run_query("SELECT url AS url, ctr AS ctr FROM search_analytics")
    assert list(df.columns) == ["url", "ctr"]
    assert len(df) == 0


def test_api_error_raises_connector_error(monkeypatch):
    def handler(req, timeout=None):  # noqa: ARG001
        raise urllib.error.HTTPError(
            req.full_url, 403, "Forbidden", None,
            BytesIO(b'{"error":{"message":"caller does not have permission"}}'),
        )

    monkeypatch.setattr("app.connectors.gsc_connector.urllib.request.urlopen", handler)
    connector = _make_connector()
    with pytest.raises(ConnectorError, match="permission"):
        connector.run_query("SELECT url AS url FROM search_analytics")


def test_close_is_a_safe_noop():
    _make_connector().close()
