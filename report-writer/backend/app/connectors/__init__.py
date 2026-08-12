"""
Connector registry — one canonical way to go from a stored connection config
to a live Connector, regardless of which warehouse or API it points at.

"sqlite" and "pagespeed" are verified end-to-end in this environment —
sqlite against a real file, pagespeed against the live public API (no
credentials needed). "gsc" and "ga4" are written to Google's documented REST
APIs and unit-tested against mocked HTTP (see tests/test_gsc_connector.py /
test_ga4_connector.py / test_google_auth.py), but need a real service
account + property access to verify live, same "unverified for lack of live
credentials" posture as postgres/snowflake/bigquery/databricks below.
"""
from __future__ import annotations

import os

from .base import Connector, ConnectorError

_KINDS = {"sqlite", "postgres", "snowflake", "bigquery", "databricks", "gsc", "ga4", "pagespeed"}


def create_connector(kind: str, config: dict) -> Connector:
    if kind == "sqlite":
        from .sqlite_connector import SQLiteConnector
        return SQLiteConnector(path=config["path"])
    if kind == "postgres":
        from .postgres_connector import PostgresConnector
        return PostgresConnector(dsn=config["dsn"])
    if kind == "snowflake":
        from .snowflake_connector import SnowflakeConnector
        return SnowflakeConnector(**{k: config[k] for k in
                                      ("account", "user", "password", "warehouse", "database")},
                                   schema=config.get("schema", "PUBLIC"))
    if kind == "bigquery":
        from .bigquery_connector import BigQueryConnector
        return BigQueryConnector(project_id=config["project_id"], dataset=config["dataset"],
                                  credentials_path=config.get("credentials_path"))
    if kind == "databricks":
        from .databricks_connector import DatabricksConnector
        return DatabricksConnector(**{k: config[k] for k in
                                       ("server_hostname", "http_path", "access_token", "catalog", "schema")})
    if kind == "gsc":
        from .gsc_connector import GSCConnector
        return GSCConnector(service_account_info=config["service_account_info"], site_url=config["site_url"],
                             start_date=config.get("start_date"), end_date=config.get("end_date"),
                             row_limit=config.get("row_limit", 1000))
    if kind == "ga4":
        from .ga4_connector import GA4Connector
        return GA4Connector(service_account_info=config["service_account_info"], property_id=config["property_id"],
                             start_date=config.get("start_date", "28daysAgo"),
                             end_date=config.get("end_date", "yesterday"))
    if kind == "pagespeed":
        from .pagespeed_connector import PageSpeedConnector
        return PageSpeedConnector(urls=config["urls"],
                                   api_key=config.get("api_key") or os.environ.get("PAGESPEED_API_KEY"),
                                   strategy=config.get("strategy", "mobile"))
    raise ConnectorError(f"Unknown connector kind {kind!r}. Supported: {sorted(_KINDS)}")
