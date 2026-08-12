"""
Tests for main.py's access-control gate. Track E1 replaced the old,
all-or-nothing X-API-Key shared secret with real per-user sessions: every
tenant-scoped route now requires Depends(auth.get_tenant_id) (a valid
`rp_session` cookie), and the one remaining non-human caller --
POST /api/schedules/run, for a cron/service trigger -- has its own narrow
X-Scheduler-Token check instead of a global gate on every request. Several
assertions from the old X-API-Key-era version of this file are now false by
design (there is no more "unset means no auth at all" state) -- this is a
full rewrite, not a patch.
"""
from app.settings import settings
from tests.conftest import seed_tenant


def test_an_unauthenticated_request_is_rejected(client):
    """No session cookie at all -- the default, zero-config state now
    means "not logged in," not "no auth."""
    assert client.get("/api/schedules").status_code == 401


def test_a_garbage_session_cookie_is_rejected(client):
    client.cookies.set(settings.session_cookie_name, "not-a-real-token")
    assert client.get("/api/schedules").status_code == 401


def test_a_real_session_is_accepted(client, db_session):
    seed_tenant(db_session, client, google_sub="g-1", email="a@northlight.com")
    assert client.get("/api/schedules").status_code == 200


def test_the_gate_applies_across_different_routes_not_just_one(client):
    assert client.get("/api/data-sources").status_code == 401
    assert client.post("/api/schedules/run").status_code == 401


def test_health_and_templates_endpoints_are_exempt(client):
    """/api/health and /api/templates are the two routes with nothing
    tenant-scoped to protect -- they stay open, no session required."""
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/templates").status_code == 200


def test_cors_preflight_is_never_blocked_by_the_auth_gate(client):
    """An OPTIONS preflight request never carries a session cookie's
    matching CSRF/auth context the way a browser would send it on the real
    follow-up request -- CORSMiddleware answers preflight before any route
    dependency runs, so it must never 401."""
    r = client.options(
        "/api/schedules",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


# ---------------------------------------------------------------------------
# POST /api/schedules/run's own dual path: a service token (every tenant,
# infra-only) vs a session (just that tenant) -- see main.py's
# _resolve_schedule_run_scope and scheduler.run_due_schedules's docstring.
# ---------------------------------------------------------------------------

def test_schedules_run_rejects_a_request_with_neither_token_nor_session(client):
    r = client.post("/api/schedules/run")
    assert r.status_code == 401


def test_schedules_run_accepts_a_valid_session_scoped_to_that_tenant(client, db_session):
    seed_tenant(db_session, client, google_sub="g-2", email="b@northlight.com")
    r = client.post("/api/schedules/run", params={"dry_run": "true"})
    assert r.status_code == 200


def test_schedules_run_accepts_a_valid_service_token_with_no_session(client, monkeypatch):
    monkeypatch.setattr(settings, "scheduler_service_token", "cron-secret")
    r = client.post("/api/schedules/run", params={"dry_run": "true"}, headers={"X-Scheduler-Token": "cron-secret"})
    assert r.status_code == 200


def test_schedules_run_rejects_the_wrong_service_token(client, monkeypatch):
    monkeypatch.setattr(settings, "scheduler_service_token", "cron-secret")
    r = client.post("/api/schedules/run", params={"dry_run": "true"}, headers={"X-Scheduler-Token": "wrong"})
    assert r.status_code == 401


def test_an_unconfigured_service_token_never_matches_a_blank_header(client, monkeypatch):
    """settings.scheduler_service_token defaults to "" -- a header that's
    also blank/absent must never accidentally satisfy an unset secret."""
    monkeypatch.setattr(settings, "scheduler_service_token", "")
    r = client.post("/api/schedules/run", params={"dry_run": "true"}, headers={"X-Scheduler-Token": ""})
    assert r.status_code == 401
