"""
Track E1 — database engine/session setup.

SQLite now, Postgres-ready: every SQLite-specific line below is gated on
`engine.dialect.name == "sqlite"` so switching settings.database_url to a
Postgres DSN later doesn't carry dead pragmas -- the rest of this module
(engine/SessionLocal/Base/get_db) is dialect-agnostic SQLAlchemy.
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .settings import settings


def configure_sqlite_engine(engine: Engine) -> Engine:
    """Applies this app's SQLite pragmas to any engine -- shared by the
    module-level `engine` below and by tests that build their own throwaway
    engine (test_db_models.py), so both get the exact same behavior instead
    of tests silently running against a laxer configuration than production.
    A no-op for any other dialect (safe to call unconditionally)."""
    if engine.dialect.name != "sqlite":
        return engine

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        # OFF by default per SQLite connection -- without this, every
        # `ON DELETE CASCADE` FK constraint in models.py is silently inert:
        # SQLite accepts the constraint at CREATE TABLE time but never
        # enforces or acts on it unless a connection explicitly opts in.
        cursor.execute("PRAGMA foreign_keys=ON")
        # WAL: readers don't block on a writer (report generation can be
        # writing a session/tenant row while another request reads).
        cursor.execute("PRAGMA journal_mode=WAL")
        # Retry on a transient lock instead of raising immediately -- a
        # single-file SQLite db under light concurrent access is fine as
        # long as a momentary lock doesn't surface as a 500.
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


_connect_args = {}
if settings.database_url.startswith("sqlite"):
    # Report generation runs on a background thread (main.py's run_job) that
    # may need DB access, and the SSE endpoint polls from the request thread
    # -- SQLite's default same-thread-only check would reject that.
    _connect_args = {"check_same_thread": False}

engine = configure_sqlite_engine(create_engine(settings.database_url, connect_args=_connect_args))

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one DB session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
