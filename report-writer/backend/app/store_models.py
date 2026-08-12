"""
DB-backed replacements for what used to be five flat-file/directory stores
next to the code (`generated/`, `data_contexts/`, `schedules/`, `alerts/`,
`delivery_logs/`) -- kept in a separate module from models.py on purpose:
models.py's own docstring scopes itself to just the identity/access-control
layer (User/Tenant/Membership/AuthSession/ApiToken), and that boundary is
worth preserving structurally, not just in prose.

Every table here is a near-verbatim JSON/bytes column matching what used to
be on disk (no field-by-field normalization) -- same "config as data"
convention already established by plans.py/template_specs.py, just backed
by a column instead of a file. Composite (tenant_id, X) primary keys reuse
the exact same namespacing the old `{DIR}/{tenant_id}/{X}...` directory
layout already enforced -- a lookup for another tenant's key still
structurally finds nothing, same guarantee as before, just expressed as a
WHERE clause instead of a path that doesn't exist.

Deliberately NO ForeignKey to tenants.id anywhere below: the file-based code
these tables replace never cross-checked tenant_id against a real Tenant
row either (it was just a namespacing string). Adding a real FK now would
be a behavior *tightening*, not a pure migration -- left as a deliberate,
separate hardening step for later, not bundled into this change.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import _uuid


class GeneratedReport(Base):
    """Replaces report_store.py's GENERATED_DIR/{tenant_id}/{report_id}/*."""
    __tablename__ = "generated_reports"

    tenant_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: Promoted to a real column (not just a field inside `meta`) because
    #: list_reports_for_client() filters on it -- a real WHERE clause,
    #: not the Python-side filter-over-every-tenant's-reports a directory
    #: glob forced before.
    client_name: Mapped[str | None] = mapped_column(String(255), index=True, default=None)
    #: Same promotion reasoning, for list_reports_for_tenant()'s ORDER BY.
    #: Also duplicated verbatim inside `meta` below -- intentional, not an
    #: inconsistency risk: `meta` must stay byte-identical to what
    #: load_meta() used to return from meta.json.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    meta: Mapped[dict] = mapped_column(JSON)
    metrics: Mapped[dict] = mapped_column(JSON)
    report_object: Mapped[dict | None] = mapped_column(JSON, default=None)
    pdf_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    html: Mapped[str] = mapped_column(Text)


class ScheduleRecord(Base):
    """Replaces scheduler.py's SCHEDULES_DIR/{tenant_id}/{client_id}.json."""
    __tablename__ = "schedules"

    tenant_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    data_source_ref: Mapped[str] = mapped_column(String(255))
    cadence: Mapped[str] = mapped_column(String(16))
    branding: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Kept as a STRING, matching scheduler.Schedule.created_at's existing
    #: `str` type (it's round-tripped as a string by to_dict()/from_dict()
    #: today) -- not promoted to a real DateTime, to avoid a type-fidelity
    #: change that's out of scope for this migration.
    created_at: Mapped[str] = mapped_column(String(64), default="")
    #: The idempotency ledger: {as_of_date_str: report_id}.
    runs: Mapped[dict] = mapped_column(JSON, default=dict)
    client_recipients: Mapped[list] = mapped_column(JSON, default=list)
    consultant_recipients: Mapped[list] = mapped_column(JSON, default=list)
    delivery_channel: Mapped[str] = mapped_column(String(32), default="email")


class DataContextRecord(Base):
    """Replaces data_context.py's DATA_CONTEXTS_DIR/{tenant_id}/{client_id}.json."""
    __tablename__ = "data_contexts"

    tenant_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(64), default="")
    #: {"kind": ..., "config": _encrypt_config(...)'s output} -- completely
    #: unchanged shape. The Fernet encryption never touches storage at all;
    #: only what wraps the resulting dict changed (column instead of file).
    connector: Mapped[dict] = mapped_column(JSON)
    sources: Mapped[dict] = mapped_column(JSON, default=dict)


class AlertConfigRecord(Base):
    """Replaces alerts.py's ALERTS_DIR/{tenant_id}/{client_id}.json."""
    __tablename__ = "alert_configs"

    tenant_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    rules: Mapped[list] = mapped_column(JSON, default=list)


class AlertFiredLedger(Base):
    """Replaces alerts.py's ALERTS_DIR/{tenant_id}/{client_id}.fired.json --
    a separate table for the same reason it was a separate file: the rule
    config and the dedup-fired ledger have independent lifecycles."""
    __tablename__ = "alert_fired_ledger"

    tenant_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    #: {as_of_date_str: [rule_id, ...]}
    fired: Mapped[dict] = mapped_column(JSON, default=dict)


class DeliveryLogEntry(Base):
    """Replaces delivery.py's DELIVERY_LOGS_DIR/{tenant_id}/{report_id}.jsonl
    -- genuinely append-only (many attempts can share a (tenant_id,
    report_id)), so unlike the other five tables this one needs a real
    synthetic primary key rather than reusing the natural key."""
    __tablename__ = "delivery_log_entries"
    __table_args__ = (Index("ix_delivery_log_tenant_report", "tenant_id", "report_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(32))
    report_id: Mapped[str] = mapped_column(String(64))
    #: Replaces JSONL append order, which was doing this implicitly.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    #: Verbatim DeliveryAttempt.to_dict().
    attempt: Mapped[dict] = mapped_column(JSON)
