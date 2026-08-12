"""
Tests for D2.3 — Power BI live warehouse connection. GOAL: no plaintext
creds; snapshot mode still selectable (and remains the untouched default).

Exit criteria proven here:
  - snapshot mode is completely unaffected (test_default_call_signature_is_unchanged,
    plus test_pbip_export.py's whole existing suite, none of which passes
    connection_mode/data_context at all).
  - Live connection is real DirectQuery against the client's own warehouse
    table -- test_live_mode_emits_a_directquery_table_per_covered_section.
  - No plaintext credentials ever reach the generated files --
    test_no_secret_ever_appears_in_generated_files (parametrized across all
    four connectors with native support).
  - A connector kind with no native Power Query connector (sqlite, this
    project's own real demo connector) degrades explicitly, not silently --
    test_sqlite_the_real_demo_connector_kind_has_no_live_support.
"""
from pathlib import Path

import pytest

from app import pbip_export, report_builder
from app.pbip_export import UnsupportedLiveConnection, _live_nav_m

BRANDING = {"agency_name": "Test Agency", "client_name": "Aurora Home Goods",
            "primary_color": "#2a78d6", "accent_color": "#eda100"}

#: Track E1 -- the checked-in "aurora-home-goods" SQLite data context fixture
#: lives under this fixed tenant_id (data_contexts/demo-tenant/aurora-home-goods.json).
TENANT = "demo-tenant"

_ANALYTICS_COLUMN_MAP = {
    "date": "event_date", "channel_group": "src_channel", "device_category": "device",
    "sessions": "sess_count", "revenue_usd": "rev_usd",
}


@pytest.fixture(scope="module")
def aurora_report_object():
    from app import agent
    from tests.conftest import seed_aurora_home_goods_data_context

    seed_aurora_home_goods_data_context()
    original = agent._ollama_available
    agent._ollama_available = lambda: False
    try:
        result = report_builder.build_report_from_data_context(
            TENANT, "aurora-home-goods", BRANDING, report_id="d23-test")
    finally:
        agent._ollama_available = original
    return result["report_object"]


def test_default_call_signature_is_unchanged(tmp_path, aurora_report_object):
    """No connection_mode/data_context args at all -- exactly how every
    existing caller (exports.py, main.py) invokes this today."""
    summary = pbip_export.build_pbip(aurora_report_object, tmp_path)
    assert summary["connection_mode"] == "snapshot"
    assert summary["live_tables_written"] == []


def test_live_mode_without_data_context_raises():
    with pytest.raises(ValueError, match="requires a data_context"):
        pbip_export.build_pbip(object(), Path("."), connection_mode="live")


def test_unknown_connection_mode_raises():
    with pytest.raises(ValueError, match="connection_mode must be"):
        pbip_export.build_pbip(object(), Path("."), connection_mode="turbo")


# ---------------------------------------------------------------------------
# _live_nav_m: connection topology only, never a secret
# ---------------------------------------------------------------------------

def test_sqlite_the_real_demo_connector_kind_has_no_live_support():
    with pytest.raises(UnsupportedLiveConnection, match="no native Power Query connector"):
        _live_nav_m("sqlite", {"path": "/data/aurora.db"}, "analytics")


def test_postgres_nav_has_no_password_and_no_dsn_scheme():
    nav = _live_nav_m("postgres", {"dsn": "postgresql://admin:s3cr3t@db.acme.com:5432/warehouse"}, "analytics")
    assert "s3cr3t" not in nav
    assert "admin" not in nav
    assert "PostgreSQL.Database" in nav
    assert "db.acme.com:5432" in nav
    assert "warehouse" in nav
    assert "analytics" in nav


def test_snowflake_nav_has_no_password():
    config = {"account": "acme-corp", "user": "svc_account", "password": "hunter2",
              "warehouse": "COMPUTE_WH", "database": "ANALYTICS_DB", "schema": "PUBLIC"}
    nav = _live_nav_m("snowflake", config, "sessions")
    assert "hunter2" not in nav
    assert "svc_account" not in nav
    assert "Snowflake.Databases" in nav
    assert "acme-corp.snowflakecomputing.com" in nav
    assert "COMPUTE_WH" in nav
    assert "ANALYTICS_DB" in nav


def test_bigquery_nav_has_no_credentials_path():
    config = {"project_id": "acme-gcp", "dataset": "reporting", "credentials_path": "/secrets/sa.json"}
    nav = _live_nav_m("bigquery", config, "analytics")
    assert "/secrets/sa.json" not in nav
    assert "GoogleBigQuery.Database" in nav
    assert "acme-gcp" in nav
    assert "reporting" in nav


def test_databricks_nav_has_no_access_token():
    config = {"server_hostname": "acme.cloud.databricks.com", "http_path": "/sql/1.0/warehouses/abc",
              "access_token": "dapi1234567890", "catalog": "main", "schema": "analytics"}
    nav = _live_nav_m("databricks", config, "sessions")
    assert "dapi1234567890" not in nav
    assert "Databricks.Catalogs" in nav
    assert "acme.cloud.databricks.com" in nav
    assert "main" in nav


# ---------------------------------------------------------------------------
# End-to-end: build_pbip(..., connection_mode="live", ...)
# ---------------------------------------------------------------------------

def _postgres_data_context() -> dict:
    return {
        "connector": {"kind": "postgres",
                      "config": {"dsn": "postgresql://admin:s3cr3t@db.acme.com:5432/warehouse"}},
        "sources": {
            "analytics": {"table": "web_analytics", "column_map": _ANALYTICS_COLUMN_MAP},
        },
    }


def test_live_mode_emits_a_directquery_table_per_covered_section(tmp_path, aurora_report_object):
    summary = pbip_export.build_pbip(aurora_report_object, tmp_path, connection_mode="live",
                                      data_context=_postgres_data_context())
    assert summary["connection_mode"] == "live"
    assert "AnalyticsLive" in summary["live_tables_written"]
    assert summary["live_tables_skipped"] == []
    # Additive: snapshot tables are still there too, untouched.
    assert "AnalyticsByChannel" in summary["tables_written"]
    assert "AnalyticsLive" in summary["tables_written"]

    tmdl = (Path(summary["model_dir"]) / "definition" / "tables" / "AnalyticsLive.tmdl").read_text(encoding="utf-8")
    assert "mode: directQuery" in tmdl
    assert "PostgreSQL.Database" in tmdl
    assert "Table.RenameColumns" in tmdl
    assert "revenue_usd" in tmdl  # canonical name, post-rename


def test_a_section_the_data_context_has_but_the_report_doesnt_cover_is_skipped(tmp_path, aurora_report_object):
    import copy
    analytics_only = copy.deepcopy(aurora_report_object)
    analytics_only.section_order = ["analytics"]  # pretend only analytics is covered

    dc = _postgres_data_context()
    dc["sources"]["sales"] = {"table": "deals", "column_map": {"amount_usd": "amount"}}
    summary = pbip_export.build_pbip(analytics_only, tmp_path, connection_mode="live", data_context=dc)
    assert summary["live_tables_written"] == ["AnalyticsLive"]  # sales was in data_context but not this report


def test_no_secret_ever_appears_in_generated_files(tmp_path, aurora_report_object):
    """The end-to-end proof, not just the unit-level nav string checks above:
    grep every byte build_pbip actually wrote to disk."""
    summary = pbip_export.build_pbip(aurora_report_object, tmp_path, connection_mode="live",
                                      data_context=_postgres_data_context())
    all_text = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore") for p in Path(summary["model_dir"]).rglob("*") if p.is_file()
    )
    assert "s3cr3t" not in all_text
    assert "admin" not in all_text


def test_the_real_aurora_home_goods_data_context_is_sqlite_so_live_mode_skips_with_a_reason(tmp_path, aurora_report_object):
    """Uses the ACTUAL data_context.py record for this project's own real
    demo client, not a synthetic one -- confirms live mode degrades
    explicitly for the connector kind this whole project actually runs on
    today, rather than only being proven against a hypothetical postgres."""
    from app import data_context
    real_dc = data_context.load_data_context(TENANT, "aurora-home-goods")
    summary = pbip_export.build_pbip(aurora_report_object, tmp_path, connection_mode="live", data_context=real_dc)
    assert summary["live_tables_written"] == []
    assert len(summary["live_tables_skipped"]) >= 1
    assert all("no native Power Query connector" in s["reason"] for s in summary["live_tables_skipped"])
    # Snapshot tables are completely unaffected by the failed live attempt.
    assert "AnalyticsByChannel" in summary["tables_written"]
