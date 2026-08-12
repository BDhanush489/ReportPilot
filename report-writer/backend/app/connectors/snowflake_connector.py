"""
Snowflake connector — written to the documented `snowflake-connector-python` API.

UNVERIFIED IN THIS SESSION: no live Snowflake account/credentials available in
this sandbox. Driver calls follow the real documented interface, not guessed,
but this has not been executed against an actual account. Requires
`pip install snowflake-connector-python[pandas]`.
"""
from __future__ import annotations

import pandas as pd

from .base import ConnectorError


class SnowflakeConnector:
    def __init__(self, account: str, user: str, password: str, warehouse: str,
                 database: str, schema: str = "PUBLIC"):
        try:
            import snowflake.connector
        except ImportError as exc:
            raise ConnectorError(
                "snowflake-connector-python is not installed. "
                "Run: pip install snowflake-connector-python[pandas]"
            ) from exc
        try:
            self._conn = snowflake.connector.connect(
                account=account, user=user, password=password,
                warehouse=warehouse, database=database, schema=schema,
            )
        except Exception as exc:  # noqa: BLE001 - snowflake raises its own broad error hierarchy
            raise ConnectorError(f"Could not connect to Snowflake: {exc}") from exc

    def list_tables(self) -> list[str]:
        cur = self._conn.cursor()
        cur.execute("SHOW TABLES")
        rows = cur.fetchall()
        cur.close()
        # SHOW TABLES columns include name at index 1
        return [r[1] for r in rows]

    def list_columns(self, table: str) -> list[dict]:
        cur = self._conn.cursor()
        cur.execute(f"DESCRIBE TABLE {table}")
        rows = cur.fetchall()
        cur.close()
        # DESCRIBE TABLE columns: name, type, kind, null?, default, ...
        return [{"name": r[0], "type": r[1]} for r in rows]

    def run_query(self, sql: str) -> pd.DataFrame:
        cur = self._conn.cursor()
        try:
            cur.execute(sql)
            return cur.fetch_pandas_all()
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"Query failed: {exc}") from exc
        finally:
            cur.close()

    def close(self) -> None:
        self._conn.close()
