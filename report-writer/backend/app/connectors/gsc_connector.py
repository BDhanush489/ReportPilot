"""
Google Search Console — a real SEO connector, not a monthly CSV export.

Auth: a Google Cloud service account (unrelated to app/auth.py's own "Sign in
with Google" login — see google_auth.py's docstring). Create one in Google
Cloud Console -> IAM & Admin -> Service Accounts, download its JSON key, then
add its `client_email` as a user on the target property in Search Console
(Settings -> Users and permissions -> Add user — "Restricted" access is
enough, this connector only ever reads).

Answers list_tables()/list_columns()/run_query() in the same shape a real
SQL warehouse connector would (see connectors/base.py's Connector protocol),
so the existing onboarding pipeline (data_context.discover_and_propose,
sql_source.py, main.py's /api/data-sources/* routes) needs zero changes to
support it — run_query() just serves one already-fetched DataFrame instead
of issuing real SQL. Internal column names are chosen to equal
data_context.py's canonical "seo" field names exactly, so the column-mapping
step becomes a trivial identity match instead of needing the AI/fuzzy-match
path at all.

Only a subset of the canonical "seo" fields (url, clicks_28d, impressions_28d,
ctr, avg_position) has a real GSC equivalent — the rest (status_code,
is_indexable, load_time_ms, ...) are left unmapped, same as any source that
doesn't cover every canonical field; normalize_seo_audit() already handles
that via its default-filling. Pair with pagespeed_connector.py for
load_time_ms/mobile_friendly on the same URLs.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

import pandas as pd

from .base import ConnectorError, select_columns_for_query
from .google_auth import get_access_token

SEARCH_ANALYTICS_URL = "https://www.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"

# GSC's data typically isn't finalized until 2-3 days after the fact --
# defaulting the window to end 3 days ago avoids silently reporting a
# partial, still-being-backfilled final day as if it were complete.
_LAG_DAYS = 3
_WINDOW_DAYS = 28

_COLUMNS = [
    {"name": "url", "type": "string"},
    {"name": "clicks_28d", "type": "integer"},
    {"name": "impressions_28d", "type": "integer"},
    {"name": "ctr", "type": "float"},
    {"name": "avg_position", "type": "float"},
]


class GSCConnector:
    def __init__(self, service_account_info: dict, site_url: str,
                 start_date: str | None = None, end_date: str | None = None, row_limit: int = 1000):
        self.site_url = site_url
        self.row_limit = row_limit
        end = date.fromisoformat(end_date) if end_date else date.today() - timedelta(days=_LAG_DAYS)
        start = date.fromisoformat(start_date) if start_date else end - timedelta(days=_WINDOW_DAYS - 1)
        self.start_date, self.end_date = start.isoformat(), end.isoformat()
        self._token = get_access_token(service_account_info, SCOPE)
        self._df: pd.DataFrame | None = None  # fetched lazily, once, on first run_query()

    def list_tables(self) -> list[str]:
        return ["search_analytics"]

    def list_columns(self, table: str) -> list[dict]:  # noqa: ARG002 -- one fixed logical table
        return list(_COLUMNS)

    def _fetch(self) -> pd.DataFrame:
        if self._df is not None:
            return self._df
        url = SEARCH_ANALYTICS_URL.format(site=urllib.parse.quote(self.site_url, safe=""))
        body = json.dumps({
            "startDate": self.start_date, "endDate": self.end_date,
            "dimensions": ["page"], "rowLimit": self.row_limit,
        }).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": f"Bearer {self._token}", "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ConnectorError(f"Search Console API request failed ({exc.code}): {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ConnectorError(f"Search Console API request failed: {exc}") from exc

        rows = payload.get("rows", [])
        self._df = pd.DataFrame([{
            "url": r["keys"][0],
            "clicks_28d": r["clicks"],
            "impressions_28d": r["impressions"],
            "ctr": r["ctr"],
            "avg_position": r["position"],
        } for r in rows], columns=[c["name"] for c in _COLUMNS])
        return self._df

    def run_query(self, sql: str) -> pd.DataFrame:
        return select_columns_for_query(self._fetch(), sql)

    def close(self) -> None:
        pass  # stateless HTTP calls per-request -- nothing persistent to tear down
