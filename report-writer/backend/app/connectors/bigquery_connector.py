"""
BigQuery connector — written to the documented `google-cloud-bigquery` API.

UNVERIFIED IN THIS SESSION: no live GCP project/credentials available in this
sandbox. Driver calls follow the real documented interface, not guessed, but
this has not been executed against an actual project. Requires
`pip install google-cloud-bigquery pandas-gbq db-dtypes`.
"""
from __future__ import annotations

import pandas as pd

from .base import ConnectorError


class BigQueryConnector:
    def __init__(self, project_id: str, dataset: str, credentials_path: str | None = None):
        try:
            from google.cloud import bigquery
        except ImportError as exc:
            raise ConnectorError(
                "google-cloud-bigquery is not installed. Run: pip install google-cloud-bigquery db-dtypes"
            ) from exc
        try:
            self._client = (
                bigquery.Client.from_service_account_json(credentials_path, project=project_id)
                if credentials_path
                else bigquery.Client(project=project_id)
            )
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"Could not create BigQuery client: {exc}") from exc
        self._dataset = dataset
        self._project_id = project_id

    def list_tables(self) -> list[str]:
        try:
            return [t.table_id for t in self._client.list_tables(self._dataset)]
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"Could not list tables: {exc}") from exc

    def list_columns(self, table: str) -> list[dict]:
        ref = f"{self._project_id}.{self._dataset}.{table}"
        try:
            schema = self._client.get_table(ref).schema
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"Could not describe table {ref!r}: {exc}") from exc
        return [{"name": f.name, "type": f.field_type} for f in schema]

    def run_query(self, sql: str) -> pd.DataFrame:
        try:
            return self._client.query(sql).to_dataframe()
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"Query failed: {exc}") from exc

    def close(self) -> None:
        self._client.close()
