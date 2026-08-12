"""
SQLite connector — stands in for "a client's warehouse" in this environment.

This is the ONE connector actually exercised end-to-end in this session: there
are no live Snowflake/BigQuery/Databricks/Postgres credentials available here,
so this file is what proves the connector abstraction + onboarding + SQL-driven
metrics path really works, rather than being a paper design. See
postgres_connector.py / snowflake_connector.py / bigquery_connector.py /
databricks_connector.py for the same interface written to each vendor's
documented driver, unverified without real credentials.
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from .base import ConnectorError


class SQLiteConnector:
    def __init__(self, path: str):
        try:
            self._conn = sqlite3.connect(path, check_same_thread=False)
        except sqlite3.Error as exc:
            raise ConnectorError(f"Could not open SQLite database at {path!r}: {exc}") from exc

    def list_tables(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return [r[0] for r in rows]

    def list_columns(self, table: str) -> list[dict]:
        rows = self._conn.execute(f"PRAGMA table_info({_quote_ident(table)})").fetchall()
        # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
        return [{"name": r[1], "type": r[2] or "TEXT"} for r in rows]

    def run_query(self, sql: str) -> pd.DataFrame:
        try:
            return pd.read_sql_query(sql, self._conn)
        except (sqlite3.Error, pd.errors.DatabaseError) as exc:
            raise ConnectorError(f"Query failed: {exc}") from exc

    def close(self) -> None:
        self._conn.close()


def _quote_ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"Unsafe table name: {name!r}")
    return f'"{name}"'
