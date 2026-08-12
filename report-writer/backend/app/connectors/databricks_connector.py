"""
Databricks connector — written to the documented `databricks-sql-connector` API.

UNVERIFIED IN THIS SESSION: no live Databricks workspace/credentials available
in this sandbox. Driver calls follow the real documented interface, not
guessed, but this has not been executed against an actual workspace. Requires
`pip install databricks-sql-connector`.
"""
from __future__ import annotations

import pandas as pd

from .base import ConnectorError


class DatabricksConnector:
    def __init__(self, server_hostname: str, http_path: str, access_token: str, catalog: str, schema: str):
        try:
            from databricks import sql
        except ImportError as exc:
            raise ConnectorError(
                "databricks-sql-connector is not installed. Run: pip install databricks-sql-connector"
            ) from exc
        try:
            self._conn = sql.connect(
                server_hostname=server_hostname, http_path=http_path, access_token=access_token,
                catalog=catalog, schema=schema,
            )
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"Could not connect to Databricks: {exc}") from exc
        self._catalog, self._schema = catalog, schema

    def list_tables(self) -> list[str]:
        cur = self._conn.cursor()
        try:
            cur.tables(catalog_name=self._catalog, schema_name=self._schema)
            rows = cur.fetchall()
            return [r.TABLE_NAME for r in rows]
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"Could not list tables: {exc}") from exc
        finally:
            cur.close()

    def list_columns(self, table: str) -> list[dict]:
        cur = self._conn.cursor()
        try:
            cur.columns(catalog_name=self._catalog, schema_name=self._schema, table_name=table)
            rows = cur.fetchall()
            return [{"name": r.COLUMN_NAME, "type": r.TYPE_NAME} for r in rows]
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"Could not describe table {table!r}: {exc}") from exc
        finally:
            cur.close()

    def run_query(self, sql_text: str) -> pd.DataFrame:
        cur = self._conn.cursor()
        try:
            cur.execute(sql_text)
            return cur.fetchall_arrow().to_pandas()
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"Query failed: {exc}") from exc
        finally:
            cur.close()

    def close(self) -> None:
        self._conn.close()
