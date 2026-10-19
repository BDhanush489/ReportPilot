"""
Turns a saved data context (connector + column mapping) into the same shape
of DataFrame that parsers.py produces from an uploaded file — by design, the
only difference between the CSV path and the warehouse path is where the raw
rows come from. Everything downstream (parsers.py's normalize_* functions,
metrics.py, insights.py, agent.py) is completely unaware of the source.
"""
from __future__ import annotations

import pandas as pd

from . import parsers
from .connectors.base import Connector, ConnectorError


def _quote_ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ConnectorError(f"Unsafe identifier: {name!r}")
    return name


def _select_mapped_columns(connector: Connector, table: str, column_map: dict[str, str | None]) -> pd.DataFrame:
    """Builds SELECT real_col AS canonical, ... FROM table for every canonical
    field that actually has a real column mapped — unmapped fields are simply
    left out and picked up by the normalize_* functions' default-filling,
    exactly as an uploaded CSV missing a column would be."""
    mapped = {canonical: real for canonical, real in column_map.items() if real}
    if not mapped:
        raise ConnectorError(f"No columns could be mapped for table {table!r} — check the data context.")
    select_list = ", ".join(f"{_quote_ident(real)} AS {_quote_ident(canonical)}"
                             for canonical, real in mapped.items())
    sql = f"SELECT {select_list} FROM {_quote_ident(table)}"
    return connector.run_query(sql)


def _require_mapped(source_type: str, source_config: dict) -> None:
    """Fails with a message a user can act on, instead of a bare KeyError
    deep in metrics.py, when the one field a report can't do without was
    never mapped (typically: no AI provider at onboarding, and the fuzzy
    matcher couldn't pair e.g. close_date with closed_on)."""
    from .data_context import REQUIRED_FIELDS
    field = REQUIRED_FIELDS[source_type]
    if not source_config["column_map"].get(field):
        raise ValueError(
            f"The {source_type} table {source_config['table']!r} has no column mapped to "
            f"{field!r}, which every report needs. On the Data sources page, click "
            f"\"Edit mapping\" for this client and pick the column that holds it."
        )


def load_analytics_from_sql(connector: Connector, source_config: dict) -> tuple[pd.DataFrame, list[dict]]:
    _require_mapped("analytics", source_config)
    df = _select_mapped_columns(connector, source_config["table"], source_config["column_map"])
    return parsers.normalize_web_analytics(df)


def load_seo_from_sql(connector: Connector, source_config: dict) -> tuple[pd.DataFrame, list[dict]]:
    _require_mapped("seo", source_config)
    df = _select_mapped_columns(connector, source_config["table"], source_config["column_map"])
    return parsers.normalize_seo_audit(df)


def load_sales_from_sql(connector: Connector, source_config: dict) -> tuple[pd.DataFrame, pd.DataFrame | None, list[dict]]:
    _require_mapped("sales", source_config)
    df = _select_mapped_columns(connector, source_config["table"], source_config["column_map"])
    deals, issues = parsers.normalize_deals(df)
    return deals, None, issues
