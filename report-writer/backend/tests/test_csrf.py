"""
Tests for Slice 5 — CSRF (double-submit cookie): a non-httpOnly `rp_csrf`
cookie, echoed as `X-CSRF-Token` on every non-GET/HEAD/OPTIONS request,
compared server-side with hmac.compare_digest (see auth.require_csrf).

Only matters for session-cookie auth -- a malicious page can ride a
victim's browser cookies into a forged request, but it can't read a
cookie's value to forge a matching header. A machine caller (X-API-Key /
scheduler service token) carries no session cookie at all and is exempt --
see auth.require_csrf's own docstring for the full reasoning.

tests/conftest.py's seed_tenant() already sets a matching CSRF cookie +
default X-CSRF-Token header so every OTHER test file's session-authenticated
POST/DELETE calls keep working unchanged -- this file tests the mechanism
itself, deliberately bypassing that convenience.
"""
from fastapi.testclient import TestClient

from app import auth
from app.main import app
from tests.conftest import seed_tenant


def test_get_requests_never_require_a_csrf_token(client, db_session):
    seed_tenant(db_session, client, google_sub="g-1", email="a@x.com")
    client.headers.pop("X-CSRF-Token", None)  # seed_tenant's convenience default -- deliberately removed
    resp = client.get("/api/schedules")
    assert resp.status_code == 200


def test_post_with_a_session_cookie_and_no_csrf_header_is_rejected(client, db_session):
    seed_tenant(db_session, client, google_sub="g-1", email="a@x.com")
    client.headers.pop("X-CSRF-Token", None)
    resp = client.post("/api/schedules/run", params={"dry_run": "true"})
    assert resp.status_code == 403
    assert "CSRF" in resp.json()["detail"]


def test_post_with_a_session_cookie_and_a_mismatched_csrf_header_is_rejected(client, db_session):
    seed_tenant(db_session, client, google_sub="g-1", email="a@x.com")
    client.headers["X-CSRF-Token"] = "wrong-value"
    resp = client.post("/api/schedules/run", params={"dry_run": "true"})
    assert resp.status_code == 403


def test_post_with_a_session_cookie_and_the_matching_csrf_header_succeeds(client, db_session):
    # seed_tenant already wires up the matching cookie + header default --
    # the common case every other test file relies on implicitly.
    seed_tenant(db_session, client, google_sub="g-1", email="a@x.com")
    resp = client.post("/api/schedules/run", params={"dry_run": "true"})
    assert resp.status_code == 200


def test_a_missing_csrf_cookie_with_a_present_header_is_rejected(client, db_session):
    """The header alone proves nothing -- an attacker's page could guess or
    fabricate ANY header value; what it can't do is read the real cookie."""
    seed_tenant(db_session, client, google_sub="g-1", email="a@x.com")
    client.cookies.delete(auth.settings.csrf_cookie_name)
    resp = client.post("/api/schedules/run", params={"dry_run": "true"})
    assert resp.status_code == 403


def test_a_request_with_no_session_cookie_at_all_is_exempt_from_csrf():
    """A machine caller (X-API-Key) never had a session cookie to protect
    in the first place -- CSRF must never block it, only auth should."""
    anon = TestClient(app)
    resp = anon.post("/api/schedules/run", headers={"X-API-Key": "not-a-real-token"})
    # 401 (bad credentials), never 403 (CSRF) -- proves CSRF got out of the way.
    assert resp.status_code == 401


def test_a_valid_service_token_request_is_exempt_from_csrf(monkeypatch):
    from app.settings import settings as app_settings
    monkeypatch.setattr(app_settings, "scheduler_service_token", "cron-secret")
    anon = TestClient(app)
    resp = anon.post(
        "/api/schedules/run", params={"dry_run": "true"}, headers={"X-Scheduler-Token": "cron-secret"},
    )
    assert resp.status_code == 200


def test_cors_preflight_is_never_blocked_by_csrf():
    anon = TestClient(app)
    resp = anon.options(
        "/api/schedules/run",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert resp.status_code == 200


def test_google_callback_issues_a_csrf_cookie_alongside_the_session_cookie(client, db_session, monkeypatch):
    async def _fake_exchange(_request):
        return {"sub": "g-csrf-test", "email": "a@northlight.com", "name": "A"}

    monkeypatch.setattr(auth, "_exchange_code_for_profile", _fake_exchange)
    resp = client.get("/api/auth/google/callback", follow_redirects=False)
    assert auth.settings.csrf_cookie_name in resp.cookies
    assert auth.settings.session_cookie_name in resp.cookies


def test_logout_clears_the_csrf_cookie_too(client, db_session):
    seed_tenant(db_session, client, google_sub="g-1", email="a@x.com")
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    # A cleared cookie is sent back with an expiry in the past / empty value.
    set_cookie_headers = resp.headers.get_list("set-cookie")
    csrf_clear = next(h for h in set_cookie_headers if h.startswith(f"{auth.settings.csrf_cookie_name}="))
    assert f'{auth.settings.csrf_cookie_name}=""' in csrf_clear or f"{auth.settings.csrf_cookie_name}=;" in csrf_clear


# ---------------------------------------------------------------------------
# SameSite is configurable (settings.session_cookie_samesite), not hardcoded
# -- required for a deploy topology where frontend/backend end up on two
# UNRELATED domains (see settings.py's own docstring on this field): a Lax
# cookie is never sent on cross-site fetch()/XHR, only top-level navigations.
# ---------------------------------------------------------------------------

def _issue_cookies(client, monkeypatch):
    async def _fake_exchange(_request):
        return {"sub": "g-samesite-test", "email": "a@northlight.com", "name": "A"}

    monkeypatch.setattr(auth, "_exchange_code_for_profile", _fake_exchange)
    resp = client.get("/api/auth/google/callback", follow_redirects=False)
    return resp.headers.get_list("set-cookie")


def test_default_samesite_is_lax_on_both_cookies(client, db_session, monkeypatch):
    set_cookie_headers = _issue_cookies(client, monkeypatch)
    session_header = next(h for h in set_cookie_headers if h.startswith(f"{auth.settings.session_cookie_name}="))
    csrf_header = next(h for h in set_cookie_headers if h.startswith(f"{auth.settings.csrf_cookie_name}="))
    assert "samesite=lax" in session_header.lower()
    assert "samesite=lax" in csrf_header.lower()


def test_samesite_none_is_honored_on_both_cookies(client, db_session, monkeypatch):
    """The cross-domain deploy topology (see settings.py) -- flipping this
    one setting must actually change what's on the wire, not just exist as
    an unused config field."""
    monkeypatch.setattr(auth.settings, "session_cookie_samesite", "none")
    set_cookie_headers = _issue_cookies(client, monkeypatch)
    session_header = next(h for h in set_cookie_headers if h.startswith(f"{auth.settings.session_cookie_name}="))
    csrf_header = next(h for h in set_cookie_headers if h.startswith(f"{auth.settings.csrf_cookie_name}="))
    assert "samesite=none" in session_header.lower()
    assert "samesite=none" in csrf_header.lower()
