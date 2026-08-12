"""
Tests for P — industry packs. GOAL: "Dental practice SEO monthly" sells
better than "a report." Content work, not engineering.

Exit criteria proven here:
  - A pack = template(s) + industry KPI set + prompt guidance, shipped as
    data only -- app/template_specs/local_service_business.v1.json is the
    ONLY new file this pack required; zero .py files changed for it.
  - Adding a pack requires no code change --
    test_local_service_business_pack_appears_with_zero_renderer_changes
    greps report_builder.py/main.py for the id, same proof pattern as T4.
  - Pack-specific KPIs still compute deterministically and pass QA -- no
    hand-waved industry math -- test_local_service_business_pack_renders_and_passes_qa.
"""
from pathlib import Path

from app import agent, report_builder, template_specs

BRANDING = {"agency_name": "Test Agency", "client_name": "Aurora Home Goods",
            "primary_color": "#2a78d6", "accent_color": "#eda100"}

_APP_DIR = Path(__file__).parent.parent / "app"

#: Track E1 -- the checked-in "aurora-home-goods" SQLite data context fixture
#: lives under this fixed tenant_id (data_contexts/demo-tenant/aurora-home-goods.json).
TENANT = "demo-tenant"


def test_local_service_business_pack_loads():
    spec = template_specs.load_template("local_service_business")
    assert spec.label == "Local Service Business Monthly"
    assert spec.prompt_guidance  # non-empty industry guidance
    assert [s.key for s in spec.sections] == ["seo", "sales"]  # analytics skipped on purpose


def test_local_service_business_pack_appears_in_the_picker():
    templates = template_specs.list_templates()
    pack = next(t for t in templates if t["id"] == "local_service_business")
    assert pack["sections"] == ["seo", "sales"]


def test_local_service_business_pack_appears_with_zero_renderer_changes():
    """Same proof as T4's second template: no .py file's source text needed
    to change to add this pack."""
    for path in (_APP_DIR / "report_builder.py", _APP_DIR / "main.py"):
        assert "local_service_business" not in path.read_text(encoding="utf-8")


def test_local_service_business_pack_renders_and_passes_qa(monkeypatch):
    from tests.conftest import seed_aurora_home_goods_data_context

    seed_aurora_home_goods_data_context()
    monkeypatch.setattr("app.agent._ollama_available", lambda: False)
    result = report_builder.build_report_from_data_context(
        TENANT, "aurora-home-goods", BRANDING, report_id="p-pack-test", template_id="local_service_business",
    )
    obj = result["report_object"]
    # Analytics data exists for this client but the pack doesn't ask for it --
    # deterministic section selection, the same mechanism as T2/T4, not a
    # special case for this pack.
    assert obj.section_order == ["seo", "sales"]
    assert "analytics" not in obj.metrics
    assert {c.caption for c in obj.charts} == {
        "Site health", "Top technical issues", "Monthly revenue & win rate", "Revenue by lead source",
    }
    assert obj.qa["badge"] in {"PASS", "PASS-WITH-WARNINGS"}


def test_pack_prompt_guidance_reaches_the_system_prompt():
    spec = template_specs.load_template("local_service_business")
    prompt = agent._system_prompt_for(spec.tone, spec.prompt_guidance)
    assert "local service business" in prompt
    assert "BOOKED JOBS" in prompt


def test_general_purpose_templates_carry_no_industry_guidance():
    for template_id in ("default", "executive_summary"):
        assert template_specs.load_template(template_id).prompt_guidance == ""
