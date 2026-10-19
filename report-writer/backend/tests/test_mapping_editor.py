"""
Tests for fixing a data source's column mapping by hand: GET/PATCH
/api/data-sources/{client_id}/mapping, plus the clear error a report run
gives when a required field was never mapped.

The scenario is the one that hit the demo warehouse with no AI provider
configured: the fuzzy matcher can't pair close_date with closed_on, so the
sales mapping is saved without it, and generating a report used to die with
a bare KeyError: 'close_date'.
"""
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from app import data_context, report_builder
from tests.conftest import seed_tenant

SAMPLE = Path(__file__).parent.parent / "sample_data"


@pytest.fixture
def tenant_id(client, db_session):
    return seed_tenant(db_session, client, google_sub="g-mapping", email="m@northlight.com", name="Morgan")


@pytest.fixture
def warehouse(tmp_path):
    """A sales table with warehouse-style column names, like
    sample_data/build_demo_warehouse.py's crm_opportunities."""
    deals = pd.read_excel(SAMPLE / "sales_pipeline.xlsx", sheet_name="Deals").rename(columns={
        "deal_id": "opp_id", "close_date": "closed_on", "sales_rep": "owner", "product": "sku",
        "lead_source": "channel", "deal_stage": "status", "amount_usd": "closed_amount",
        "potential_amount_usd": "pipeline_amount",
    })
    path = tmp_path / "warehouse.sqlite"
    with sqlite3.connect(path) as conn:
        deals.to_sql("crm_opportunities", conn, index=False)
    return str(path)


@pytest.fixture
def onboarded(client, tenant_id, warehouse, monkeypatch):
    """Onboards with no AI provider reachable -> fuzzy matching only."""
    monkeypatch.setattr(data_context, "_ai_propose_mapping", lambda *a, **k: None)
    resp = client.post("/api/data-sources/onboard", json={
        "client_id": "aurora", "kind": "sqlite", "config": {"path": warehouse},
        "table_map": {"sales": "crm_opportunities"},
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_onboard_flags_a_missing_required_field(onboarded):
    sales = onboarded["sources"]["sales"]
    assert sales["required_field"] == "close_date"
    assert sales["missing_required"] is True
    assert "closed_on" in sales["discovered_columns"]


def test_report_run_names_the_unmapped_field_instead_of_a_key_error(onboarded, tenant_id):
    with pytest.raises(ValueError, match="close_date.*Edit mapping"):
        report_builder.build_report_from_data_context(tenant_id, "aurora", {"client_name": "Aurora"})


def test_mapping_endpoint_never_returns_connector_credentials(client, onboarded):
    resp = client.get("/api/data-sources/aurora/mapping")
    assert resp.status_code == 200
    body = resp.json()
    assert "connector" not in body
    assert "warehouse.sqlite" not in resp.text
    assert body["sources"]["sales"]["table"] == "crm_opportunities"


def test_patch_fixes_the_mapping_and_the_report_builds(client, onboarded, tenant_id):
    resp = client.patch("/api/data-sources/aurora/mapping", json={"overrides": {"sales": {
        "close_date": "closed_on", "sales_rep": "owner", "deal_stage": "status", "amount_usd": "closed_amount",
    }}})
    assert resp.status_code == 200, resp.text
    sales = resp.json()["sources"]["sales"]
    assert sales["missing_required"] is False
    assert sales["column_map"]["close_date"] == "closed_on"
    assert sales["mapping_method"] == "fuzzy_match+manual"

    result = report_builder.build_report_from_data_context(tenant_id, "aurora", {"client_name": "Aurora"})
    assert result["metrics"]["sales"]["totals"]["revenue_usd"] > 0


def test_patch_rejects_a_column_the_table_does_not_have(client, onboarded):
    resp = client.patch("/api/data-sources/aurora/mapping",
                        json={"overrides": {"sales": {"close_date": "no_such_column"}}})
    assert resp.status_code == 400
    assert "no_such_column" in resp.json()["detail"]


def test_patch_rejects_an_unknown_field(client, onboarded):
    resp = client.patch("/api/data-sources/aurora/mapping",
                        json={"overrides": {"sales": {"not_a_field": "owner"}}})
    assert resp.status_code == 400


def test_patch_for_an_unknown_client_is_404(client, tenant_id):
    resp = client.patch("/api/data-sources/nobody/mapping",
                        json={"overrides": {"sales": {"close_date": "closed_on"}}})
    assert resp.status_code == 404
