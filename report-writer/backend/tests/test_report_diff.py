"""
Tests for W2 — version history + report diff.

Exit criteria proven here:
  - Every generated report retained and listable per client, by period --
    test_list_reports_for_client_scopes_and_sorts.
  - "What changed between July and August" rendered from B1's own diff,
    reused not reinvented -- test_diff_report_objects_reuses_b1_exactly
    (same function scheduler.py's automatic comparison already calls),
    plus the real HTTP round trip in test_diff_endpoint_*.
"""
from datetime import datetime

from app import period_diff, report_store
from app.report_object import Period, ReportObject, SourceInfo
from app.store_models import GeneratedReport
from tests.conftest import seed_tenant


def _obj(report_id: str, period_label: str, revenue: float, client_name: str = "Acme") -> ReportObject:
    return ReportObject(
        report_id=report_id, period=Period(label=period_label),
        sources={"analytics": SourceInfo(row_count=10, sha256="a" * 64)},
        metrics={"analytics": {"totals": {"revenue_usd": revenue, "sessions": 1000}}},
        series={}, charts=[], narrative={"report_title": "R", "period_label": period_label},
        qa={"badge": "PASS"}, branding={"agency_name": "Northlight", "client_name": client_name},
        section_order=["analytics"],
    )


# ---------------------------------------------------------------------------
# period_diff.diff_report_objects: the one reusable core
# ---------------------------------------------------------------------------

def test_diff_report_objects_reuses_b1_exactly():
    current = _obj("r-aug", "2026-08", 6000.0)
    prior = _obj("r-jul", "2026-07", 5000.0)
    result = period_diff.diff_report_objects(current, prior)

    assert result["current_report_id"] == "r-aug"
    assert result["prior_report_id"] == "r-jul"
    assert result["current_period_label"] == "2026-08"
    assert result["prior_period_label"] == "2026-07"
    assert result["analytics"]["revenue_usd"]["abs_delta"] == 1000.0
    assert result["analytics"]["revenue_usd"]["pct_delta"] == 20.0


def test_diff_report_objects_skips_a_source_missing_from_either_side():
    current = _obj("r2", "2026-08", 6000.0)
    prior = _obj("r1", "2026-07", 5000.0)
    prior.metrics = {}  # nothing to diff against for ANY source
    result = period_diff.diff_report_objects(current, prior)
    assert "analytics" not in result
    assert "sales" not in result


# ---------------------------------------------------------------------------
# report_store.list_reports_for_client
# ---------------------------------------------------------------------------

def _persist(db_session, tenant_id, report_id, client_name, period_label, created_at):
    """Real persist_report() call (not a hand-written file/row) so this
    exercises the actual persistence path -- created_at is then overridden
    directly on the row afterward, since these tests need EXPLICIT,
    deterministic timestamps (a month apart) to prove sort order, not
    whatever real wall-clock instant persist_report() would stamp."""
    branding = {"agency_name": "Northlight", "client_name": client_name}
    result = {
        "pdf_bytes": b"%PDF-fake", "html": "<html></html>",
        "report": {"period_label": period_label, "report_title": "R"}, "metrics": {},
    }
    report_store.persist_report(tenant_id, report_id, result, branding)
    row = db_session.query(GeneratedReport).filter_by(tenant_id=tenant_id, report_id=report_id).one()
    parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    row.created_at = parsed
    row.meta = {**row.meta, "created_at": parsed.isoformat()}
    db_session.commit()


def test_list_reports_for_client_scopes_and_sorts(db_session):
    _persist(db_session, "t1", "acme-jul", "Acme", "2026-07", "2026-07-01T00:00:00Z")
    _persist(db_session, "t1", "acme-aug", "Acme", "2026-08", "2026-08-01T00:00:00Z")
    _persist(db_session, "t1", "other-jul", "Other Co", "2026-07", "2026-07-01T00:00:00Z")

    reports = report_store.list_reports_for_client("t1", "Acme")
    assert [r["report_id"] for r in reports] == ["acme-aug", "acme-jul"]  # newest first
    assert all(r not in {"other-jul"} for r in [x["report_id"] for x in reports])


def test_list_reports_for_client_with_no_reports_returns_empty(db_session):
    assert report_store.list_reports_for_client("t1", "Nobody") == []


# ---------------------------------------------------------------------------
# Real HTTP round trip
# ---------------------------------------------------------------------------

def test_client_reports_endpoint(client, db_session):
    tenant_id = seed_tenant(db_session, client, google_sub="g-diff", email="a@northlight.com")
    _persist(db_session, tenant_id, "acme-jul", "Acme", "2026-07", "2026-07-01T00:00:00Z")
    _persist(db_session, tenant_id, "acme-aug", "Acme", "2026-08", "2026-08-01T00:00:00Z")

    resp = client.get("/api/clients/Acme/reports")
    assert resp.status_code == 200
    body = resp.json()
    assert body["client_name"] == "Acme"
    assert [r["report_id"] for r in body["reports"]] == ["acme-aug", "acme-jul"]


def test_diff_endpoint_returns_a_real_b1_diff(client, db_session):
    tenant_id = seed_tenant(db_session, client, google_sub="g-diff2", email="b@northlight.com")
    aug = _obj("acme-aug", "2026-08", 6000.0)
    jul = _obj("acme-jul", "2026-07", 5000.0)
    report_store.persist_report(tenant_id, "acme-aug", {"pdf_bytes": b"", "html": "<html></html>",
                                              "report": {}, "metrics": aug.metrics,
                                              "report_object": aug}, aug.branding)
    report_store.persist_report(tenant_id, "acme-jul", {"pdf_bytes": b"", "html": "<html></html>",
                                              "report": {}, "metrics": jul.metrics,
                                              "report_object": jul}, jul.branding)

    resp = client.get("/api/reports/diff", params={"report_id_a": "acme-aug", "report_id_b": "acme-jul"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["analytics"]["revenue_usd"]["abs_delta"] == 1000.0
    assert body["current_report_id"] == "acme-aug"
    assert body["prior_report_id"] == "acme-jul"


def test_diff_endpoint_404s_on_an_unknown_report_id(client, db_session):
    seed_tenant(db_session, client, google_sub="g-diff3", email="c@northlight.com")
    resp = client.get("/api/reports/diff", params={"report_id_a": "nope-a", "report_id_b": "nope-b"})
    assert resp.status_code == 404
