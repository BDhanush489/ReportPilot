"""
Tests for app/connectors/ga4_connector.py. Same pattern as
test_gsc_connector.py -- get_access_token() is faked, only the GA4 Data API
HTTP response is mocked.
"""
import json
import urllib.error
from io import BytesIO

import pytest

from app.connectors.base import ConnectorError
from app.connectors.ga4_connector import GA4Connector


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
    monkeypatch.setattr("app.connectors.ga4_connector.get_access_token", lambda info, scope: "fake-token")


def _make_connector(property_id="123456", **kwargs):
    return GA4Connector(service_account_info={"client_email": "x", "private_key": "y"},
                         property_id=property_id, **kwargs)


def test_list_columns_covers_every_canonical_analytics_field():
    connector = _make_connector()
    names = {c["name"] for c in connector.list_columns("ga4_report")}
    assert names == {"date", "channel_group", "device_category", "sessions", "new_users",
                      "engaged_sessions", "conversions", "revenue_usd", "bounce_rate",
                      "avg_session_duration_sec"}


def test_run_query_maps_ga4_rows_including_date_reformatting(monkeypatch):
    payload = {
        "rows": [{
            "dimensionValues": [{"value": "20260715"}, {"value": "Organic Search"}, {"value": "mobile"}],
            "metricValues": [{"value": "120"}, {"value": "40"}, {"value": "95"}, {"value": "6"},
                              {"value": "812.50"}, {"value": "0.31"}, {"value": "58.4"}],
        }]
    }

    def handler(req, timeout=None):  # noqa: ARG001
        assert req.full_url == "https://analyticsdata.googleapis.com/v1beta/properties/123456:runReport"
        assert req.headers["Authorization"] == "Bearer fake-token"
        return _FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr("app.connectors.ga4_connector.urllib.request.urlopen", handler)
    connector = _make_connector()
    df = connector.run_query(
        "SELECT date AS date, channel_group AS channel_group, sessions AS sessions, "
        "revenue_usd AS revenue_usd FROM ga4_report"
    )

    assert df.iloc[0]["date"] == "2026-07-15"
    assert df.iloc[0]["channel_group"] == "Organic Search"
    assert df.iloc[0]["sessions"] == 120.0
    assert df.iloc[0]["revenue_usd"] == 812.50


def test_fetch_only_called_once_across_multiple_queries(monkeypatch):
    calls = []

    def handler(req, timeout=None):  # noqa: ARG001
        calls.append(1)
        return _FakeResponse(json.dumps({"rows": []}).encode())

    monkeypatch.setattr("app.connectors.ga4_connector.urllib.request.urlopen", handler)
    connector = _make_connector()
    connector.run_query("SELECT date AS date FROM ga4_report")
    connector.run_query("SELECT date AS date FROM ga4_report")
    assert len(calls) == 1


def test_api_error_raises_connector_error(monkeypatch):
    def handler(req, timeout=None):  # noqa: ARG001
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", None,
            BytesIO(b'{"error":{"message":"invalid property"}}'),
        )

    monkeypatch.setattr("app.connectors.ga4_connector.urllib.request.urlopen", handler)
    connector = _make_connector()
    with pytest.raises(ConnectorError, match="invalid property"):
        connector.run_query("SELECT date AS date FROM ga4_report")


def test_close_is_a_safe_noop():
    _make_connector().close()
