"""
Track E1 — centralized config for the auth/tenancy surface specifically.

pydantic-settings was already a dependency (requirements.txt) but unused
anywhere in the codebase before this — the rest of the app keeps its
existing `os.environ.get(...)` style unchanged; this is only for the new
security-sensitive surface (OAuth, sessions, cookies, CORS), where having
every value validated and documented in one place matters more than it does
for e.g. OLLAMA_BASE_URL.

Every value below is env-driven on purpose: this app runs local-dev-only
today (localhost:3000 / localhost:8000), but redirect URIs, CORS origins,
and cookie flags all need to become "just a config change" the day a real
domain exists, not a code change.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Database -----------------------------------------------------
    # SQLite by default (zero local install, matches "local dev now"). A
    # later Postgres migration is a DATABASE_URL change + a new Alembic
    # target, not a rewrite -- see app/db.py.
    database_url: str = "sqlite:///./reportpilot.db"

    # --- Google OAuth ---------------------------------------------------
    # Blank by default so the app still imports/runs without them (matches
    # this app's existing "missing key -> feature unavailable, not a crash"
    # convention, e.g. ANTHROPIC_API_KEY) -- app/auth.py itself decides what
    # "not configured" means for each route.
    google_client_id: str = ""
    google_client_secret: str = ""

    # --- URLs -----------------------------------------------------------
    # Where THIS backend is reachable (used to build the OAuth redirect_uri)
    # and where the frontend lives (used for the post-login redirect and as
    # the CORS allowlist). Both must be exact — OAuth redirect URIs are
    # matched literally by Google, no wildcards.
    backend_base_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:3000"
    #: Comma-separated list, so a real deployment can allow e.g. both an
    #: apex and a www subdomain without a code change.
    frontend_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # --- Session cookie ---------------------------------------------------
    session_cookie_name: str = "rp_session"
    session_ttl_days: int = 30
    #: False for local http:// dev; must be True the moment this is served
    #: over https (browsers silently drop Secure cookies over plain http,
    #: so this can't just default True and "happen to work" locally).
    cookie_secure: bool = False
    #: None lets the browser default to the exact serving host (correct for
    #: localhost). Set to e.g. ".yourdomain.com" once frontend/backend are
    #: subdomains of one real domain, so the cookie is shared between them.
    cookie_domain: str | None = None
    #: "lax" (default) works for localhost and for a same-parent-domain
    #: deploy (cookie_domain set above). It does NOT work if frontend and
    #: backend end up on two UNRELATED domains (e.g. two different free
    #: platform subdomains, *.vercel.app + *.onrender.com) -- a Lax cookie
    #: is never sent on cross-site fetch()/XHR, only top-level navigations,
    #: so login would appear to succeed (the redirect completes) while
    #: every subsequent API call silently arrives unauthenticated. Set to
    #: "none" for that topology -- SameSite=None requires Secure=True on
    #: the cookie, which cookie_secure above should already be True for
    #: any real (https) deploy regardless.
    session_cookie_samesite: str = "lax"

    # --- CSRF -------------------------------------------------------------
    csrf_cookie_name: str = "rp_csrf"

    # --- OAuth handshake (Authlib's own short-lived signed cookie, NOT the
    # app session -- see app/auth.py's module docstring) -------------------
    oauth_state_secret_key: str = "dev-only-insecure-key-change-me"

    # --- Service-to-service (e.g. a cron trigger calling
    # POST /api/schedules/run with no human session) -----------------------
    scheduler_service_token: str = ""

    # --- Platform admin bootstrap ------------------------------------------
    # Comma-separated emails. On login (see auth.get_or_create_user_and_tenant),
    # a matching, not-yet-admin user is promoted to User.is_platform_admin
    # automatically -- this is ONLY how the very first platform admin gets
    # in the door (there's no other user to promote them). Every admin after
    # that is minted by an existing one via POST /api/admin/users/{id}/promote,
    # a real DB-backed action, not this env var -- so removing an email here
    # later does NOT revoke admin rights already granted.
    platform_admin_emails: str = ""

    @property
    def platform_admin_email_list(self) -> list[str]:
        return [e.strip().lower() for e in self.platform_admin_emails.split(",") if e.strip()]

    @property
    def frontend_origin_list(self) -> list[str]:
        return [o.strip() for o in self.frontend_origins.split(",") if o.strip()]


settings = Settings()
