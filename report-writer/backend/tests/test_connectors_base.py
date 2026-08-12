"""
Tests for app/connectors/base.py's parse_select_as()/select_columns_for_query()
-- the shared helpers that let an API-backed "connector" (GSC, GA4,
PageSpeed) answer run_query() against the exact `SELECT real AS alias, ...
FROM table` shape sql_source.py's _select_mapped_columns always generates,
without needing a real SQL engine.
"""
import pandas as pd
import pytest

from app.connectors.base import ConnectorError, parse_select_as, select_columns_for_query


def test_parse_select_as_single_column():
    table, mapping = parse_select_as("SELECT url AS url FROM search_analytics")
    assert table == "search_analytics"
    assert mapping == {"url": "url"}


def test_parse_select_as_multiple_columns_with_renames():
    table, mapping = parse_select_as(
        "SELECT real_clicks AS clicks_28d, real_ctr AS ctr FROM pagespeed_audit"
    )
    assert table == "pagespeed_audit"
    assert mapping == {"real_clicks": "clicks_28d", "real_ctr": "ctr"}


def test_parse_select_as_rejects_unsupported_shape():
    with pytest.raises(ConnectorError, match="Unsupported query shape"):
        parse_select_as("DROP TABLE search_analytics")


def test_parse_select_as_rejects_no_alias_columns():
    with pytest.raises(ConnectorError, match="No 'real AS alias'"):
        parse_select_as("SELECT url FROM search_analytics")


def test_select_columns_for_query_renames_and_orders():
    df = pd.DataFrame({"url": ["/a", "/b"], "clicks_28d": [10, 3], "impressions_28d": [100, 40]})
    out = select_columns_for_query(df, "SELECT clicks_28d AS clicks_28d, url AS url FROM search_analytics")
    assert list(out.columns) == ["clicks_28d", "url"]
    assert out["url"].tolist() == ["/a", "/b"]


def test_select_columns_for_query_fills_missing_real_column_with_nulls():
    df = pd.DataFrame({"url": ["/a", "/b"]})
    out = select_columns_for_query(df, "SELECT url AS url, avg_position AS avg_position FROM search_analytics")
    assert out["avg_position"].isna().all()
    assert len(out) == 2
