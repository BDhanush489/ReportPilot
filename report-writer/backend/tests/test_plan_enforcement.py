"""
Tests for app/plans.py's actual enforcement -- not just the marketing copy,
the real limits: an active-client cap, a can-schedule-at-all gate (Solo
can't schedule), and a Power BI export gate (Solo can't export pbip).

"active client" = a distinct client_id with a saved Schedule for this
tenant (see plans.py's own module docstring for why a schedule, not a
one-off upload, is what "active" means here).

tests/conftest.py's seed_tenant() defaults a fresh tenant to the "agency"
plan (scheduling + PBIP both included, a high cap) so every OTHER test file
that predates plan-gating keeps working unchanged -- tests here explicitly
downgrade to "solo" (or patch a plan's cap down) to exercise the gates
themselves.
"""
import dataclasses

import pytest

from app import plans as plans_mod
from app.models import Tenant
from tests.conftest import seed_tenant

CSV = (
    "date,channel_group,device_category,sessions,new_users,conversions,revenue_usd\n"
    "2026-01-01,Organic Search,desktop,100,40,5,500\n"
)


@pytest.fixture(autouse=True)
def _isolated_stores(db_session, monkeypatch):
    monkeypatch.setattr("app.agent._ollama_available", lambda: False)


def _downgrade_to_solo(db_session, tenant_id: str) -> None:
    tenant = db_session.get(Tenant, tenant_id)
    tenant.plan = "solo"
    db_session.commit()


def _onboard(client, client_id: str):
    return client.post("/api/data-sources/onboard", json={
        "client_id": client_id, "kind": "sqlite", "config": {"path": f"{client_id}.db"}, "table_map": {},
    })


# ---------------------------------------------------------------------------
# can_schedule gate
# ---------------------------------------------------------------------------

def test_solo_plan_cannot_create_any_schedule(client, db_session):
    tenant_id = seed_tenant(db_session, client, google_sub="g-1", email="a@x.com")
    _downgrade_to_solo(db_session, tenant_id)
    assert _onboard(client, "acme").status_code == 200

    resp = client.post("/api/schedules", json={"client_id": "acme", "data_source_ref": "acme", "cadence": "daily"})
    assert resp.status_code == 402
    assert "Solo" in resp.json()["detail"]


def test_agency_plan_can_create_a_schedule(client, db_session):
    seed_tenant(db_session, client, google_sub="g-1", email="a@x.com")  # agency by default
    assert _onboard(client, "acme").status_code == 200
    resp = client.post("/api/schedules", json={"client_id": "acme", "data_source_ref": "acme", "cadence": "daily"})
    assert resp.status_code == 200


def test_upgrading_a_tenant_off_solo_unblocks_scheduling(client, db_session):
    tenant_id = seed_tenant(db_session, client, google_sub="g-1", email="a@x.com")
    _downgrade_to_solo(db_session, tenant_id)
    assert _onboard(client, "acme").status_code == 200
    assert client.post(
        "/api/schedules", json={"client_id": "acme", "data_source_ref": "acme", "cadence": "daily"},
    ).status_code == 402

    tenant = db_session.get(Tenant, tenant_id)
    tenant.plan = "agency"
    db_session.commit()

    assert client.post(
        "/api/schedules", json={"client_id": "acme", "data_source_ref": "acme", "cadence": "daily"},
    ).status_code == 200


# ---------------------------------------------------------------------------
# Active-client cap
# ---------------------------------------------------------------------------

def test_active_client_cap_is_enforced(client, db_session, monkeypatch):
    seed_tenant(db_session, client, google_sub="g-1", email="a@x.com")  # agency, cap 50 by default
    monkeypatch.setitem(plans_mod.PLANS, "agency", dataclasses.replace(plans_mod.PLANS["agency"], max_active_clients=2))

    for client_id in ("acme", "beta"):
        assert _onboard(client, client_id).status_code == 200
        resp = client.post("/api/schedules", json={"client_id": client_id, "data_source_ref": client_id, "cadence": "daily"})
        assert resp.status_code == 200, resp.text

    # A third DISTINCT client exceeds the (patched) cap of 2.
    assert _onboard(client, "gamma").status_code == 200
    resp = client.post("/api/schedules", json={"client_id": "gamma", "data_source_ref": "gamma", "cadence": "daily"})
    assert resp.status_code == 402
    assert "2 active clients" in resp.json()["detail"]


def test_updating_an_already_scheduled_client_never_counts_twice_against_the_cap(client, db_session, monkeypatch):
    seed_tenant(db_session, client, google_sub="g-1", email="a@x.com")
    monkeypatch.setitem(plans_mod.PLANS, "agency", dataclasses.replace(plans_mod.PLANS["agency"], max_active_clients=1))

    assert _onboard(client, "acme").status_code == 200
    first = client.post("/api/schedules", json={"client_id": "acme", "data_source_ref": "acme", "cadence": "daily"})
    assert first.status_code == 200

    # Re-saving the SAME client_id (a cadence change) is an update, not a
    # new active-client relationship -- must succeed even at a cap of 1.
    second = client.post("/api/schedules", json={"client_id": "acme", "data_source_ref": "acme", "cadence": "weekly"})
    assert second.status_code == 200


def test_inhouse_plan_has_no_cap(client, db_session, monkeypatch):
    tenant_id = seed_tenant(db_session, client, google_sub="g-1", email="a@x.com")
    tenant = db_session.get(Tenant, tenant_id)
    tenant.plan = "inhouse"
    db_session.commit()
    assert plans_mod.get_plan("inhouse").max_active_clients is None

    for client_id in ("acme", "beta", "gamma"):
        assert _onboard(client, client_id).status_code == 200
        resp = client.post("/api/schedules", json={"client_id": client_id, "data_source_ref": client_id, "cadence": "daily"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Power BI (pbip) export gate
# ---------------------------------------------------------------------------

def _generate_report(client) -> str:
    resp = client.post(
        "/api/generate-report",
        data={"agency_name": "A", "client_name": "Acme"},
        files={"analytics_file": ("analytics.csv", CSV, "text/csv")},
    )
    job_id = resp.json()["job_id"]
    import time
    for _ in range(100):
        if client.get(f"/api/report/{job_id}").status_code == 200:
            return job_id
        time.sleep(0.2)
    pytest.fail("report never finished generating")


def test_solo_plan_cannot_export_pbip(client, db_session):
    tenant_id = seed_tenant(db_session, client, google_sub="g-1", email="a@x.com")
    _downgrade_to_solo(db_session, tenant_id)
    report_id = _generate_report(client)

    resp = client.get(f"/api/report/{report_id}/export/pbip")
    assert resp.status_code == 402
    assert "Solo" in resp.json()["detail"]


def test_solo_plan_can_still_export_other_formats(client, db_session):
    """The gate is pbip-specific -- Solo's own copy never mentions pptx/
    email_html, so those stay available; only Power BI is called out as an
    Agency+ feature."""
    tenant_id = seed_tenant(db_session, client, google_sub="g-1", email="a@x.com")
    _downgrade_to_solo(db_session, tenant_id)
    report_id = _generate_report(client)

    resp = client.get(f"/api/report/{report_id}/export/pptx")
    assert resp.status_code == 200


def test_agency_plan_can_export_pbip(client, db_session):
    seed_tenant(db_session, client, google_sub="g-1", email="a@x.com")  # agency by default
    report_id = _generate_report(client)
    resp = client.get(f"/api/report/{report_id}/export/pbip")
    assert resp.status_code == 200
