"""
Shared report-artifact persistence: where a generated report lives, and how
it's written and read back. Factored out of main.py so non-HTTP callers
(the scheduler, in particular) can persist and reload a report without
importing the FastAPI app module — main.py will call into the scheduler, so
the scheduler importing main.py back would be circular.

Backed by the `generated_reports` table (see app/store_models.py) rather
than local disk — local files don't survive a redeploy/restart on most
hosting, and don't exist at all across more than one instance. Every
function here opens and commits its own short-lived DB session internally
(see the module-level rationale in scheduler.py, which documents this in
more depth: two real call paths, main.py's background-threaded run_job()
and scheduler's own autonomous loop, have no FastAPI request to draw a
`Depends(get_db)` session from at all).

Track E1 — every row is namespaced by tenant_id (now the first half of a
composite primary key, previously a directory segment). This makes
cross-tenant isolation structural (a read for another tenant's report_id
genuinely finds nothing, a 404 by construction) rather than a comparison
someone has to remember to add at every call site. tenant_id is a REQUIRED
parameter everywhere in this module on purpose — no silent default bucket a
forgotten argument could fall into (matches this app's existing
loud-not-silent convention, e.g. data_context.py's encryption-key warning).
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import db as db_mod
from .report_object import ReportObject
from .store_models import GeneratedReport


def persist_report(tenant_id: str, report_id: str, result: dict, branding: dict) -> None:
    created_at = datetime.now(timezone.utc)
    meta = {
        "report_id": report_id,
        "tenant_id": tenant_id,
        "created_at": created_at.isoformat(),
        "agency_name": branding.get("agency_name"),
        "client_name": branding.get("client_name"),
        "primary_color": branding.get("primary_color"),
        "accent_color": branding.get("accent_color"),
        "report": result["report"],
        "ai_generated": result["report"].get("_ai_generated", False),
        "ai_provider": result["report"].get("_ai_provider"),
        "ai_error": result["report"].get("_ai_error"),
        "ai_limit_reached": result["report"].get("_ai_limit_reached", False),
    }
    qa_input = {
        "metrics": result["metrics"],
        "source_fingerprints": result.get("source_fingerprints", {}),
    }
    # F0: the canonical report object -- extends (doesn't replace) meta/
    # metrics. Intended convergence: report_object becomes the single
    # source, meta/metrics become views derived from it.
    report_object = result["report_object"].to_dict() if "report_object" in result else None

    with db_mod.SessionLocal() as session:
        row = GeneratedReport(
            tenant_id=tenant_id, report_id=report_id, client_name=branding.get("client_name"),
            created_at=created_at, meta=meta, metrics=qa_input, report_object=report_object,
            pdf_bytes=result["pdf_bytes"], html=result["html"],
        )
        session.merge(row)
        session.commit()


def load_meta(tenant_id: str, report_id: str) -> dict | None:
    with db_mod.SessionLocal() as session:
        row = session.query(GeneratedReport).filter_by(tenant_id=tenant_id, report_id=report_id).one_or_none()
        return row.meta if row else None


def load_report_object(tenant_id: str, report_id: str) -> ReportObject | None:
    with db_mod.SessionLocal() as session:
        row = session.query(GeneratedReport).filter_by(tenant_id=tenant_id, report_id=report_id).one_or_none()
        if row is None or row.report_object is None:
            return None
        return ReportObject.from_dict(row.report_object)


def load_pdf_bytes(tenant_id: str, report_id: str) -> bytes | None:
    with db_mod.SessionLocal() as session:
        row = session.query(GeneratedReport).filter_by(tenant_id=tenant_id, report_id=report_id).one_or_none()
        return row.pdf_bytes if row else None


def load_html(tenant_id: str, report_id: str) -> str | None:
    with db_mod.SessionLocal() as session:
        row = session.query(GeneratedReport).filter_by(tenant_id=tenant_id, report_id=report_id).one_or_none()
        return row.html if row else None


def report_exists(tenant_id: str, report_id: str) -> bool:
    with db_mod.SessionLocal() as session:
        return session.query(GeneratedReport.report_id).filter_by(
            tenant_id=tenant_id, report_id=report_id,
        ).first() is not None


def list_reports_for_tenant(tenant_id: str) -> list[dict]:
    """Every report id, created_at, and headline info for one tenant --
    the tenant-scoped replacement for what used to be a global glob over
    every report on disk (main.py's old GET /api/reports)."""
    with db_mod.SessionLocal() as session:
        rows = (
            session.query(GeneratedReport)
            .filter_by(tenant_id=tenant_id)
            .order_by(GeneratedReport.created_at.desc())
            .all()
        )
        return [_summarize(row) for row in rows]


def list_reports_for_client(tenant_id: str, client_name: str) -> list[dict]:
    """W2 — "every generated report retained and listable per client, by
    period," scoped to one tenant's one client (client_name is a grouping
    WITHIN a tenant, not a tenant boundary itself -- see this module's own
    docstring / the E1 CHANGELOG entry for the tenant-vs-client
    distinction)."""
    with db_mod.SessionLocal() as session:
        rows = (
            session.query(GeneratedReport)
            .filter_by(tenant_id=tenant_id, client_name=client_name)
            .order_by(GeneratedReport.created_at.desc())
            .all()
        )
        return [_summarize(row) for row in rows]


def _summarize(row: GeneratedReport) -> dict:
    meta = row.meta
    return {
        "report_id": meta["report_id"],
        "created_at": meta["created_at"],
        "agency_name": meta.get("agency_name"),
        "client_name": meta.get("client_name"),
        "report_title": meta.get("report", {}).get("report_title"),
        "period_label": meta.get("report", {}).get("period_label"),
        "ai_generated": meta.get("ai_generated", False),
        "ai_provider": meta.get("ai_provider"),
    }
