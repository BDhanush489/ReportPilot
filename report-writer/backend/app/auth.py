"""
Track E1 — Google OAuth login + DB-backed sessions.

Two distinct cookies, never to be confused:
  - `rp_oauth_state` (Starlette's SessionMiddleware, itsdangerous-signed):
    exists only to survive the redirect round-trip to Google and back
    (state/nonce/PKCE verifier). Short-lived, unrelated to app identity.
  - `{settings.session_cookie_name}` (this app's real session, set by
    _set_session_cookie below): an opaque, high-entropy random token. Only
    its sha256 hash is ever stored (AuthSession.token_hash) -- a DB leak
    alone can't be replayed as a live session, the same reasoning as
    storing a password hash instead of a password. Deleting the DB row IS
    logout: a real, revocable session, not a stateless JWT that stays valid
    until expiry no matter what.

The one genuinely untestable step is the live HTTP round-trip to Google
(_exchange_code_for_profile) -- isolated into its own function so tests can
monkeypatch exactly that, rather than trying to drive a real OAuth handshake
through TestClient. Everything else here (tenant auto-creation, session
create/resolve/expire, /me, /logout) is plain Python + DB, fully testable.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .db import get_db
from .models import ApiToken, AuthSession, Membership, Tenant, User
from .settings import settings

router = APIRouter()

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "workspace"


def _unique_slug(db: Session, base_text: str) -> str:
    base = _slugify(base_text)
    slug = base
    suffix = 1
    while db.query(Tenant).filter_by(slug=slug).first() is not None:
        suffix += 1
        slug = f"{base}-{suffix}"
    return slug


def get_or_create_user_and_tenant(
    db: Session, *, google_sub: str, email: str, name: str = "", avatar_url: str | None = None,
) -> tuple[User, Tenant]:
    """First login for a given `google_sub` auto-creates a brand-new Tenant
    + an "owner" Membership -- there's no invite flow yet, so this is the
    only way anyone gets a workspace. Real, stated limitation (not hidden):
    a second person at the same agency who signs in gets their OWN separate
    tenant, not membership in a colleague's -- see the E1 CHANGELOG entry.
    A repeat login (same google_sub) always resolves to the SAME tenant,
    via that first Membership row, never creates a second one.

    Keyed on google_sub, never email: Google allows an account's email to
    change, so keying identity on email would let a changed/reassigned
    address silently take over a different user's account."""
    user = db.query(User).filter_by(google_sub=google_sub).one_or_none()
    if user is not None:
        membership = (
            db.query(Membership).filter_by(user_id=user.id).order_by(Membership.created_at).first()
        )
        tenant = db.get(Tenant, membership.tenant_id) if membership else None
        if tenant is not None:
            _maybe_bootstrap_platform_admin(db, user)
            return user, tenant
        # A user row with no membership at all shouldn't happen via this
        # function (every creation path below adds one), but never silently
        # invent a tenant for an inconsistent row -- fail loud instead.
        raise RuntimeError(f"user {user.id} has no tenant membership")

    user = User(google_sub=google_sub, email=email, name=name, avatar_url=avatar_url)
    db.add(user)
    db.flush()  # assigns user.id without committing yet

    tenant_name = f"{(name or email.split('@')[0]).strip()}'s Workspace"
    tenant = Tenant(name=tenant_name, slug=_unique_slug(db, tenant_name))
    db.add(tenant)
    db.flush()

    db.add(Membership(user_id=user.id, tenant_id=tenant.id, role="owner"))
    db.commit()
    db.refresh(user)
    db.refresh(tenant)
    _maybe_bootstrap_platform_admin(db, user)
    return user, tenant


def _maybe_bootstrap_platform_admin(db: Session, user: User) -> None:
    """The ONLY way the very first platform admin gets in -- there's no
    existing admin yet to promote them otherwise. Checked on every login
    (not just account creation), so setting PLATFORM_ADMIN_EMAILS and
    having that person sign in again is enough; no direct DB edit needed.
    One-directional: never demotes -- removing an email from the env var
    later does not revoke admin rights already granted (that's what
    POST /api/admin/users/{id}/demote is for, a real DB-backed action)."""
    if user.is_platform_admin:
        return
    if user.email.strip().lower() in settings.platform_admin_email_list:
        user.is_platform_admin = True
        db.commit()


def create_session(db: Session, user: User, tenant: Tenant) -> str:
    """Returns the RAW token (the only time it ever exists in plaintext) --
    the caller sets it as the cookie value; only its hash is persisted."""
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.session_ttl_days)
    db.add(AuthSession(token_hash=token_hash, user_id=user.id, tenant_id=tenant.id, expires_at=expires_at))
    db.commit()
    return raw_token


def get_current_session(db: Session, raw_token: str | None) -> AuthSession | None:
    if not raw_token:
        return None
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    session = db.query(AuthSession).filter_by(token_hash=token_hash).one_or_none()
    if session is None:
        return None
    expires_at = session.expires_at
    if expires_at.tzinfo is None:  # SQLite round-trips naive datetimes
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        return None
    return session


#: Prefix on the raw token value only (never part of what's hashed/stored,
#: just a human/tooling hint -- same idea as "sk-"/"ghp_"-style prefixes) so
#: a token is recognizable at a glance in logs or a secret scanner.
API_TOKEN_PREFIX = "rp_live_"


def create_api_token(db: Session, tenant: Tenant, user: User, label: str = "") -> str:
    """For a non-browser caller tied to one tenant -- client_agent.exe
    (Ingestion Mode 1) is the motivating case, a scheduled job on a
    client's own machine with no way to run the OAuth/session-cookie flow.
    Returns the RAW token (the only time it ever exists in plaintext); only
    its hash is persisted, same reasoning as create_session."""
    raw_token = API_TOKEN_PREFIX + secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    db.add(ApiToken(token_hash=token_hash, tenant_id=tenant.id, created_by_user_id=user.id, label=label))
    db.commit()
    return raw_token


def get_api_token(db: Session, raw_token: str | None) -> ApiToken | None:
    """No expiry check -- see ApiToken's own docstring for why (a scheduled
    machine job needs to keep working indefinitely once configured).
    Stamps last_used_at on every successful resolution, so a tenant can
    tell a token that's actually in use apart from one they forgot about."""
    if not raw_token or not raw_token.startswith(API_TOKEN_PREFIX):
        return None
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    token = db.query(ApiToken).filter_by(token_hash=token_hash).one_or_none()
    if token is None:
        return None
    token.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return token


def _set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        raw_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.session_cookie_samesite,
        domain=settings.cookie_domain,
        max_age=settings.session_ttl_days * 86400,
        path="/",
    )


def _set_csrf_cookie(response: Response) -> None:
    """Track E1 Slice 5 -- the double-submit half of CSRF protection.
    Deliberately NOT httponly: frontend JS must be able to read this value
    to echo it back as X-CSRF-Token on non-GET requests (see
    require_csrf below and the frontend's src/lib/csrf.ts). A page on
    another origin can trick a browser into sending the session cookie
    automatically, but it can't read this cookie's value to forge a
    matching header -- that's the entire protection."""
    response.set_cookie(
        settings.csrf_cookie_name,
        secrets.token_urlsafe(32),
        httponly=False,
        secure=settings.cookie_secure,
        samesite=settings.session_cookie_samesite,
        domain=settings.cookie_domain,
        max_age=settings.session_ttl_days * 86400,
        path="/",
    )


# ---------------------------------------------------------------------------
# FastAPI dependencies -- Slice 4 wires get_tenant_id onto every tenant-
# scoped route. Defined here now so Slice 2 and Slice 4 don't duplicate the
# session-resolution logic.
# ---------------------------------------------------------------------------

def get_current_auth_session(request: Request, db: Session = Depends(get_db)) -> AuthSession:
    raw_token = request.cookies.get(settings.session_cookie_name)
    session = get_current_session(db, raw_token)
    if session is None:
        raise HTTPException(401, "Not authenticated.")
    return session


def get_tenant_id(request: Request, db: Session = Depends(get_db)) -> str:
    """Every tenant-scoped route's real gate. Two independent ways in, tried
    in order, either one resolving to a tenant_id:
      1. The browser session cookie (the common case -- a human at the web app).
      2. An X-API-Key header matching a live ApiToken (a non-browser machine
         caller, e.g. client_agent.exe, which has no browser to run the
         OAuth/session flow -- see ApiToken's own docstring).
    A session always wins when both happen to be present. Neither -> 401."""
    raw_cookie = request.cookies.get(settings.session_cookie_name)
    session = get_current_session(db, raw_cookie)
    if session is not None:
        return session.tenant_id

    api_key = request.headers.get("X-API-Key")
    token = get_api_token(db, api_key)
    if token is not None:
        return token.tenant_id

    raise HTTPException(401, "Not authenticated.")


def require_csrf(request: Request) -> None:
    """Track E1 Slice 5 -- applied app-wide (see main.py's FastAPI(...,
    dependencies=[Depends(require_csrf)])), not per-route, so a newly added
    POST/PUT/PATCH/DELETE route is protected by default rather than only
    when someone remembers to add it.

    Only matters for a SESSION-cookie-authenticated request: CSRF exists
    specifically to stop a malicious page from riding a victim's BROWSER
    cookies into an unwanted cross-site request. A machine caller
    presenting only an X-API-Key bearer credential (client_agent.exe,
    the scheduler's service token) was never vulnerable to that in the
    first place -- a malicious webpage cannot read or attach a secret it
    was never given -- so a request that carries no session cookie at all
    skips this check entirely and is left to get_tenant_id/the schedule-run
    scope resolver to authenticate (or reject) on its own terms."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if not request.cookies.get(settings.session_cookie_name):
        return

    cookie_token = request.cookies.get(settings.csrf_cookie_name)
    header_token = request.headers.get("X-CSRF-Token")
    if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
        raise HTTPException(403, "Missing or invalid CSRF token.")


def require_platform_admin(session: AuthSession = Depends(get_current_auth_session), db: Session = Depends(get_db)) -> User:
    """Gates ONLY the /api/admin/* surface below -- deliberately a totally
    separate check from get_tenant_id. A platform admin does NOT thereby
    get tenant_id access to every tenant's reports/schedules/data through
    the normal API; that would quietly undo the whole point of E1's
    structural tenant isolation. Session-only on purpose (Depends
    get_current_auth_session, not get_tenant_id) -- an ApiToken is a
    single-tenant machine credential and must never be usable to reach a
    cross-tenant admin surface, even for a tenant whose owner happens to
    also be a platform admin."""
    user = db.get(User, session.user_id)
    if user is None or not user.is_platform_admin:
        raise HTTPException(403, "Platform admin access required.")
    return user


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

async def _exchange_code_for_profile(request: Request) -> dict:
    """The one genuinely live-HTTP-dependent step. Isolated so tests
    monkeypatch this exact function rather than trying to drive a real
    OAuth handshake through TestClient."""
    token = await oauth.google.authorize_access_token(request)
    profile = token.get("userinfo")
    if profile is None:
        profile = await oauth.google.parse_id_token(request, token)
    return profile


@router.get("/api/auth/google/login")
async def google_login(request: Request):
    if not settings.google_client_id:
        raise HTTPException(503, "Google login is not configured (GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET unset).")
    redirect_uri = f"{settings.backend_base_url}/api/auth/google/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/api/auth/google/callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    profile = await _exchange_code_for_profile(request)
    user, tenant = get_or_create_user_and_tenant(
        db,
        google_sub=profile["sub"],
        email=profile["email"],
        name=profile.get("name", ""),
        avatar_url=profile.get("picture"),
    )
    raw_token = create_session(db, user, tenant)
    response = RedirectResponse(settings.frontend_url)
    _set_session_cookie(response, raw_token)
    _set_csrf_cookie(response)
    return response


@router.get("/api/auth/me")
def me(session: AuthSession = Depends(get_current_auth_session), db: Session = Depends(get_db)):
    from . import plans as plans_mod

    user = db.get(User, session.user_id)
    tenant = db.get(Tenant, session.tenant_id)
    membership = db.query(Membership).filter_by(user_id=session.user_id, tenant_id=session.tenant_id).one_or_none()
    return {
        "user": {
            "id": user.id, "email": user.email, "name": user.name, "avatar_url": user.avatar_url,
            "is_platform_admin": user.is_platform_admin,
        },
        "tenant": {"id": tenant.id, "name": tenant.name, "slug": tenant.slug, "plan": tenant.plan},
        "role": membership.role if membership else None,
        "plan": plans_mod.get_plan(tenant.plan).label,
    }


@router.post("/api/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token:
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        db.query(AuthSession).filter_by(token_hash=token_hash).delete()
        db.commit()
    response.delete_cookie(settings.session_cookie_name, domain=settings.cookie_domain, path="/")
    response.delete_cookie(settings.csrf_cookie_name, domain=settings.cookie_domain, path="/")
    return {"status": "logged_out"}


# ---------------------------------------------------------------------------
# API tokens for non-browser callers (client_agent.exe / Ingestion Mode 1) --
# session-authenticated only (Depends(get_current_auth_session), never
# get_tenant_id): minting or revoking a token is a human-at-the-browser
# action, not something an existing machine token should be able to do to
# itself. A token's own tenant_id is fixed at creation from the caller's
# CURRENT session -- never client-supplied, so tenant A can never mint a
# token for tenant B by passing a different id in the request body.
# ---------------------------------------------------------------------------

class CreateApiTokenRequest(BaseModel):
    label: str = ""


@router.post("/api/auth/tokens")
def create_token(
    req: CreateApiTokenRequest,
    session: AuthSession = Depends(get_current_auth_session),
    db: Session = Depends(get_db),
):
    user = db.get(User, session.user_id)
    tenant = db.get(Tenant, session.tenant_id)
    raw_token = create_api_token(db, tenant, user, label=req.label)
    return {
        # The ONLY response that ever carries the raw value -- gone the
        # moment this response is sent, same as a GitHub/GitLab personal
        # access token. The frontend must show this exactly once.
        "token": raw_token,
        "label": req.label,
    }


@router.get("/api/auth/tokens")
def list_tokens(session: AuthSession = Depends(get_current_auth_session), db: Session = Depends(get_db)):
    tokens = db.query(ApiToken).filter_by(tenant_id=session.tenant_id).order_by(ApiToken.created_at).all()
    return {
        "tokens": [
            {
                "id": t.id, "label": t.label,
                "created_at": t.created_at.isoformat(),
                "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
            }
            for t in tokens
        ]
    }


@router.delete("/api/auth/tokens/{token_id}")
def revoke_token(
    token_id: str,
    session: AuthSession = Depends(get_current_auth_session),
    db: Session = Depends(get_db),
):
    # Scoped to the caller's own tenant_id -- a token_id belonging to
    # another tenant is treated exactly like one that doesn't exist.
    deleted = db.query(ApiToken).filter_by(id=token_id, tenant_id=session.tenant_id).delete()
    db.commit()
    if not deleted:
        raise HTTPException(404, "API token not found.")
    return {"status": "revoked"}


# ---------------------------------------------------------------------------
# Platform admin: cross-tenant visibility + management. Everything below is
# gated by require_platform_admin (session-only -- see its own docstring),
# a deliberately SEPARATE surface from every tenant-scoped route: reading
# this data does not go through get_tenant_id, and nothing here reaches
# into a tenant's actual reports/schedules/data-sources. What a platform
# admin can see/do is identity- and billing-plan-level only: who exists,
# which tenant they're in, and what plan that tenant is on.
# ---------------------------------------------------------------------------

@router.get("/api/admin/users")
def admin_list_users(_admin: User = Depends(require_platform_admin), db: Session = Depends(get_db)):
    rows = (
        db.query(User, Membership, Tenant)
        .outerjoin(Membership, Membership.user_id == User.id)
        .outerjoin(Tenant, Tenant.id == Membership.tenant_id)
        .order_by(User.created_at)
        .all()
    )
    return {
        "users": [
            {
                "id": user.id, "email": user.email, "name": user.name,
                "is_platform_admin": user.is_platform_admin,
                "created_at": user.created_at.isoformat(),
                "tenant": {"id": tenant.id, "name": tenant.name, "plan": tenant.plan} if tenant else None,
                "role": membership.role if membership else None,
            }
            for user, membership, tenant in rows
        ]
    }


@router.post("/api/admin/users/{user_id}/promote")
def admin_promote_user(user_id: str, _admin: User = Depends(require_platform_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found.")
    user.is_platform_admin = True
    db.commit()
    return {"id": user.id, "email": user.email, "is_platform_admin": True}


@router.post("/api/admin/users/{user_id}/demote")
def admin_demote_user(user_id: str, admin: User = Depends(require_platform_admin), db: Session = Depends(get_db)):
    if user_id == admin.id:
        # Never let the last-known-good admin lock themselves out with a
        # misclick; demoting yourself requires a DIFFERENT admin to do it.
        raise HTTPException(400, "You cannot demote your own account. Ask another platform admin to do it.")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found.")
    user.is_platform_admin = False
    db.commit()
    return {"id": user.id, "email": user.email, "is_platform_admin": False}


@router.get("/api/admin/tenants")
def admin_list_tenants(_admin: User = Depends(require_platform_admin), db: Session = Depends(get_db)):
    from . import plans as plans_mod
    from . import scheduler as scheduler_mod

    tenants = db.query(Tenant).order_by(Tenant.created_at).all()
    out = []
    for tenant in tenants:
        member_count = db.query(Membership).filter_by(tenant_id=tenant.id).count()
        plan = plans_mod.get_plan(tenant.plan)
        active_clients = scheduler_mod.count_active_clients_for_tenant(tenant.id)
        out.append({
            "id": tenant.id, "name": tenant.name, "slug": tenant.slug,
            "plan": tenant.plan, "plan_label": plan.label,
            "member_count": member_count,
            "active_clients": active_clients,
            "max_active_clients": plan.max_active_clients,
            "created_at": tenant.created_at.isoformat(),
        })
    return {"tenants": out}


class SetTenantPlanRequest(BaseModel):
    plan: str


@router.post("/api/admin/tenants/{tenant_id}/plan")
def admin_set_tenant_plan(
    tenant_id: str, req: SetTenantPlanRequest,
    _admin: User = Depends(require_platform_admin), db: Session = Depends(get_db),
):
    from . import plans as plans_mod

    if req.plan not in plans_mod.PLANS:
        raise HTTPException(400, f"Unknown plan {req.plan!r}. Choose one of: {sorted(plans_mod.PLANS)}")
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(404, "Tenant not found.")
    tenant.plan = req.plan
    db.commit()
    return {"id": tenant.id, "plan": tenant.plan}


@router.get("/api/admin/tenants/{tenant_id}/clients")
def admin_list_clients(tenant_id: str, _admin: User = Depends(require_platform_admin), db: Session = Depends(get_db)):
    from . import scheduler as scheduler_mod

    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(404, "Tenant not found.")
    return {"client_ids": [s.client_id for s in scheduler_mod.list_schedules_for_tenant(tenant_id)]}


class AdminAddClientRequest(BaseModel):
    client_id: str | None = None  # auto-generated ("demo-client-NN") if omitted


@router.post("/api/admin/tenants/{tenant_id}/clients")
def admin_add_client(
    tenant_id: str, req: AdminAddClientRequest,
    _admin: User = Depends(require_platform_admin), db: Session = Depends(get_db),
):
    """Adds one active-client relationship (a placeholder data context + a
    schedule) to ANY tenant -- unlike POST /api/schedules (which only ever
    acts on the caller's own tenant, see get_tenant_id), this deliberately
    crosses tenant boundaries: it's an admin/demo-management tool, not
    something a real tenant owner can reach.

    Still enforces the SAME plan rules a real schedule creation would
    (can_schedule, max_active_clients) -- an admin tool that could silently
    exceed a tenant's own plan limits would make "the cap is really
    enforced" a lie the moment someone checks here instead of the real
    onboarding flow. A Solo-plan tenant (can_schedule=False) therefore
    402s here exactly like it would through the real UI."""
    from . import data_context, plans as plans_mod, scheduler as scheduler_mod

    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(404, "Tenant not found.")
    plan = plans_mod.get_plan(tenant.plan)
    if not plan.can_schedule:
        raise HTTPException(402, f"Recurring schedules aren't included in the {plan.label} plan.")

    existing = {s.client_id for s in scheduler_mod.list_schedules_for_tenant(tenant_id)}
    if req.client_id and req.client_id in existing:
        raise HTTPException(400, f"{tenant.name} already has an active client with id {req.client_id!r}.")
    if plan.max_active_clients is not None and len(existing) >= plan.max_active_clients:
        raise HTTPException(
            402,
            f"{tenant.name} is already at its {plan.label} plan's cap of {plan.max_active_clients} active clients.",
        )

    client_id = req.client_id
    if not client_id:
        n = len(existing) + 1
        while f"demo-client-{n:02d}" in existing:
            n += 1
        client_id = f"demo-client-{n:02d}"

    if not data_context.load_data_context(tenant_id, client_id):
        data_context.save_data_context(tenant_id, client_id, "sqlite", {"path": f"{client_id}.db"}, {})
    scheduler_mod.save_schedule(scheduler_mod.Schedule(
        tenant_id=tenant_id, client_id=client_id, data_source_ref=client_id, cadence="monthly",
    ))
    return {"tenant_id": tenant_id, "client_id": client_id,
            "active_clients": len(existing) + 1, "max_active_clients": plan.max_active_clients}


@router.delete("/api/admin/tenants/{tenant_id}/clients/{client_id}")
def admin_remove_client(
    tenant_id: str, client_id: str,
    _admin: User = Depends(require_platform_admin), db: Session = Depends(get_db),
):
    """Removes the schedule AND its data context together -- undoing
    exactly what admin_add_client created, so the tenant's active-clients
    count actually drops rather than leaving an orphaned schedule pointing
    at a data source that's still there."""
    from . import data_context, scheduler as scheduler_mod

    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(404, "Tenant not found.")
    existing = {s.client_id for s in scheduler_mod.list_schedules_for_tenant(tenant_id)}
    if client_id not in existing:
        raise HTTPException(404, f"{tenant.name} has no active client with id {client_id!r}.")
    scheduler_mod.delete_schedule(tenant_id, client_id)
    data_context.delete_data_context(tenant_id, client_id)
    return {"ok": True}
