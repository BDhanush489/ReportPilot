"""
PageSpeed Insights — the one connector here needing no OAuth/service account
at all, just an API key (config["api_key"], or PAGESPEED_API_KEY as an env
fallback). In principle the API allows light anonymous use with no key, but
in practice the unauthenticated global quota is commonly already exhausted
(confirmed live in this environment — a keyless call returns HTTP 429
"Quota exceeded ... Queries per day"), so treat a key as required. It's a
free, instant step: Google Cloud Console -> APIs & Services -> enable
"PageSpeed Insights API" -> Credentials -> Create API key. No OAuth consent
screen, no service account, no review — see
https://developers.google.com/speed/docs/insights/v5/get-started.

Unlike GSC/GA4 (one API call covers every row), PageSpeed audits one URL per
call, so `urls` — the pages to audit — must be supplied explicitly in the
connector config rather than discovered. A natural pairing is onboarding GSC
first (its real top URLs) and passing that same list here, but this
connector works standalone off any URL list.

Same list_tables()/list_columns()/run_query() shape as connectors/base.py's
Connector protocol; internal column names equal data_context.py's canonical
"seo" field names exactly (url, load_time_ms, mobile_friendly — the subset
PageSpeed can actually answer).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

from .base import ConnectorError, select_columns_for_query

API_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

_COLUMNS = [
    {"name": "url", "type": "string"},
    {"name": "load_time_ms", "type": "float"},
    {"name": "mobile_friendly", "type": "boolean"},
]


class PageSpeedConnector:
    def __init__(self, urls: list[str], api_key: str | None = None, strategy: str = "mobile"):
        if not urls:
            raise ConnectorError("PageSpeedConnector needs at least one URL to audit.")
        self.urls = urls
        self.api_key = api_key
        self.strategy = strategy
        self._df: pd.DataFrame | None = None

    def list_tables(self) -> list[str]:
        return ["pagespeed_audit"]

    def list_columns(self, table: str) -> list[dict]:  # noqa: ARG002 -- one fixed logical table
        return list(_COLUMNS)

    def _audit_one(self, url: str) -> dict:
        params = {"url": url, "strategy": self.strategy, "category": "PERFORMANCE"}
        if self.api_key:
            params["key"] = self.api_key
        full_url = f"{API_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(full_url)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ConnectorError(f"PageSpeed Insights request for {url!r} failed ({exc.code}): {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ConnectorError(f"PageSpeed Insights request for {url!r} failed: {exc}") from exc

        audits = payload.get("lighthouseResult", {}).get("audits", {})
        speed_index = audits.get("speed-index", {}).get("numericValue")
        viewport_score = audits.get("viewport", {}).get("score")
        return {
            "url": url,
            "load_time_ms": speed_index,
            "mobile_friendly": bool(viewport_score) if viewport_score is not None else None,
        }

    def _fetch(self) -> pd.DataFrame:
        if self._df is None:
            self._df = pd.DataFrame([self._audit_one(u) for u in self.urls],
                                     columns=[c["name"] for c in _COLUMNS])
        return self._df

    def run_query(self, sql: str) -> pd.DataFrame:
        return select_columns_for_query(self._fetch(), sql)

    def close(self) -> None:
        pass
