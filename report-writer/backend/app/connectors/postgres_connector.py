"""
Postgres connector — written to the documented `psycopg2` API.

UNVERIFIED IN THIS SESSION: there is no live Postgres instance or credentials
available in this sandbox. The SQL and driver calls below follow psycopg2's
real, documented interface (not guessed), but this file has not been executed
against an actual database. Treat it as "ready to test" rather than "tested."
Requires `pip install psycopg2-binary` (not yet added to requirements.txt —
add it if/when this connector is actually wired up against a real instance).
"""
from __future__ import annotations

import pandas as pd

from .base import ConnectorError


class PostgresConnector:
    def __init__(self, dsn: str):
        """dsn: e.g. 'postgresql://user:password@host:5432/dbname'"""
        try:
            import psycopg2

        except ImportError as exc:
            raise ConnectorError(
                "psycopg2-binary is not installed. Run: pip install psycopg2-binary"
            ) from exc
        
        try:
            self._conn = psycopg2.connect(dsn)
        
        except psycopg2.OperationalError as exc:
            raise ConnectorError(f"Could not connect to Postgres: {exc}") from exc

    def list_tables(self) -> list[str]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
            )
            return [r[0] for r in cur.fetchall()]

    def list_columns(self, table: str) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = %s",
                (table,),
            )
            return [{"name": r[0], "type": r[1]} for r in cur.fetchall()]

    def run_query(self, sql: str) -> pd.DataFrame:
        try:
            return pd.read_sql_query(sql, self._conn)
        except Exception as exc:  # noqa: BLE001 - psycopg2 raises several distinct error classes
            raise ConnectorError(f"Query failed: {exc}") from exc

    def close(self) -> None:
        self._conn.close()
