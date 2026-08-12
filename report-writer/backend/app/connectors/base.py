"""
Common interface every warehouse/database connector implements.

Nothing in this file computes a metric — connectors only ever return raw rows
as a DataFrame. app/metrics.py (unchanged) does every aggregation, so pulling
data from a warehouse instead of a CSV upload never touches the "AI never
computes a number" invariant: the source of the rows changes, the pandas math
that turns rows into numbers does not.
"""
from __future__ import annotations

import re
from typing import Protocol

import pandas as pd


class Connector(Protocol):
    """Every concrete connector (SQLite, Postgres, Snowflake, BigQuery,
    Databricks, ...) implements exactly this surface."""

    def list_tables(self) -> list[str]:
        """Every table/view name visible to the connected credentials."""
        ...

    def list_columns(self, table: str) -> list[dict]:
        """[{"name": str, "type": str}, ...] for one table — used by
        data_context.py to propose a canonical-field mapping."""
        ...

    def run_query(self, sql: str) -> pd.DataFrame:
        """Execute a read-only SQL query, return the result as a DataFrame."""
        ...

    def close(self) -> None:
        ...


class ConnectorError(Exception):
    """Raised when a connector can't reach or authenticate to its source."""


_SELECT_RE = re.compile(r"^\s*SELECT\s+(.*?)\s+FROM\s+(\w+)\s*$", re.IGNORECASE | re.DOTALL)
_COLUMN_ALIAS_RE = re.compile(r"(\w+)\s+AS\s+(\w+)", re.IGNORECASE)


def parse_select_as(sql: str) -> tuple[str, dict[str, str]]:
    """Parses the narrow `SELECT real AS alias, ... FROM table` shape that
    sql_source.py's _select_mapped_columns always generates (identifiers are
    validated alnum/underscore there and never quoted, so this regex is safe
    against the only inputs that ever reach it).

    Used by connectors whose "tables" are really a single API call rather
    than literal SQL (see gsc_connector.py, ga4_connector.py,
    pagespeed_connector.py) — they still answer list_tables()/list_columns()
    like a real warehouse so the existing onboarding/column-mapping pipeline
    in data_context.py and sql_source.py needs zero changes to support them;
    only run_query() differs, serving an already-fetched DataFrame instead
    of issuing real SQL.

    Returns (table, {real_col: alias})."""
    match = _SELECT_RE.match(sql)
    if not match:
        raise ConnectorError(f"Unsupported query shape: {sql!r}")
    columns_part, table = match.groups()
    mapping = dict(_COLUMN_ALIAS_RE.findall(columns_part))
    if not mapping:
        raise ConnectorError(f"No 'real AS alias' columns found in query: {sql!r}")
    return table, mapping


def select_columns_for_query(df: pd.DataFrame, sql: str) -> pd.DataFrame:
    """Applies parse_select_as()'s real->alias mapping to an already-fetched
    DataFrame, renaming/reordering columns to exactly what the caller's SQL
    asked for. A real column the API simply didn't return becomes an
    all-null column — the same "unmapped field left null, filled in by
    normalize_*'s defaults" contract a CSV upload missing a column already
    gets, so callers downstream of sql_source.py don't need to know or care
    whether a column came from a warehouse table or an API response."""
    _, mapping = parse_select_as(sql)
    out = {}
    for real, alias in mapping.items():
        out[alias] = df[real] if real in df.columns else pd.Series([None] * len(df), dtype="object")
    return pd.DataFrame(out)
