"""
GA4 (Google Analytics 4) Data API — a real analytics connector, not a CSV
export. Same auth posture as gsc_connector.py: a service-account credential
(create in Google Cloud Console, then add the service account's client_email
as a "Viewer" in the GA4 property under Admin -> Property Access Management —
no OAuth consent screen, no app review, purely machine-to-machine).

Answers list_tables()/list_columns()/run_query() in the connectors/base.py
Connector shape, so the existing onboarding pipeline needs zero changes to
support it. Internal column names equal data_context.py's canonical
"analytics" field names exactly — GA4's Data API happens to cover every one
of them directly (date, channel group, device category as dimensions;
sessions/new users/engaged sessions/conversions/revenue/bounce rate/avg
session duration as metrics), so this is a full mapping, not a partial one
like gsc_connector.py's.

Quota note (worth knowing, not something this file works around): GA4 Data
API is free within quota, but a property only allows ~10 concurrent request
tokens — a scheduler refreshing many clients back-to-back should stay
sequential rather than firing every client's report at once. scheduler.py
already runs due schedules one at a time, so this doesn't need handling
here.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pandas as pd

from .base import ConnectorError, select_columns_for_query
from .google_auth import get_access_token

RUN_REPORT_URL = "https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"
SCOPE = "https://www.googleapis.com/auth/analytics.readonly"

_API_DIMENSIONS = ["date", "sessionDefaultChannelGroup", "deviceCategory"]
_API_METRICS = ["sessions", "newUsers", "engagedSessions", "conversions",
                "totalRevenue", "bounceRate", "averageSessionDuration"]

_CANONICAL_DIMENSIONS = ["date", "channel_group", "device_category"]
_CANONICAL_METRICS = ["sessions", "new_users", "engaged_sessions", "conversions",
                       "revenue_usd", "bounce_rate", "avg_session_duration_sec"]
_ALL_COLUMNS = _CANONICAL_DIMENSIONS + _CANONICAL_METRICS


class GA4Connector:
    def __init__(self, service_account_info: dict, property_id: str,
                 start_date: str = "28daysAgo", end_date: str = "yesterday"):
        self.property_id = property_id
        self.start_date = start_date
        self.end_date = end_date
        self._token = get_access_token(service_account_info, SCOPE)
        self._df: pd.DataFrame | None = None

    def list_tables(self) -> list[str]:
        return ["ga4_report"]

    def list_columns(self, table: str) -> list[dict]:  # noqa: ARG002 -- one fixed logical table
        cols = [{"name": n, "type": "string"} for n in _CANONICAL_DIMENSIONS]
        cols += [{"name": n, "type": "float"} for n in _CANONICAL_METRICS]
        return cols

    def _fetch(self) -> pd.DataFrame:
        if self._df is not None:
            return self._df
        url = RUN_REPORT_URL.format(property_id=self.property_id)
        body = json.dumps({
            "dateRanges": [{"startDate": self.start_date, "endDate": self.end_date}],
            "dimensions": [{"name": d} for d in _API_DIMENSIONS],
            "metrics": [{"name": m} for m in _API_METRICS],
            "limit": 100000,
        }).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": f"Bearer {self._token}", "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ConnectorError(f"GA4 Data API request failed ({exc.code}): {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ConnectorError(f"GA4 Data API request failed: {exc}") from exc

        records = []
        for row in payload.get("rows", []):
            dims = [v["value"] for v in row["dimensionValues"]]
            mets = [v["value"] for v in row["metricValues"]]
            raw_date = dims[0]  # GA4 returns "date" as a bare YYYYMMDD string
            record = {
                "date": f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}",
                "channel_group": dims[1],
                "device_category": dims[2],
            }
            for name, value in zip(_CANONICAL_METRICS, mets):
                record[name] = float(value)
            records.append(record)
        self._df = pd.DataFrame(records, columns=_ALL_COLUMNS)
        return self._df

    def run_query(self, sql: str) -> pd.DataFrame:
        return select_columns_for_query(self._fetch(), sql)

    def close(self) -> None:
        pass
