"""
Track E1 — the primary new signal for Slice 4: two independently-seeded
tenants, proving cross-tenant access is structurally impossible (a 404 by
construction, or the correct "nothing configured" contract), not just
untested.

Scenarios below mirror the approved plan's own list exactly (see the
Track E1 plan, "Tenant-isolation test scenarios"), numbered the same way
so a failure here maps directly back to the finding it guards against.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app import alerts, data_context, delivery, report_store, scheduler
from app.main import app
from tests.conftest import seed_tenant

CSV = (
    "date,channel_group,device_category,sessions,new_users,conversions,revenue_usd\n"
    "2026-01-01,Organic Search,desktop,100,40,5,500\n"
    "2026-01-08,Paid Search,mobile,120,45,6,600\n"
)


@pytest.fixture(autouse=True)
def _fast_and_isolated(tmp_path, monkeypatch, db_session):
    """Every store this track touches, isolated per test (db_session, see
    tests/conftest.py) -- and the deterministic narrative fallback forced
    on, so a real end-to-end generation (needed for several scenarios
    below) is fast and doesn't depend on a live model."""
    monkeypatch.setattr("app.agent._ollama_available", lambda: False)
    return tmp_path


@pytest.fixture
def two_tenants(client, db_session):
    """client (tenant A) comes from conftest.py; tenant B gets its own
    TestClient + cookie jar against the SAME app/db_session, exactly like
    two different browsers logged in as two different agencies."""
    tenant_a = seed_tenant(db_session, client, google_sub="g-northlight", email="a@northlight.com", name="Northlight")
    client_b = TestClient(app)
    tenant_b = seed_tenant(db_session, client_b, google_sub="g-meridian", email="b@meridian.com", name="Meridian")
    return client, tenant_a, client_b, tenant_b


def _make_sqlite_client(tmp_path, tenant_id: str, client_id: str, revenue: float = 500.0) -> None:
    import sqlite3

    db_path = tmp_path / f"{tenant_id}-{client_id}.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE analytics (date TEXT, channel_group TEXT, device_category TEXT, "
        "sessions INTEGER, new_users INTEGER, conversions INTEGER, revenue_usd REAL)"
    )
    conn.execute(
        "INSERT INTO analytics VALUES ('2026-01-01', 'Organic Search', 'desktop', 100, 40, 5, ?)", (revenue,),
    )
    conn.commit()
    conn.close()
    fields = ["date", "channel_group", "device_category", "sessions", "new_users", "conversions", "revenue_usd"]
    data_context.save_data_context(
        tenant_id, client_id, "sqlite", {"path": str(db_path)},
        {"analytics": {"table": "analytics", "column_map": {f: f for f in fields}}},
    )


# ---------------------------------------------------------------------------
# 1. Data-source read isolation
# ---------------------------------------------------------------------------

def test_1_data_source_read_isolation(two_tenants, tmp_path):
    client_a, tenant_a, client_b, tenant_b = two_tenants
    _make_sqlite_client(tmp_path, tenant_a, "acme")

    ok = client_a.get("/api/data-sources/acme")
    assert ok.status_code == 200
    assert ok.json()["client_id"] == "acme"

    denied = client_b.get("/api/data-sources/acme")
    assert denied.status_code == 404

    listed_b = client_b.get("/api/data-sources").json()["data_sources"]
    assert all(d["client_id"] != "acme" for d in listed_b)


def test_1b_data_source_delete_isolation(two_tenants, tmp_path):
    """B can't delete A's data source (still a 404, same as the read case
    above) -- and, critically, A's file survives B's attempt untouched, not
    just "the response says 404." A deleting its own then gets a real 200,
    and a second delete of the same client_id 404s (nothing left to delete)."""
    client_a, tenant_a, client_b, tenant_b = two_tenants
    _make_sqlite_client(tmp_path, tenant_a, "acme")

    denied = client_b.delete("/api/data-sources/acme")
    assert denied.status_code == 404
    assert client_a.get("/api/data-sources/acme").status_code == 200  # A's copy untouched by B's attempt

    ok = client_a.delete("/api/data-sources/acme")
    assert ok.status_code == 200
    assert client_a.get("/api/data-sources/acme").status_code == 404

    gone_again = client_a.delete("/api/data-sources/acme")
    assert gone_again.status_code == 404


# ---------------------------------------------------------------------------
# 2. Filename-collision regression: identical client_id, different tenants
# ---------------------------------------------------------------------------

def test_2_identical_client_id_across_tenants_never_collides(two_tenants):
    client_a, tenant_a, client_b, tenant_b = two_tenants

    r_a = client_a.post("/api/data-sources/onboard", json={
        "client_id": "acme", "kind": "sqlite", "config": {"path": "a.db"}, "table_map": {},
    })
    r_b = client_b.post("/api/data-sources/onboard", json={
        "client_id": "acme", "kind": "sqlite", "config": {"path": "b.db"}, "table_map": {},
    })
    assert r_a.status_code == 200 and r_b.status_code == 200

    ctx_a = client_a.get("/api/data-sources/acme").json()
    ctx_b = client_b.get("/api/data-sources/acme").json()
    assert ctx_a["connector"]["config"]["path"] == "a.db"
    assert ctx_b["connector"]["config"]["path"] == "b.db"  # A's write never clobbered B's


# ---------------------------------------------------------------------------
# 3. Schedule isolation
# ---------------------------------------------------------------------------

def test_3_session_authenticated_schedules_run_only_touches_the_callers_tenant(two_tenants, tmp_path):
    client_a, tenant_a, client_b, tenant_b = two_tenants
    _make_sqlite_client(tmp_path, tenant_a, "acme")
    _make_sqlite_client(tmp_path, tenant_b, "zenith")

    assert client_a.post("/api/schedules", json={
        "client_id": "acme", "data_source_ref": "acme", "cadence": "daily",
        "branding": {"agency_name": "A", "client_name": "Acme"},
    }).status_code == 200
    assert client_b.post("/api/schedules", json={
        "client_id": "zenith", "data_source_ref": "zenith", "cadence": "daily",
        "branding": {"agency_name": "B", "client_name": "Zenith"},
    }).status_code == 200

    b_before = scheduler.load_schedule(tenant_b, "zenith")

    resp = client_a.post("/api/schedules/run", params={"as_of": "2026-06-01", "dry_run": "false"})
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert [r["client_id"] for r in results] == ["acme"]  # never "zenith"

    # unmodified, not just absent from the response
    assert scheduler.load_schedule(tenant_b, "zenith") == b_before


# ---------------------------------------------------------------------------
# 4. Report isolation, per endpoint
# ---------------------------------------------------------------------------

@pytest.fixture
def tenant_a_report(two_tenants):
    client_a, tenant_a, client_b, tenant_b = two_tenants
    resp = client_a.post(
        "/api/generate-report",
        data={"agency_name": "A", "client_name": "Acme"},
        files={"analytics_file": ("analytics.csv", CSV, "text/csv")},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    import time
    for _ in range(100):
        if client_a.get(f"/api/report/{job_id}").status_code == 200:
            break
        time.sleep(0.2)
    else:
        pytest.fail("report never finished generating")
    return job_id


def test_4_report_endpoints_404_for_another_tenant(two_tenants, tenant_a_report):
    client_a, tenant_a, client_b, tenant_b = two_tenants
    report_id = tenant_a_report

    for suffix in ("", "/pdf", "/html", "/dashboard", "/export/pptx"):
        assert client_a.get(f"/api/report/{report_id}{suffix}").status_code == 200, suffix
        assert client_b.get(f"/api/report/{report_id}{suffix}").status_code == 404, suffix

    all_reports_b = client_b.get("/api/reports").json()["reports"]
    assert all(r["report_id"] != report_id for r in all_reports_b)


# ---------------------------------------------------------------------------
# 5. Cross-tenant diff, both argument orderings
# ---------------------------------------------------------------------------

def test_5_diff_endpoint_checks_both_report_id_params_not_just_one(two_tenants, tenant_a_report):
    client_a, tenant_a, client_b, tenant_b = two_tenants
    report_a = tenant_a_report

    resp_b = client_b.post(
        "/api/generate-report",
        data={"agency_name": "B", "client_name": "Zenith"},
        files={"analytics_file": ("analytics.csv", CSV, "text/csv")},
    )
    job_b = resp_b.json()["job_id"]
    import time
    for _ in range(100):
        if client_b.get(f"/api/report/{job_b}").status_code == 200:
            break
        time.sleep(0.2)
    else:
        pytest.fail("report never finished generating")
    report_b = job_b

    # A owns report_a only. Neither ordering of A's-own vs B's-report may succeed.
    assert client_a.get("/api/reports/diff", params={"report_id_a": report_a, "report_id_b": report_b}).status_code == 404
    assert client_a.get("/api/reports/diff", params={"report_id_a": report_b, "report_id_b": report_a}).status_code == 404


# ---------------------------------------------------------------------------
# 6. Alerts: "nothing configured" contract preserved across tenants
# ---------------------------------------------------------------------------

def test_6_alerts_for_another_tenants_client_id_returns_empty_not_404(two_tenants):
    client_a, tenant_a, client_b, tenant_b = two_tenants
    r = client_a.post("/api/alerts", json={
        "client_id": "acme",
        "rules": [{"id": "r1", "metric_path": "analytics.revenue_usd", "direction": "pct_drop", "threshold_pct": 10}],
    })
    assert r.status_code == 200

    seen_by_b = client_b.get("/api/alerts/acme")
    assert seen_by_b.status_code == 200  # matches the existing "unconfigured" contract -- not 404
    assert seen_by_b.json() == {"client_id": "acme", "rules": []}


# ---------------------------------------------------------------------------
# 7. Job/SSE isolation
# ---------------------------------------------------------------------------

def test_7_job_events_for_another_tenants_job_id_looks_exactly_like_not_found(two_tenants):
    client_a, tenant_a, client_b, tenant_b = two_tenants
    resp = client_a.post(
        "/api/generate-report",
        data={"agency_name": "A", "client_name": "Acme"},
        files={"analytics_file": ("analytics.csv", CSV, "text/csv")},
    )
    job_id = resp.json()["job_id"]

    genuinely_missing = client_a.get("/api/jobs/does-not-exist/events")
    cross_tenant = client_b.get(f"/api/jobs/{job_id}/events")
    assert genuinely_missing.status_code == 200  # SSE 200s at the HTTP layer; the payload carries the real signal
    assert cross_tenant.status_code == 200
    assert "job not found" in cross_tenant.text
    assert "job not found" in genuinely_missing.text
    # B never sees a real stage from A's in-flight run.
    assert "Parsing" not in cross_tenant.text and "Writing" not in cross_tenant.text


# ---------------------------------------------------------------------------
# 8. Session sanity
# ---------------------------------------------------------------------------

def test_8_two_seeded_sessions_never_share_a_tenant_and_role_is_owner(two_tenants):
    client_a, tenant_a, client_b, tenant_b = two_tenants
    assert tenant_a != tenant_b

    me_a = client_a.get("/api/auth/me").json()
    me_b = client_b.get("/api/auth/me").json()
    assert me_a["tenant"]["id"] == tenant_a
    assert me_b["tenant"]["id"] == tenant_b
    assert me_a["role"] == "owner"
    assert me_b["role"] == "owner"


# ---------------------------------------------------------------------------
# 9. Sweeping unauthenticated check across every tenant-scoped route
# ---------------------------------------------------------------------------

_TENANT_SCOPED_ROUTES = [
    ("POST", "/api/data-sources/test", {"json": {"kind": "sqlite", "config": {}}}),
    ("POST", "/api/data-sources/onboard", {"json": {"client_id": "x", "kind": "sqlite", "config": {}, "table_map": {}}}),
    ("POST", "/api/data-sources/onboard-inbox", {"json": {"client_id": "x", "username": "u", "password": "p"}}),
    ("POST", "/api/data-sources/onboard-slack", {"json": {"client_id": "x", "bot_token": "t", "channel_id": "c"}}),
    ("GET", "/api/data-sources", {}),
    ("GET", "/api/data-sources/x", {}),
    ("DELETE", "/api/data-sources/x", {}),
    ("POST", "/api/schedules", {"json": {"client_id": "x", "data_source_ref": "x", "cadence": "daily"}}),
    ("GET", "/api/schedules", {}),
    ("POST", "/api/schedules/run", {}),
    ("POST", "/api/alerts", {"json": {"client_id": "x", "rules": []}}),
    ("GET", "/api/alerts/x", {}),
    ("POST", "/api/generate-report", {}),
    ("POST", "/api/generate-report/from-inbox", {"json": {}}),
    ("GET", "/api/jobs/x/events", {}),
    ("GET", "/api/reports", {}),
    ("GET", "/api/clients/x/reports", {}),
    ("GET", "/api/reports/diff", {"params": {"report_id_a": "x", "report_id_b": "y"}}),
    ("GET", "/api/report/x", {}),
    ("GET", "/api/report/x/pdf", {}),
    ("GET", "/api/report/x/html", {}),
    ("GET", "/api/report/x/dashboard", {}),
    ("GET", "/api/report/x/export/pptx", {}),
]


@pytest.mark.parametrize("method,path,kwargs", _TENANT_SCOPED_ROUTES, ids=[p for _, p, _ in _TENANT_SCOPED_ROUTES])
def test_9_every_tenant_scoped_route_401s_with_no_session(method, path, kwargs):
    anon = TestClient(app)  # no cookie set at all
    resp = anon.request(method, path, **kwargs)
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code} (expected 401)"


def test_9_health_and_templates_are_the_only_two_exemptions():
    anon = TestClient(app)
    assert anon.get("/api/health").status_code == 200
    assert anon.get("/api/templates").status_code == 200


# ---------------------------------------------------------------------------
# 10. Service-token vs session privilege boundary on /schedules/run
# ---------------------------------------------------------------------------

def test_10_service_token_touches_every_tenant_session_touches_only_its_own(two_tenants, tmp_path, monkeypatch):
    from app.settings import settings

    client_a, tenant_a, client_b, tenant_b = two_tenants
    _make_sqlite_client(tmp_path, tenant_a, "acme")
    _make_sqlite_client(tmp_path, tenant_b, "zenith")
    client_a.post("/api/schedules", json={
        "client_id": "acme", "data_source_ref": "acme", "cadence": "daily",
        "branding": {"agency_name": "A", "client_name": "Acme"},
    })
    client_b.post("/api/schedules", json={
        "client_id": "zenith", "data_source_ref": "zenith", "cadence": "daily",
        "branding": {"agency_name": "B", "client_name": "Zenith"},
    })

    # Session-authenticated: only the caller's own tenant.
    session_scoped = client_a.post("/api/schedules/run", params={"as_of": "2026-06-01", "dry_run": "true"})
    assert [r["client_id"] for r in session_scoped.json()["results"]] == ["acme"]

    # Valid service token, no session: every tenant's due schedules -- intentional.
    monkeypatch.setattr(settings, "scheduler_service_token", "cron-secret")
    anon = TestClient(app)
    infra_scoped = anon.post(
        "/api/schedules/run", params={"as_of": "2026-06-01", "dry_run": "true"},
        headers={"X-Scheduler-Token": "cron-secret"},
    )
    assert infra_scoped.status_code == 200
    client_ids = {r["client_id"] for r in infra_scoped.json()["results"]}
    assert client_ids == {"acme", "zenith"}
