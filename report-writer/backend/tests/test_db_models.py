"""
Tests for Slice 1 (Track E1) — the DB foundation itself: models round-trip,
constraints hold, nothing existing is touched.

Uses an in-memory SQLite DB created directly from Base.metadata (not via
Alembic) -- this is the standard, fast pattern for model-level tests; the
migration file itself is verified separately (see test_alembic_migration.py-
style manual check in the E1 CHANGELOG entry / README) since autogenerate
already ran cleanly against a real file during development.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db import Base, configure_sqlite_engine
from app.models import AuthSession, Membership, Tenant, User


@pytest.fixture
def db():
    engine = configure_sqlite_engine(create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool))
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def test_user_round_trips(db):
    user = User(google_sub="g-123", email="a@example.com", name="Alex")
    db.add(user)
    db.commit()
    db.refresh(user)
    assert user.id  # default factory assigned one
    assert db.query(User).filter_by(google_sub="g-123").one().email == "a@example.com"


def test_google_sub_must_be_unique(db):
    db.add(User(google_sub="g-dupe", email="a@example.com"))
    db.commit()
    db.add(User(google_sub="g-dupe", email="b@example.com"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_tenant_slug_must_be_unique(db):
    db.add(Tenant(name="Northlight", slug="northlight"))
    db.commit()
    db.add(Tenant(name="Northlight Clone", slug="northlight"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_membership_links_user_and_tenant_with_a_role(db):
    user = User(google_sub="g-1", email="a@example.com")
    tenant = Tenant(name="Acme Agency", slug="acme-agency")
    db.add_all([user, tenant])
    db.commit()

    membership = Membership(user_id=user.id, tenant_id=tenant.id, role="owner")
    db.add(membership)
    db.commit()
    db.refresh(user)
    db.refresh(tenant)

    assert user.memberships[0].tenant_id == tenant.id
    assert tenant.memberships[0].user_id == user.id
    assert membership.role == "owner"


def test_membership_role_defaults_to_owner(db):
    user = User(google_sub="g-1", email="a@example.com")
    tenant = Tenant(name="Acme", slug="acme")
    db.add_all([user, tenant])
    db.commit()
    m = Membership(user_id=user.id, tenant_id=tenant.id)
    db.add(m)
    db.commit()
    db.refresh(m)
    assert m.role == "owner"


def test_deleting_a_tenant_cascades_to_its_memberships_and_sessions(db):
    user = User(google_sub="g-1", email="a@example.com")
    tenant = Tenant(name="Acme", slug="acme")
    db.add_all([user, tenant])
    db.commit()
    db.add(Membership(user_id=user.id, tenant_id=tenant.id))
    db.add(AuthSession(
        token_hash="h" * 64, user_id=user.id, tenant_id=tenant.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    ))
    db.commit()

    db.delete(tenant)
    db.commit()

    assert db.query(Membership).count() == 0
    assert db.query(AuthSession).count() == 0
    # The user itself is untouched -- only tenant-scoped rows cascade.
    assert db.query(User).count() == 1


def test_user_is_platform_admin_defaults_to_false(db):
    user = User(google_sub="g-1", email="a@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    assert user.is_platform_admin is False


def test_tenant_plan_defaults_to_solo(db):
    tenant = Tenant(name="Acme", slug="acme")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    assert tenant.plan == "solo"


def test_auth_session_token_hash_must_be_unique(db):
    user = User(google_sub="g-1", email="a@example.com")
    tenant = Tenant(name="Acme", slug="acme")
    db.add_all([user, tenant])
    db.commit()
    expires = datetime.now(timezone.utc) + timedelta(days=1)
    db.add(AuthSession(token_hash="dupe" * 16, user_id=user.id, tenant_id=tenant.id, expires_at=expires))
    db.commit()
    db.add(AuthSession(token_hash="dupe" * 16, user_id=user.id, tenant_id=tenant.id, expires_at=expires))
    with pytest.raises(IntegrityError):
        db.commit()


def test_two_users_never_share_a_tenant_by_default():
    """Sanity check for the auto-create-tenant-on-first-login design (built
    in Slice 2): nothing at the model layer forces or implies sharing --
    each User<->Tenant relationship is independent unless a Membership row
    explicitly links them."""
    engine = configure_sqlite_engine(create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool))
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        user_a, tenant_a = User(google_sub="a", email="a@x.com"), Tenant(name="A", slug="a")
        user_b, tenant_b = User(google_sub="b", email="b@x.com"), Tenant(name="B", slug="b")
        db.add_all([user_a, tenant_a, user_b, tenant_b])
        db.commit()
        db.add(Membership(user_id=user_a.id, tenant_id=tenant_a.id))
        db.add(Membership(user_id=user_b.id, tenant_id=tenant_b.id))
        db.commit()
        assert tenant_a.id != tenant_b.id
    finally:
        db.close()
