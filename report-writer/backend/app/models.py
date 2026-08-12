"""
Track E1 — the four tables real auth/tenancy needs: User, Tenant,
Membership, AuthSession. This module owns only the identity/access-control
layer. Everything else this app persists (reports, schedules, data
contexts, alert rules, delivery logs) lives in app/store_models.py instead
-- a deliberately separate module, kept out of here to preserve this file's
narrower scope, even though both now back onto the same database (see
report_store.py / scheduler.py / data_context.py / alerts.py / delivery.py,
which own the DB session handling for their own tables in store_models.py).

Naming note: AuthSession, not "Session" -- sqlalchemy.orm.Session already
owns that name; colliding with it in the same codebase is a real footgun,
not just a style preference.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    #: Google's stable per-account identifier ("sub" claim) -- the ONLY safe
    #: key for identity lookups. Google explicitly allows email to change on
    #: an account; keying on email would let a changed/reassigned email
    #: silently take over a different user's account.
    google_sub: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str] = mapped_column(String(255), default="")
    avatar_url: Mapped[str | None] = mapped_column(String(1024), default=None)
    #: Platform-wide, cross-tenant admin -- a DIFFERENT, more powerful thing
    #: than a Membership.role="owner" (which is scoped to one tenant). False
    #: for everyone by default; the first one is bootstrapped from the
    #: PLATFORM_ADMIN_EMAILS env var on login (see
    #: auth.get_or_create_user_and_tenant), every one after that is minted
    #: by an existing platform admin via POST /api/admin/users/{id}/promote.
    #: Deliberately never checked by get_tenant_id or anything tenant-scoped
    #: -- it only gates the separate /api/admin/* surface (see
    #: auth.require_platform_admin), so a platform admin does NOT
    #: automatically get access to every tenant's reports/data through the
    #: normal API; that would defeat the whole point of E1's tenant isolation.
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # passive_deletes=True: trust the DB's own ON DELETE CASCADE (see
    # models.py's FKs + db.py's PRAGMA foreign_keys=ON) instead of
    # SQLAlchemy's default ORM-level emulation, which tries to UPDATE
    # child rows' FK to NULL first -- that fails outright here since
    # tenant_id/user_id are NOT NULL, and duplicates cascade logic the
    # database already owns.
    memberships: Mapped[list["Membership"]] = relationship(back_populates="user", passive_deletes=True)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    #: One of app/plans.py's PLANS keys ("solo" | "agency" | "inhouse").
    #: Every new tenant starts on "solo" (the lowest paid tier's "Start
    #: free" trial) -- there's no real billing/Stripe integration yet (still
    #: out of scope), so this is set here directly by a platform admin via
    #: POST /api/admin/tenants/{id}/plan rather than by a checkout flow.
    plan: Mapped[str] = mapped_column(String(32), default="solo")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    memberships: Mapped[list["Membership"]] = relationship(back_populates="tenant", passive_deletes=True)


class Membership(Base):
    """user <-> tenant, with a role. Forward-built for a future invite flow
    (T4-style: the schema exists now, nothing creates a "member" row or
    checks role yet -- every tenant today has exactly one "owner", created
    at first login. Stated as a real limitation, not hidden: see the E1
    CHANGELOG entry."""
    __tablename__ = "memberships"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32), default="owner")  # "owner" | "member"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped["User"] = relationship(back_populates="memberships")
    tenant: Mapped["Tenant"] = relationship(back_populates="memberships")


class AuthSession(Base):
    """A signed-in session. The raw cookie value is `secrets.token_urlsafe(32)`
    -- never stored. Only its sha256 hash lives here (token_hash), so a
    database leak alone can't be replayed as a live session, the same
    reasoning as storing a password hash instead of a password. Deleting a
    row here IS logout -- this is a real, revocable session, not a
    stateless JWT that stays valid until it expires no matter what."""
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApiToken(Base):
    """A long-lived, non-browser credential tied to exactly one tenant -- for
    a machine caller that can't run the OAuth/session-cookie flow because
    there's no browser involved (client_agent.exe, Ingestion Mode 1, is the
    motivating case: a scheduled Task Scheduler job on a CLIENT's own
    machine, pushing files to POST /api/generate-report unattended).

    Same at-rest pattern as AuthSession, and for the same reason: the raw
    value (`rp_live_` + secrets.token_urlsafe(32)) is only ever returned
    once, at creation; only its sha256 hash is stored here, so a DB leak
    alone can't be replayed. Presented as an X-API-Key header and resolved
    to a tenant_id by auth.get_tenant_id, which tries the session cookie
    first and only falls back to this table if there isn't one -- a browser
    session always wins when both are somehow present.

    No expires_at: unlike a browser session (which should naturally time
    out), a scheduled machine job needs to keep working indefinitely once
    configured. Deleting a row here IS revocation -- there is no separate
    "disabled" flag to remember to check on every request."""
    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    #: Human label so a tenant with several agents/integrations can tell
    #: their tokens apart in a list -- e.g. "Acme Corp client agent".
    label: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    #: Stamped on every successful use (see auth.py) -- lets a tenant tell a
    #: token that's actually in use apart from one they forgot about, before
    #: they revoke it.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
