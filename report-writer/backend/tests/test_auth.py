"""
Tests for Slice 2 (Track E1) — Google OAuth session issuance, not yet
enforced on any existing route.

The live HTTP round-trip to Google (_exchange_code_for_profile) is
genuinely untestable without real credentials — isolated in app/auth.py
specifically so it can be monkeypatched here instead of trying to drive a
real OAuth handshake through TestClient. Everything else (tenant
auto-creation, session create/resolve/expire, /me, /logout) is plain
Python + DB, tested directly and via real HTTP.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth
from app.db import Base, configure_sqlite_engine, get_db
from app.main import app
from app.models import ApiToken, AuthSession, Membership, Tenant, User


@pytest.fixture
def db_session():
    # StaticPool: a bare `sqlite:///:memory:` gives each checked-out
    # connection its OWN empty in-memory database -- fine for a single
    # direct Session (test_db_models.py's pattern), but a real HTTP request
    # through TestClient can check out a connection separately from this
    # fixture's own queries. StaticPool forces one shared connection for the
    # engine's lifetime, so the DB the request handler sees is genuinely the
    # same DB this fixture set up and asserts against.
    engine = configure_sqlite_engine(create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    ))
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# get_or_create_user_and_tenant: the tenant-auto-creation contract
# ---------------------------------------------------------------------------

def test_first_login_creates_a_user_and_a_tenant(db_session):
    user, tenant = auth.get_or_create_user_and_tenant(
        db_session, google_sub="g-1", email="alex@northlight.com", name="Alex", avatar_url="https://x/pic.jpg",
    )
    assert user.google_sub == "g-1"
    assert user.email == "alex@northlight.com"
    assert tenant.name == "Alex's Workspace"
    membership = db_session.query(Membership).filter_by(user_id=user.id, tenant_id=tenant.id).one()
    assert membership.role == "owner"


def test_repeat_login_with_the_same_google_sub_resolves_to_the_same_tenant(db_session):
    user1, tenant1 = auth.get_or_create_user_and_tenant(db_session, google_sub="g-1", email="a@x.com", name="A")
    user2, tenant2 = auth.get_or_create_user_and_tenant(db_session, google_sub="g-1", email="a@x.com", name="A")
    assert user1.id == user2.id
    assert tenant1.id == tenant2.id
    # Never a second membership row for the same repeat login.
    assert db_session.query(Membership).filter_by(user_id=user1.id).count() == 1


def test_a_different_google_sub_gets_an_isolated_second_tenant(db_session):
    """The stated limitation: two different people never share a tenant on
    first login (no invite flow yet) -- confirmed directly, not assumed."""
    _, tenant_a = auth.get_or_create_user_and_tenant(db_session, google_sub="g-a", email="a@northlight.com", name="A")
    _, tenant_b = auth.get_or_create_user_and_tenant(db_session, google_sub="g-b", email="b@northlight.com", name="B")
    assert tenant_a.id != tenant_b.id


def test_tenant_slugs_never_collide_even_with_the_same_display_name(db_session):
    _, tenant_a = auth.get_or_create_user_and_tenant(db_session, google_sub="g-a", email="a@x.com", name="Sam")
    _, tenant_b = auth.get_or_create_user_and_tenant(db_session, google_sub="g-b", email="b@x.com", name="Sam")
    assert tenant_a.slug != tenant_b.slug


def test_identity_is_keyed_on_google_sub_not_email():
    """A changed/reassigned email must never silently take over a
    different user's account -- google_sub is the only safe key."""
    engine = configure_sqlite_engine(create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    ))
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        user1, _ = auth.get_or_create_user_and_tenant(db, google_sub="g-1", email="old@x.com", name="A")
        # Same google_sub, email changed at Google's end -- must resolve to
        # the SAME user, not create a new one.
        user2, _ = auth.get_or_create_user_and_tenant(db, google_sub="g-1", email="new@x.com", name="A")
        assert user1.id == user2.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Session create / resolve / expire
# ---------------------------------------------------------------------------

def test_create_and_resolve_session_round_trips(db_session):
    user, tenant = auth.get_or_create_user_and_tenant(db_session, google_sub="g-1", email="a@x.com", name="A")
    raw_token = auth.create_session(db_session, user, tenant)
    resolved = auth.get_current_session(db_session, raw_token)
    assert resolved is not None
    assert resolved.user_id == user.id
    assert resolved.tenant_id == tenant.id


def test_the_raw_token_is_never_stored_only_its_hash(db_session):
    user, tenant = auth.get_or_create_user_and_tenant(db_session, google_sub="g-1", email="a@x.com", name="A")
    raw_token = auth.create_session(db_session, user, tenant)
    stored = db_session.query(AuthSession).one()
    assert stored.token_hash != raw_token
    assert len(stored.token_hash) == 64  # sha256 hex digest


def test_garbage_token_resolves_to_none(db_session):
    assert auth.get_current_session(db_session, "not-a-real-token") is None


def test_missing_token_resolves_to_none(db_session):
    assert auth.get_current_session(db_session, None) is None


def test_expired_session_resolves_to_none(db_session):
    user, tenant = auth.get_or_create_user_and_tenant(db_session, google_sub="g-1", email="a@x.com", name="A")
    raw_token = auth.create_session(db_session, user, tenant)
    # Backdate it directly, past expiry.
    row = db_session.query(AuthSession).one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.commit()
    assert auth.get_current_session(db_session, raw_token) is None


# ---------------------------------------------------------------------------
# /api/auth/me, /api/auth/logout -- real HTTP, session seeded directly
# (the workaround for not being able to automate a live Google login)
# ---------------------------------------------------------------------------

def _seed_session(client: TestClient, db_session) -> str:
    user, tenant = auth.get_or_create_user_and_tenant(db_session, google_sub="g-1", email="a@northlight.com", name="Alex")
    raw_token = auth.create_session(db_session, user, tenant)
    client.cookies.set(auth.settings.session_cookie_name, raw_token)
    # Slice 5: a matching CSRF cookie + default header, same reasoning as
    # tests/conftest.py's seed_tenant() -- see its docstring.
    csrf_value = "test-csrf-token"
    client.cookies.set(auth.settings.csrf_cookie_name, csrf_value)
    client.headers["X-CSRF-Token"] = csrf_value
    return raw_token


def test_me_requires_authentication(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_returns_the_authenticated_users_identity_and_tenant(client, db_session):
    _seed_session(client, db_session)
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == "a@northlight.com"
    assert body["tenant"]["name"] == "Alex's Workspace"
    assert body["role"] == "owner"


def test_logout_deletes_the_session_row_and_a_second_me_call_401s(client, db_session):
    _seed_session(client, db_session)
    assert client.get("/api/auth/me").status_code == 200

    logout_resp = client.post("/api/auth/logout")
    assert logout_resp.status_code == 200
    assert db_session.query(AuthSession).count() == 0

    # Same cookie jar, same (now-deleted) token -- must 401, not 200.
    assert client.get("/api/auth/me").status_code == 401


def test_login_without_google_credentials_configured_fails_loud(client, monkeypatch):
    """Forces settings.google_client_id blank regardless of whatever's in
    this developer's own .env -- tests must never depend on ambient local
    config to stay hermetic. Confirms the guard raises a clear 503, not a
    confusing Authlib exception surfacing as a 500."""
    monkeypatch.setattr(auth.settings, "google_client_id", "")
    resp = client.get("/api/auth/google/login", follow_redirects=False)
    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# The callback route, with the live Google HTTP hop mocked out
# ---------------------------------------------------------------------------

def test_callback_creates_a_session_and_redirects_to_the_frontend(client, db_session, monkeypatch):
    async def _fake_exchange(_request):
        return {"sub": "g-999", "email": "new@northlight.com", "name": "New User", "picture": "https://x/p.jpg"}

    monkeypatch.setattr(auth, "_exchange_code_for_profile", _fake_exchange)

    resp = client.get("/api/auth/google/callback", follow_redirects=False)
    assert resp.status_code in (302, 307)
    # /app specifically, not just the frontend's bare root -- "/" is the
    # marketing homepage now (report-writer/frontend merged it in), which
    # has no concept of auth state at all and would show "Sign in" either
    # way, making a successful login indistinguishable from a failed one.
    assert resp.headers["location"] == f"{auth.settings.frontend_url}/app"
    assert auth.settings.session_cookie_name in resp.cookies

    user = db_session.query(User).filter_by(google_sub="g-999").one()
    assert user.email == "new@northlight.com"
    assert db_session.query(AuthSession).filter_by(user_id=user.id).count() == 1


# ---------------------------------------------------------------------------
# API tokens: the non-browser path for client_agent.exe (Ingestion Mode 1),
# which has no browser to run the OAuth/session-cookie flow.
# ---------------------------------------------------------------------------

def test_create_and_resolve_api_token_round_trips(db_session):
    user, tenant = auth.get_or_create_user_and_tenant(db_session, google_sub="g-1", email="a@x.com", name="A")
    raw_token = auth.create_api_token(db_session, tenant, user, label="Acme agent")
    assert raw_token.startswith(auth.API_TOKEN_PREFIX)

    resolved = auth.get_api_token(db_session, raw_token)
    assert resolved is not None
    assert resolved.tenant_id == tenant.id
    assert resolved.label == "Acme agent"


def test_the_raw_api_token_is_never_stored_only_its_hash(db_session):
    user, tenant = auth.get_or_create_user_and_tenant(db_session, google_sub="g-1", email="a@x.com", name="A")
    raw_token = auth.create_api_token(db_session, tenant, user)
    stored = db_session.query(ApiToken).one()
    assert stored.token_hash != raw_token
    assert len(stored.token_hash) == 64  # sha256 hex digest


def test_garbage_api_token_resolves_to_none(db_session):
    assert auth.get_api_token(db_session, "not-a-real-token") is None
    assert auth.get_api_token(db_session, None) is None


def test_resolving_an_api_token_stamps_last_used_at(db_session):
    user, tenant = auth.get_or_create_user_and_tenant(db_session, google_sub="g-1", email="a@x.com", name="A")
    raw_token = auth.create_api_token(db_session, tenant, user)
    assert db_session.query(ApiToken).one().last_used_at is None

    auth.get_api_token(db_session, raw_token)
    assert db_session.query(ApiToken).one().last_used_at is not None


def test_an_api_token_never_expires():
    """Unlike a browser session, a scheduled machine job needs to keep
    working indefinitely once configured -- see ApiToken's own docstring."""
    assert not hasattr(ApiToken, "expires_at")


# ---------------------------------------------------------------------------
# get_tenant_id's dual path, exercised over real HTTP against a real
# tenant-scoped route (/api/schedules -- any would do).
# ---------------------------------------------------------------------------

def test_get_tenant_id_accepts_a_valid_x_api_key_with_no_session_cookie(client, db_session):
    user, tenant = auth.get_or_create_user_and_tenant(db_session, google_sub="g-1", email="a@x.com", name="A")
    raw_token = auth.create_api_token(db_session, tenant, user)
    resp = client.get("/api/schedules", headers={"X-API-Key": raw_token})
    assert resp.status_code == 200


def test_get_tenant_id_rejects_a_garbage_x_api_key(client):
    resp = client.get("/api/schedules", headers={"X-API-Key": "not-a-real-token"})
    assert resp.status_code == 401


def test_get_tenant_id_prefers_the_session_cookie_when_both_are_present(client, db_session):
    """A real X-API-Key for tenant B alongside a session cookie for tenant A
    -- the session must win, never silently switch tenants based on a
    header a browser page could also be tricked into sending."""
    _, tenant_a = auth.get_or_create_user_and_tenant(db_session, google_sub="g-a", email="a@x.com", name="A")
    raw_a = auth.create_session(db_session, db_session.query(User).filter_by(google_sub="g-a").one(), tenant_a)
    client.cookies.set(auth.settings.session_cookie_name, raw_a)

    user_b, tenant_b = auth.get_or_create_user_and_tenant(db_session, google_sub="g-b", email="b@x.com", name="B")
    raw_token_b = auth.create_api_token(db_session, tenant_b, user_b)

    resp = client.get("/api/auth/me", headers={"X-API-Key": raw_token_b})
    assert resp.status_code == 200
    assert resp.json()["tenant"]["id"] == tenant_a.id  # session wins, not the header's tenant


# ---------------------------------------------------------------------------
# Token management routes -- session-authenticated only, never usable by an
# existing API token (minting/revoking is a human-at-the-browser action).
# ---------------------------------------------------------------------------

def test_create_token_requires_a_session_not_an_api_key(client, db_session):
    user, tenant = auth.get_or_create_user_and_tenant(db_session, google_sub="g-1", email="a@x.com", name="A")
    raw_token = auth.create_api_token(db_session, tenant, user)
    resp = client.post("/api/auth/tokens", json={"label": "x"}, headers={"X-API-Key": raw_token})
    assert resp.status_code == 401  # an API token can't mint another token for itself


def test_create_token_returns_the_raw_value_exactly_once(client, db_session):
    _seed_session(client, db_session)
    resp = client.post("/api/auth/tokens", json={"label": "Acme agent"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"].startswith(auth.API_TOKEN_PREFIX)
    assert body["label"] == "Acme agent"


def test_list_tokens_never_leaks_the_raw_or_hashed_value(client, db_session):
    _seed_session(client, db_session)
    client.post("/api/auth/tokens", json={"label": "Acme agent"})
    resp = client.get("/api/auth/tokens")
    assert resp.status_code == 200
    tokens = resp.json()["tokens"]
    assert len(tokens) == 1
    assert tokens[0]["label"] == "Acme agent"
    assert "token" not in tokens[0] and "token_hash" not in tokens[0]


def test_list_tokens_never_shows_another_tenants_tokens(client, db_session):
    _seed_session(client, db_session)  # tenant A
    client.post("/api/auth/tokens", json={"label": "A's agent"})

    user_b, tenant_b = auth.get_or_create_user_and_tenant(db_session, google_sub="g-b", email="b@x.com", name="B")
    auth.create_api_token(db_session, tenant_b, user_b, label="B's agent")

    resp = client.get("/api/auth/tokens")
    labels = [t["label"] for t in resp.json()["tokens"]]
    assert labels == ["A's agent"]


def test_revoked_token_can_no_longer_authenticate(client, db_session):
    _seed_session(client, db_session)
    raw_token = client.post("/api/auth/tokens", json={"label": "x"}).json()["token"]
    token_id = client.get("/api/auth/tokens").json()["tokens"][-1]["id"]

    # A cookie-less client -- otherwise the session cookie set by
    # _seed_session (which the dual-path check tries first) would mask
    # whether the API key itself still works.
    agent = TestClient(app)
    assert agent.get("/api/schedules", headers={"X-API-Key": raw_token}).status_code == 200
    assert client.delete(f"/api/auth/tokens/{token_id}").status_code == 200
    assert agent.get("/api/schedules", headers={"X-API-Key": raw_token}).status_code == 401


def test_revoking_another_tenants_token_404s_not_a_cross_tenant_delete(client, db_session):
    _seed_session(client, db_session)  # tenant A
    user_b, tenant_b = auth.get_or_create_user_and_tenant(db_session, google_sub="g-b", email="b@x.com", name="B")
    other_raw = auth.create_api_token(db_session, tenant_b, user_b)
    other_id = db_session.query(ApiToken).filter_by(tenant_id=tenant_b.id).one().id

    resp = client.delete(f"/api/auth/tokens/{other_id}")
    assert resp.status_code == 404
    # And it's genuinely untouched -- B's token still authenticates.
    assert auth.get_api_token(db_session, other_raw) is not None
