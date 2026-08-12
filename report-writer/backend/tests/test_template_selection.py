"""
Tests for T4 — two maximally different templates ("Full Monthly Report":
deep, multi-section vs. "Executive Summary": one page, synthesis), both
rendering through the exact same T1 engine with no renderer special-casing,
plus the API/UI surface that lets a real user pick one.

Exit criteria proven here:
  - Both templates render from the same engine, no special-casing --
    test_executive_summary_renders_via_the_same_generic_pipeline (the test
    itself calls no executive_summary-specific code path; report_builder.py
    has none).
  - Both pass QA -- test_executive_summary_gets_a_qa_badge.
  - Both honor the data contract (T2) -- test_executive_summary_respects_requires_columns.
  - Both respect branding -- test_executive_summary_respects_branding.
  - Additional templates are config only, not code
    (test_list_templates_reflects_the_json_files_on_disk_with_zero_hardcoding).
"""
import io

from fastapi.testclient import TestClient

from app import main, report_builder, report_store, template_specs

BRANDING = {"agency_name": "Test Agency", "client_name": "Aurora Home Goods",
            "primary_color": "#2a78d6", "accent_color": "#eda100"}

#: Track E1 -- the checked-in "aurora-home-goods" SQLite data context fixture
#: lives under this fixed tenant_id (data_contexts/demo-tenant/aurora-home-goods.json).
TENANT = "demo-tenant"


def test_list_templates_reflects_the_json_files_on_disk_with_zero_hardcoding():
    templates = template_specs.list_templates()
    ids = {t["id"] for t in templates}
    assert "default" in ids
    assert "executive_summary" in ids
    assert "analytics_only" not in ids  # hidden: true -- a fixture, not a product template

    default_t = next(t for t in templates if t["id"] == "default")
    exec_t = next(t for t in templates if t["id"] == "executive_summary")
    assert default_t["label"] == "Full Monthly Report"
    assert exec_t["label"] == "Executive Summary"
    assert exec_t["tone"] == "executive"
    assert set(exec_t["sections"]) == {"analytics", "seo", "sales"}


def test_the_two_templates_are_genuinely_maximally_different():
    deep = template_specs.load_template("default")
    exec_summary = template_specs.load_template("executive_summary")
    deep_chart_count = sum(len(s.charts) for s in deep.sections)
    exec_chart_count = sum(len(s.charts) for s in exec_summary.sections)
    assert deep_chart_count == 11
    assert exec_chart_count == 3  # exactly one chart per section -- "one page, synthesis"
    assert deep.tone != exec_summary.tone


def test_executive_summary_renders_via_the_same_generic_pipeline(monkeypatch):
    from tests.conftest import seed_aurora_home_goods_data_context

    seed_aurora_home_goods_data_context()
    monkeypatch.setattr("app.agent._ollama_available", lambda: False)
    result = report_builder.build_report_from_data_context(
        TENANT, "aurora-home-goods",BRANDING, report_id="t4-exec-summary-test", template_id="executive_summary",
    )
    obj = result["report_object"]
    assert obj.template_id == "executive_summary"
    assert obj.section_order == ["analytics", "seo", "sales"]
    assert {c.caption for c in obj.charts} == {"Revenue by channel", "Site health", "Monthly revenue & win rate"}
    assert len(obj.narrative["sections"]) == 3


def test_executive_summary_gets_a_qa_badge(monkeypatch):
    from tests.conftest import seed_aurora_home_goods_data_context

    seed_aurora_home_goods_data_context()
    monkeypatch.setattr("app.agent._ollama_available", lambda: False)
    result = report_builder.build_report_from_data_context(
        TENANT, "aurora-home-goods",BRANDING, report_id="t4-exec-qa-test", template_id="executive_summary",
    )
    assert result["report_object"].qa["badge"] in {"PASS", "PASS-WITH-WARNINGS"}


def test_executive_summary_respects_branding(monkeypatch):
    from tests.conftest import seed_aurora_home_goods_data_context

    seed_aurora_home_goods_data_context()
    monkeypatch.setattr("app.agent._ollama_available", lambda: False)
    custom = {**BRANDING, "agency_name": "Northlight", "client_name": "Zenith Corp"}
    result = report_builder.build_report_from_data_context(
        TENANT, "aurora-home-goods",custom, report_id="t4-exec-branding-test", template_id="executive_summary",
    )
    assert result["report_object"].branding["agency_name"] == "Northlight"
    assert result["report_object"].branding["client_name"] == "Zenith Corp"


def test_executive_summary_respects_requires_columns(monkeypatch):
    """T2's data contract applies identically -- no revenue column means the
    revenue chart is omitted here too, same mechanism, zero special-casing."""
    monkeypatch.setattr("app.agent._ollama_available", lambda: False)
    text = (
        "date,channel_group,device_category,sessions,new_users,conversions\n"
        "2026-01-01,Organic Search,desktop,100,40,5\n"
    )
    buf = io.BytesIO(text.encode("utf-8"))
    result = report_builder.build_report(
        {"analytics": ("analytics.csv", buf)}, BRANDING, report_id="t4-exec-no-revenue-test",
        template_id="executive_summary",
    )
    obj = result["report_object"]
    assert obj.charts == []  # the only analytics chart in this template requires revenue_usd
    assert obj.qa["data_availability"]["omitted_charts"][0]["caption"] == "Revenue by channel"


# ---------------------------------------------------------------------------
# Real HTTP surface: GET /api/templates, and the generate endpoint actually
# accepting/using template_id, not just report_builder.py in isolation.
# ---------------------------------------------------------------------------

def test_api_templates_endpoint_lists_the_real_non_hidden_templates():
    client = TestClient(main.app)
    resp = client.get("/api/templates")
    assert resp.status_code == 200
    ids = {t["id"] for t in resp.json()["templates"]}
    assert {"default", "executive_summary"} <= ids
    assert "analytics_only" not in ids


def test_generate_report_endpoint_honors_the_submitted_template_id(monkeypatch, client, db_session):
    from tests.conftest import seed_tenant
    tenant_id = seed_tenant(db_session, client, google_sub="g-t4", email="a@northlight.com")
    monkeypatch.setattr("app.agent._ollama_available", lambda: False)

    csv_text = (
        "date,channel_group,device_category,sessions,new_users,conversions,revenue_usd\n"
        "2026-01-01,Organic Search,desktop,100,40,5,500\n"
        "2026-01-08,Paid Search,mobile,120,45,6,600\n"
    )
    resp = client.post(
        "/api/generate-report",
        data={"agency_name": "A", "client_name": "B", "template_id": "executive_summary"},
        files={"analytics_file": ("analytics.csv", csv_text, "text/csv")},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    import time
    for _ in range(100):
        job_resp = client.get(f"/api/report/{job_id}")
        if job_resp.status_code == 200:
            break
        time.sleep(0.2)
    else:
        raise AssertionError("job never completed in time")

    obj = report_store.load_report_object(tenant_id, job_id)
    assert obj.template_id == "executive_summary"
    assert {c.caption for c in obj.charts} == {"Revenue by channel"}  # only analytics was uploaded
