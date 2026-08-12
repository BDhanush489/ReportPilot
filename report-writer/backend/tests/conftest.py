"""
Track E1 — shared test infrastructure for anything that needs a real,
session-authenticated HTTP call against app.main (every tenant-scoped route
now requires Depends(auth.get_tenant_id); there is no more standalone
X-API-Key bypass).

seed_tenant() is the workaround for not being able to automate a live
Google login in tests (see app/auth.py's own module docstring): it inserts
User+Tenant+Membership+AuthSession rows directly and seeds the TestClient's
cookie jar with a real, valid session token, exactly as app/auth.py's own
/google/callback route would after a real OAuth round-trip.

db_session ALSO repoints app.db.SessionLocal itself (not just FastAPI's
get_db dependency) to this same isolated engine -- report_store.py/
data_context.py/scheduler.py/alerts.py/delivery.py each open their own
short-lived session via `db_mod.SessionLocal()` directly (see scheduler.py's
module docstring for why: some callers, like a background report-generation
thread, have no FastAPI request to draw a Depends(get_db) session from at
all), so without this they'd silently fall through to the REAL on-disk
reportpilot.db instead of a test's throwaway one.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth
from app import db as db_module
from app import store_models  # noqa: F401 -- import registers the 6 store tables on Base.metadata
from app.db import Base, configure_sqlite_engine, get_db
from app.main import app


@pytest.fixture
def db_session(monkeypatch):
    # StaticPool: a bare `sqlite:///:memory:` gives each checked-out
    # connection its OWN empty in-memory database -- fine for a single
    # direct Session, but a real HTTP request through TestClient checks out
    # a connection separately from this fixture's own queries. StaticPool
    # forces one shared connection for the engine's lifetime, so the DB the
    # request handler sees is genuinely the same DB this fixture set up.
    engine = configure_sqlite_engine(create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    ))
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    # See this module's own docstring -- the 5 store modules call
    # db_mod.SessionLocal() directly, not through FastAPI's DI, so
    # overriding get_db alone (the `client` fixture below) isn't enough.
    monkeypatch.setattr(db_module, "SessionLocal", Session)
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


def seed_tenant(db_session, client: TestClient, *, google_sub: str, email: str, name: str = "") -> str:
    """Logs `client` in as a freshly-created user+tenant (or resolves to the
    existing one for a repeat google_sub) and returns the tenant_id.

    Also seeds a matching CSRF cookie + default X-CSRF-Token header on the
    client (Slice 5): a real /google/callback mints both the session and
    CSRF cookies together (see auth.py), and every session-authenticated
    POST/PUT/PATCH/DELETE now requires that header to match. Setting the
    header as a CLIENT DEFAULT here -- rather than on every individual
    request -- means every existing test written before Slice 5 keeps
    working unchanged.

    Plan: defaults new tenants to "solo" (see models.Tenant.plan), which
    can't schedule at all -- but most existing tests here predate plan
    gating and use scheduling incidentally to test something else entirely
    (onboarding, tenant isolation). Bumping the seeded tenant to "agency"
    (scheduling + PBIP both included, a high client cap) keeps their
    original behavior intact; dedicated plan-gating tests
    (tests/test_admin.py) construct a "solo" tenant explicitly instead."""
    user, tenant = auth.get_or_create_user_and_tenant(db_session, google_sub=google_sub, email=email, name=name)
    tenant.plan = "agency"
    db_session.commit()
    raw_token = auth.create_session(db_session, user, tenant)
    client.cookies.set(auth.settings.session_cookie_name, raw_token)

    csrf_value = "test-csrf-token"
    client.cookies.set(auth.settings.csrf_cookie_name, csrf_value)
    client.headers["X-CSRF-Token"] = csrf_value
    return tenant.id


def seed_aurora_home_goods_data_context() -> None:
    """Several test modules (test_pbip_export.py, test_pbip_live_connection.py,
    test_template_specs.py, test_template_selection.py,
    test_template_versioning.py, test_industry_packs.py) build a real
    ReportObject from a fixed tenant_id="demo-tenant" data context pointed
    at the real sample_data/aurora_warehouse.sqlite fixture warehouse.

    Before storage moved into the DB, this worked because a one-off
    onboarding call had left data_contexts/demo-tenant/aurora-home-goods.json
    sitting on disk locally -- a file that was never actually reproducible
    (data_contexts/ is gitignored) even before this migration, just
    ambient local state. This function replaces it: idempotent (an
    overwrite, not an insert-or-fail), safe to call unconditionally at the
    top of any fixture that needs this data context to exist, regardless
    of which DB app.db.SessionLocal currently resolves to."""
    from app import data_context

    warehouse_path = Path(__file__).resolve().parent.parent / "sample_data" / "aurora_warehouse.sqlite"
    data_context.save_data_context(
        "demo-tenant", "aurora-home-goods", "sqlite", {"path": str(warehouse_path)},
        {
            "analytics": {"table": "ga_sessions_daily", "column_map": {
                "date": "event_date", "channel_group": "channel", "device_category": "device",
                "sessions": "session_count", "new_users": "new_user_count",
                "engaged_sessions": "engaged_session_count", "conversions": "goal_completions",
                "revenue_usd": "total_revenue", "bounce_rate": "bounce_pct",
                "avg_session_duration_sec": "avg_duration_sec",
            }},
            "seo": {"table": "crawl_results", "column_map": {
                "url": "page_url", "status_code": "http_status", "is_indexable": "index_status",
                "load_time_ms": "ttfb_ms", "title_length": "title_length",
                "meta_description_length": "meta_description_length", "h1_count": "h1_count",
                "word_count": "word_count", "has_canonical": "has_canonical",
                "mobile_friendly": "mobile_friendly", "broken_internal_links": "broken_internal_links",
                "images_missing_alt": "images_missing_alt", "impressions_28d": "impressions",
                "clicks_28d": "clicks", "ctr": "ctr", "avg_position": "serp_position",
                "organic_sessions_28d": "organic_traffic", "issue_severity": "severity",
                "issues": "issue_notes",
            }},
            "sales": {"table": "crm_opportunities", "column_map": {
                "deal_id": "opp_id", "close_date": "closed_on", "sales_rep": "owner",
                "product": "sku", "region": "region", "lead_source": "channel",
                "deal_stage": "status", "amount_usd": "closed_amount",
                "potential_amount_usd": "pipeline_amount", "days_to_close": "days_to_close",
            }},
        },
    )
