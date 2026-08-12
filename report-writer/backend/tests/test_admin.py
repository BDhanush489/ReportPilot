"""
Tests for the platform-admin surface (cross-tenant user/tenant visibility
and management) and plan enforcement.

Platform admin is a DIFFERENT, more powerful thing than a Membership.role
"owner" (which is scoped to one tenant) -- deliberately gated separately
(auth.require_platform_admin, session-only) from every tenant-scoped route,
so being a platform admin never grants access to another tenant's actual
reports/schedules/data through the normal API. That isolation is the whole
point of Track E1; this file proves the admin surface doesn't quietly
undo it.
"""
import pytest

from app import auth, data_context, scheduler
from app.models import Tenant, User
from tests.conftest import seed_tenant


def _make_admin(db_session, client, *, google_sub="g-admin", email="admin@platform.com") -> str:
    tenant_id = seed_tenant(db_session, client, google_sub=google_sub, email=email, name="Admin")
    user = db_session.query(User).filter_by(google_sub=google_sub).one()
    user.is_platform_admin = True
    db_session.commit()
    return tenant_id


# ---------------------------------------------------------------------------
# Bootstrap: PLATFORM_ADMIN_EMAILS
# ---------------------------------------------------------------------------

def test_a_matching_email_is_promoted_on_login(db_session, monkeypatch):
    monkeypatch.setattr(auth.settings, "platform_admin_emails", "boss@agency.com, other@x.com")
    user, _tenant = auth.get_or_create_user_and_tenant(db_session, google_sub="g-1", email="boss@agency.com", name="Boss")
    assert user.is_platform_admin is True


def test_matching_is_case_insensitive_and_whitespace_tolerant(db_session, monkeypatch):
    monkeypatch.setattr(auth.settings, "platform_admin_emails", " Boss@Agency.com ")
    user, _tenant = auth.get_or_create_user_and_tenant(db_session, google_sub="g-1", email="boss@agency.com", name="Boss")
    assert user.is_platform_admin is True


def test_a_non_matching_email_is_never_promoted(db_session, monkeypatch):
    monkeypatch.setattr(auth.settings, "platform_admin_emails", "boss@agency.com")
    user, _tenant = auth.get_or_create_user_and_tenant(db_session, google_sub="g-2", email="nobody@x.com", name="N")
    assert user.is_platform_admin is False


def test_bootstrap_is_checked_on_every_login_not_just_creation(db_session, monkeypatch):
    """The account can exist BEFORE PLATFORM_ADMIN_EMAILS is set (or before
    this env var existed at all) -- promotion just needs their NEXT login,
    no direct DB edit required."""
    monkeypatch.setattr(auth.settings, "platform_admin_emails", "")
    user, _tenant = auth.get_or_create_user_and_tenant(db_session, google_sub="g-3", email="future-admin@x.com", name="F")
    assert user.is_platform_admin is False

    monkeypatch.setattr(auth.settings, "platform_admin_emails", "future-admin@x.com")
    user2, _tenant2 = auth.get_or_create_user_and_tenant(db_session, google_sub="g-3", email="future-admin@x.com", name="F")
    assert user2.id == user.id
    assert user2.is_platform_admin is True


def test_removing_an_email_later_does_not_revoke_already_granted_admin(db_session, monkeypatch):
    monkeypatch.setattr(auth.settings, "platform_admin_emails", "boss@agency.com")
    user, _tenant = auth.get_or_create_user_and_tenant(db_session, google_sub="g-4", email="boss@agency.com", name="B")
    assert user.is_platform_admin is True

    monkeypatch.setattr(auth.settings, "platform_admin_emails", "")  # removed from the list
    user2, _tenant2 = auth.get_or_create_user_and_tenant(db_session, google_sub="g-4", email="boss@agency.com", name="B")
    assert user2.is_platform_admin is True  # still an admin -- a real DB grant, not env-var-derived state


# ---------------------------------------------------------------------------
# require_platform_admin: the gate itself
# ---------------------------------------------------------------------------

def test_a_regular_session_is_rejected(client, db_session):
    seed_tenant(db_session, client, google_sub="g-1", email="a@x.com")
    resp = client.get("/api/admin/users")
    assert resp.status_code == 403


def test_no_session_at_all_is_rejected(client):
    assert client.get("/api/admin/users").status_code == 401


def test_an_api_key_can_never_reach_the_admin_surface(client, db_session):
    """A single-tenant machine credential must never be usable to reach a
    cross-tenant admin surface, even if that tenant's owner happens to also
    be a platform admin -- require_platform_admin is session-only by design."""
    tenant_id = _make_admin(db_session, client)
    admin_user = db_session.query(User).filter_by(email="admin@platform.com").one()
    raw_token = auth.create_api_token(db_session, db_session.get(Tenant, tenant_id), admin_user)

    from fastapi.testclient import TestClient

    from app.main import app
    anon = TestClient(app)
    resp = anon.get("/api/admin/users", headers={"X-API-Key": raw_token})
    assert resp.status_code == 401  # no session cookie at all -- get_current_auth_session rejects it


def test_a_platform_admin_session_is_accepted(client, db_session):
    _make_admin(db_session, client)
    assert client.get("/api/admin/users").status_code == 200


# ---------------------------------------------------------------------------
# GET /api/admin/users
# ---------------------------------------------------------------------------

def test_list_users_spans_every_tenant(client, db_session):
    _make_admin(db_session, client)
    # Direct DB creation, not seed_tenant(client, ...) -- seed_tenant would
    # overwrite `client`'s cookie jar with THIS user's session, logging the
    # admin back out mid-test.
    auth.get_or_create_user_and_tenant(db_session, google_sub="g-other", email="owner@northlight.com", name="Owner")

    resp = client.get("/api/admin/users")
    emails = {u["email"] for u in resp.json()["users"]}
    assert emails == {"admin@platform.com", "owner@northlight.com"}


def test_list_users_shows_role_and_tenant_and_admin_flag(client, db_session):
    _make_admin(db_session, client)
    resp = client.get("/api/admin/users")
    admin_row = next(u for u in resp.json()["users"] if u["email"] == "admin@platform.com")
    assert admin_row["is_platform_admin"] is True
    assert admin_row["role"] == "owner"
    assert admin_row["tenant"]["name"]


# ---------------------------------------------------------------------------
# Promote / demote
# ---------------------------------------------------------------------------

def test_promote_grants_platform_admin(client, db_session):
    _make_admin(db_session, client)
    auth.get_or_create_user_and_tenant(db_session, google_sub="g-target", email="target@x.com")
    target = db_session.query(User).filter_by(email="target@x.com").one()

    resp = client.post(f"/api/admin/users/{target.id}/promote")
    assert resp.status_code == 200
    assert resp.json()["is_platform_admin"] is True
    db_session.refresh(target)
    assert target.is_platform_admin is True


def test_demote_revokes_platform_admin(client, db_session):
    _make_admin(db_session, client)
    auth.get_or_create_user_and_tenant(db_session, google_sub="g-target", email="target@x.com")
    target = db_session.query(User).filter_by(email="target@x.com").one()
    target.is_platform_admin = True
    db_session.commit()

    resp = client.post(f"/api/admin/users/{target.id}/demote")
    assert resp.status_code == 200
    db_session.refresh(target)
    assert target.is_platform_admin is False


def test_an_admin_cannot_demote_their_own_account(client, db_session):
    _make_admin(db_session, client)
    admin_user = db_session.query(User).filter_by(email="admin@platform.com").one()

    resp = client.post(f"/api/admin/users/{admin_user.id}/demote")
    assert resp.status_code == 400
    db_session.refresh(admin_user)
    assert admin_user.is_platform_admin is True  # untouched


def test_promoting_an_unknown_user_404s(client, db_session):
    _make_admin(db_session, client)
    assert client.post("/api/admin/users/does-not-exist/promote").status_code == 404


def test_a_non_admin_cannot_promote_anyone(client, db_session):
    seed_tenant(db_session, client, google_sub="g-1", email="a@x.com")
    resp = client.post("/api/admin/users/whatever/promote")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/admin/tenants, POST .../plan
# ---------------------------------------------------------------------------

def test_list_tenants_shows_plan_and_active_client_count(client, db_session):
    tenant_id = _make_admin(db_session, client)
    resp = client.get("/api/admin/tenants")
    row = next(t for t in resp.json()["tenants"] if t["id"] == tenant_id)
    assert row["plan"] == "agency"  # seed_tenant's own default -- see its docstring
    assert row["max_active_clients"] == 50
    assert row["active_clients"] == 0
    assert row["member_count"] == 1


def test_set_tenant_plan_changes_it(client, db_session):
    tenant_id = _make_admin(db_session, client)
    resp = client.post(f"/api/admin/tenants/{tenant_id}/plan", json={"plan": "inhouse"})
    assert resp.status_code == 200
    assert resp.json()["plan"] == "inhouse"
    assert db_session.get(Tenant, tenant_id).plan == "inhouse"


def test_set_tenant_plan_rejects_an_unknown_plan(client, db_session):
    tenant_id = _make_admin(db_session, client)
    resp = client.post(f"/api/admin/tenants/{tenant_id}/plan", json={"plan": "enterprise-ultra"})
    assert resp.status_code == 400


def test_set_tenant_plan_404s_for_an_unknown_tenant(client, db_session):
    _make_admin(db_session, client)
    resp = client.post("/api/admin/tenants/does-not-exist/plan", json={"plan": "solo"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Being a platform admin never grants tenant-scoped access to other tenants
# -- the isolation guarantee this whole file exists to protect. is_platform_
# admin is never consulted by get_tenant_id/get_current_session at all, so
# this is really just confirming that stays true through the admin surface
# existing alongside it.
# ---------------------------------------------------------------------------

def test_a_platform_admin_still_only_sees_their_own_tenants_schedules(client, db_session):
    from fastapi.testclient import TestClient

    from app.main import app

    _make_admin(db_session, client)

    other_client = TestClient(app)
    other_tenant_id = seed_tenant(db_session, other_client, google_sub="g-other", email="owner@northlight.com")
    other_client.post("/api/data-sources/onboard", json={
        "client_id": "acme", "kind": "sqlite", "config": {"path": "x.db"}, "table_map": {},
    })
    r = other_client.post("/api/schedules", json={
        "client_id": "acme", "data_source_ref": "acme", "cadence": "daily",
    })
    assert r.status_code == 200  # sanity: the OTHER tenant really does have a schedule

    # The admin's own session, a completely separate TestClient/cookie jar,
    # sees none of it -- get_tenant_id resolves to the admin's OWN tenant,
    # is_platform_admin plays no part in that resolution at all.
    resp = client.get("/api/schedules")
    assert resp.status_code == 200
    assert resp.json()["schedules"] == []


# ---------------------------------------------------------------------------
# GET/POST/DELETE /api/admin/tenants/{id}/clients -- admin-driven add/remove
# of the active-client relationships that back the Admin panel's "active
# clients / cap" count. Crosses tenant boundaries on purpose (an admin/demo
# management tool, unlike POST /api/schedules which is caller's-own-tenant
# only) but still enforces the SAME plan rules a real schedule creation
# would -- this section is the proof that enforcement holds even from here.
# ---------------------------------------------------------------------------

def test_add_client_creates_a_real_schedule_and_data_context(client, db_session):
    tenant_id = _make_admin(db_session, client)  # seed_tenant's default plan is "agency"
    resp = client.post(f"/api/admin/tenants/{tenant_id}/clients", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["client_id"] == "demo-client-01"
    assert body["active_clients"] == 1
    assert data_context.load_data_context(tenant_id, "demo-client-01") is not None
    assert scheduler.load_schedule(tenant_id, "demo-client-01") is not None


def test_add_client_honors_a_caller_supplied_client_id(client, db_session):
    tenant_id = _make_admin(db_session, client)
    resp = client.post(f"/api/admin/tenants/{tenant_id}/clients", json={"client_id": "acme-corp"})
    assert resp.status_code == 200
    assert resp.json()["client_id"] == "acme-corp"
    assert scheduler.load_schedule(tenant_id, "acme-corp") is not None


def test_add_client_auto_ids_skip_past_a_taken_name(client, db_session):
    tenant_id = _make_admin(db_session, client)
    client.post(f"/api/admin/tenants/{tenant_id}/clients", json={"client_id": "demo-client-01"})
    resp = client.post(f"/api/admin/tenants/{tenant_id}/clients", json={})
    assert resp.status_code == 200
    assert resp.json()["client_id"] == "demo-client-02"


def test_add_client_rejects_a_duplicate_explicit_id(client, db_session):
    tenant_id = _make_admin(db_session, client)
    client.post(f"/api/admin/tenants/{tenant_id}/clients", json={"client_id": "acme"})
    resp = client.post(f"/api/admin/tenants/{tenant_id}/clients", json={"client_id": "acme"})
    assert resp.status_code == 400


def test_add_client_402s_when_plan_cannot_schedule(client, db_session):
    tenant_id = _make_admin(db_session, client)
    db_session.get(Tenant, tenant_id).plan = "solo"
    db_session.commit()
    resp = client.post(f"/api/admin/tenants/{tenant_id}/clients", json={})
    assert resp.status_code == 402
    assert "Solo" in resp.json()["detail"]
    assert scheduler.list_schedules_for_tenant(tenant_id) == []  # nothing left behind by the failed attempt


def test_add_client_402s_at_the_plan_cap(client, db_session, monkeypatch):
    import dataclasses

    from app import plans

    tenant_id = _make_admin(db_session, client)
    monkeypatch.setitem(plans.PLANS, "agency", dataclasses.replace(plans.PLANS["agency"], max_active_clients=1))
    ok = client.post(f"/api/admin/tenants/{tenant_id}/clients", json={})
    assert ok.status_code == 200
    blocked = client.post(f"/api/admin/tenants/{tenant_id}/clients", json={})
    assert blocked.status_code == 402


def test_add_client_404s_for_an_unknown_tenant(client, db_session):
    _make_admin(db_session, client)
    resp = client.post("/api/admin/tenants/does-not-exist/clients", json={})
    assert resp.status_code == 404


def test_list_clients_returns_every_active_client_id(client, db_session):
    tenant_id = _make_admin(db_session, client)
    client.post(f"/api/admin/tenants/{tenant_id}/clients", json={"client_id": "acme"})
    client.post(f"/api/admin/tenants/{tenant_id}/clients", json={"client_id": "zenith"})
    resp = client.get(f"/api/admin/tenants/{tenant_id}/clients")
    assert resp.status_code == 200
    assert set(resp.json()["client_ids"]) == {"acme", "zenith"}


def test_remove_client_deletes_both_schedule_and_data_context(client, db_session):
    tenant_id = _make_admin(db_session, client)
    client.post(f"/api/admin/tenants/{tenant_id}/clients", json={"client_id": "acme"})
    resp = client.delete(f"/api/admin/tenants/{tenant_id}/clients/acme")
    assert resp.status_code == 200
    assert scheduler.load_schedule(tenant_id, "acme") is None
    assert data_context.load_data_context(tenant_id, "acme") is None
    tenants = client.get("/api/admin/tenants").json()["tenants"]
    assert next(t for t in tenants if t["id"] == tenant_id)["active_clients"] == 0


def test_remove_client_404s_when_not_present(client, db_session):
    tenant_id = _make_admin(db_session, client)
    resp = client.delete(f"/api/admin/tenants/{tenant_id}/clients/never-added")
    assert resp.status_code == 404


def test_remove_client_404s_for_an_unknown_tenant(client, db_session):
    _make_admin(db_session, client)
    resp = client.delete("/api/admin/tenants/does-not-exist/clients/x")
    assert resp.status_code == 404


def test_a_non_admin_cannot_add_or_remove_clients(client, db_session):
    tenant_id = seed_tenant(db_session, client, google_sub="g-plain", email="plain@agency.com")
    assert client.post(f"/api/admin/tenants/{tenant_id}/clients", json={}).status_code == 403
    assert client.delete(f"/api/admin/tenants/{tenant_id}/clients/x").status_code == 403


def test_add_client_from_admin_is_visible_to_the_tenants_own_session(client, db_session):
    """The whole point: this is real data, not admin-only bookkeeping -- a
    client added here shows up through the tenant owner's own normal,
    non-admin session exactly like one they added themselves."""
    from fastapi.testclient import TestClient

    from app.main import app

    admin_tenant_id = _make_admin(db_session, client)
    owner_client = TestClient(app)
    owner_tenant_id = seed_tenant(db_session, owner_client, google_sub="g-owner2", email="owner2@northlight.com")
    assert owner_tenant_id != admin_tenant_id

    client.post(f"/api/admin/tenants/{owner_tenant_id}/clients", json={"client_id": "acme"})

    resp = owner_client.get("/api/data-sources/acme")
    assert resp.status_code == 200
    assert resp.json()["client_id"] == "acme"
