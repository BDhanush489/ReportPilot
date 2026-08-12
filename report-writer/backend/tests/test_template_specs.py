"""
Tests for T1 — templates as declarative specs (app/template_specs.py +
app/template_specs/*.json), and the report_builder.py wiring that makes
section order/labels/charts/tone entirely template-driven instead of the old
hardcoded SECTION_ORDER / _CHART_SPECS / _analytics_section-style functions —
plus T2, the data-availability contract built on top of it.

Exit criteria this file proves (T1):
  - Template = declarative spec in a file, no Python branch, no bespoke
    prompt per template (test_default_template_matches_the_old_hardcoded_shape,
    test_report_builder_source_never_names_the_second_template).
  - Rendering is template-driven: adding a template requires ZERO renderer
    changes (test_analytics_only_template_renders_via_the_generic_pipeline).
  - QA badge applies automatically, no per-template path
    (test_a_non_default_template_still_gets_a_qa_badge).
  - tone is a field; it never changes figures
    (test_tone_never_changes_the_shared_sections_computed_figures,
    test_fallback_tone_changes_only_how_many_highlights_lead_not_their_content).

Exit criteria this file proves (T2):
  - Each chart declares required inputs (requires_columns) --
    test_select_renderable_charts_omits_a_chart_missing_its_required_column.
  - A template requesting an unavailable KPI degrades explicitly (omits the
    chart with a stated reason), never a blank/0 chart --
    test_a_report_missing_revenue_omits_revenue_charts_but_keeps_the_rest.
  - The omission is visible in the output (Data Quality) and the QA JSON --
    same test, plus test_omitted_chart_reason_is_a_real_sentence_not_a_code.

Uses the real "aurora-home-goods" SQLite data context (same fixture
test_pbip_export.py uses) so this exercises the exact build_report_from_data_context()
path a real run takes, not a hand-built stub.
"""
import io
from pathlib import Path

import pytest

from app import agent, report_builder, template_specs

BRANDING = {"agency_name": "Test Agency", "client_name": "Aurora Home Goods",
            "primary_color": "#2a78d6", "accent_color": "#eda100"}

_APP_DIR = Path(__file__).parent.parent / "app"

#: Track E1 -- the checked-in "aurora-home-goods" SQLite data context fixture
#: lives under this fixed tenant_id (data_contexts/demo-tenant/aurora-home-goods.json).
TENANT = "demo-tenant"


# ---------------------------------------------------------------------------
# template_specs.py: loading, caching, unknown ids
# ---------------------------------------------------------------------------

def test_load_template_unknown_id_raises_a_clear_error():
    with pytest.raises(ValueError, match="unknown template_id"):
        template_specs.load_template("does-not-exist")


def test_load_template_is_cached_by_id():
    a = template_specs.load_template("default")
    b = template_specs.load_template("default")
    assert a is b


def test_default_template_matches_the_old_hardcoded_shape():
    """Golden-list check: the JSON spec must carry exactly the section order,
    labels, and chart captions the pre-T1 hardcoded SECTION_ORDER/_CHART_SPECS
    encoded, in the same order -- this IS the regression guarantee for the
    refactor (also independently proven by the untouched pbip golden fixture
    passing byte-for-byte, since it's built from this same "default" spec)."""
    spec = template_specs.load_template("default")
    assert [s.key for s in spec.sections] == ["analytics", "seo", "sales"]
    assert [s.label for s in spec.sections] == ["Web Analytics", "SEO & Site Health", "Sales Performance"]
    assert spec.tone == "manager"

    captions_by_section = {s.key: [c.caption for c in s.charts] for s in spec.sections}
    assert captions_by_section["analytics"] == [
        "Weekly sessions by channel", "Weekly revenue", "Revenue by channel",
        "Conversion rate by channel", "Sessions by device",
    ]
    assert captions_by_section["seo"] == ["Site health", "Top technical issues"]
    assert captions_by_section["sales"] == [
        "Monthly revenue & win rate", "Revenue by sales rep",
        "Revenue by lead source", "Revenue by product",
    ]


def test_report_builder_still_exposes_backward_compatible_chart_spec_tables():
    """Several existing tests (test_chart_intelligence.py, test_report_object.py,
    test_chart_annotation.py) import _CHART_SPECS / _CHART_METRIC_PATHS
    directly and call _build_chart_refs with the old 4-arg signature -- both
    must keep working, now DERIVED from the JSON file rather than hand-written."""
    assert report_builder._CHART_SPECS[("analytics", "Weekly revenue")].chart_type == "line"
    assert report_builder._CHART_METRIC_PATHS[("seo", "Site health")] == (
        "bar", ["metrics.seo.severity_counts"],
    )


# ---------------------------------------------------------------------------
# render_section_charts: the generic replacement for the old per-section
# hardcoded chart lists
# ---------------------------------------------------------------------------

def test_render_section_charts_calls_the_right_builder_with_the_right_args(monkeypatch):
    calls = []
    monkeypatch.setitem(template_specs.CHART_BUILDERS, "channel_revenue_bar_chart",
                         lambda by_channel: calls.append(by_channel) or "img-bytes")
    spec = template_specs.load_template("analytics_only")
    section = spec.section("analytics")
    section_metrics = {
        "by_channel": [{"channel": "Organic", "revenue_usd": 1.0}],
        "by_device": [{"device_category": "desktop", "sessions": 10}],
    }
    out = template_specs.render_section_charts(section.charts, section_metrics)
    revenue_chart = next(c for c in out if c["caption"] == "Revenue by channel")
    assert revenue_chart["img"] == "img-bytes"
    assert calls == [section_metrics["by_channel"]]


# ---------------------------------------------------------------------------
# End-to-end via the real pipeline: a genuinely smaller second template,
# proving extensibility without touching report_builder.py
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def default_report_object():
    from tests.conftest import seed_aurora_home_goods_data_context

    seed_aurora_home_goods_data_context()
    result = report_builder.build_report_from_data_context(
        TENANT, "aurora-home-goods", BRANDING, report_id="t1-default-test", template_id="default")
    return result["report_object"]


@pytest.fixture(scope="module")
def analytics_only_report_object():
    from tests.conftest import seed_aurora_home_goods_data_context

    seed_aurora_home_goods_data_context()
    result = report_builder.build_report_from_data_context(
        TENANT, "aurora-home-goods", BRANDING, report_id="t1-analytics-only-test", template_id="analytics_only")
    return result["report_object"]


def test_analytics_only_template_renders_via_the_generic_pipeline(analytics_only_report_object):
    obj = analytics_only_report_object
    assert obj.section_order == ["analytics"]
    assert {c.caption for c in obj.charts} == {"Revenue by channel", "Sessions by device"}
    # The other two sections were never uploaded to metrics/series/narrative,
    # even though aurora-home-goods genuinely has seo + sales data available --
    # the template selected them out, not just the renderer's chart loop.
    assert set(obj.metrics) == {"analytics", "period_label"}
    assert len(obj.narrative["sections"]) == 1
    assert obj.narrative["sections"][0]["heading"] == "Web Analytics"


def test_report_builder_source_never_names_the_second_template():
    """The literal proof of "adding a template requires ZERO renderer
    changes": report_builder.py's own source text has no idea
    "analytics_only" exists. Only template_specs.py's directory listing
    (data, not code) knows about it."""
    source = (_APP_DIR / "report_builder.py").read_text(encoding="utf-8")
    assert "analytics_only" not in source


def test_a_non_default_template_still_gets_a_qa_badge(analytics_only_report_object):
    """QA badge applies to every template automatically -- qa.run_qa is
    called from the one shared _finish_report tail regardless of which
    template loaded, no per-template verification path."""
    assert analytics_only_report_object.qa.get("badge") in {"PASS", "PASS-WITH-WARNINGS", "FAIL"}


def test_unknown_template_id_fails_loudly_not_a_silent_default(default_report_object):
    with pytest.raises(ValueError, match="unknown template_id"):
        report_builder.build_report_from_data_context(
            TENANT, "aurora-home-goods", BRANDING, report_id="t1-bad-template", template_id="does-not-exist")


# ---------------------------------------------------------------------------
# Tone: a template-spec field that changes register, never figures
# ---------------------------------------------------------------------------

def test_tone_never_changes_the_shared_sections_computed_figures(default_report_object,
                                                                    analytics_only_report_object):
    """"default" (tone=manager) and "analytics_only" (tone=executive) both
    include an "analytics" section computed from the SAME underlying data.
    Different tone (and different section selection) must never move a
    single number in the section they share."""
    assert default_report_object.metrics["analytics"] == analytics_only_report_object.metrics["analytics"]
    assert default_report_object.series["analytics"] == analytics_only_report_object.series["analytics"]


def test_fallback_tone_changes_only_how_many_highlights_lead_not_their_content():
    m = {
        "period_label": "2026-01-01 to 2026-06-30",
        "analytics": {
            "date_range": {"start": "2026-01-01", "end": "2026-06-30", "days": 180},
            "totals": {"sessions": 1000, "revenue_usd": 5000.0, "conversion_rate": 3.0},
            "sessions_change_pct": 5.0, "revenue_change_pct": 10.0,
            "by_channel": [{"channel": "Organic Search", "revenue_usd": 2000.0, "share_of_sessions_pct": 40.0}],
            "top_declining_channel": None,
        },
    }
    reports = {tone: agent._fallback_report(m, {}, ["Web Analytics"], tone=tone)
               for tone in ("executive", "manager", "specialist")}

    # The full highlights list (computed from m, never from tone) is identical.
    assert reports["executive"]["highlights"] == reports["manager"]["highlights"] == reports["specialist"]["highlights"]
    # Section narrative content -- the actual sentences with numbers in them --
    # is completely untouched by tone in the fallback path.
    assert reports["executive"]["sections"] == reports["manager"]["sections"] == reports["specialist"]["sections"]

    # Only the executive-summary truncation differs, by design.
    highlights = reports["manager"]["highlights"]
    assert reports["executive"]["executive_summary"] == " ".join(highlights[:1])
    assert reports["manager"]["executive_summary"] == " ".join(highlights[:2])
    assert reports["specialist"]["executive_summary"] == " ".join(highlights[:3])


def test_fallback_default_tone_is_byte_identical_to_pre_t1_behavior():
    """Regression: manager is the default and must reproduce exactly what
    the old hardcoded highlights[:2] produced, so every existing test/golden
    fixture relying on _fallback_report's default output is unaffected."""
    m = {"period_label": "p", "analytics": {
        "date_range": {"start": "2026-01-01", "end": "2026-01-02", "days": 2},
        "totals": {"sessions": 10, "revenue_usd": 100.0, "conversion_rate": 1.0},
        "sessions_change_pct": None, "revenue_change_pct": None,
        "by_channel": [{"channel": "Organic Search", "revenue_usd": 100.0, "share_of_sessions_pct": 100.0}],
        "top_declining_channel": None,
    }}
    explicit = agent._fallback_report(m, {}, ["Web Analytics"], tone="manager")
    default = agent._fallback_report(m, {}, ["Web Analytics"])
    assert explicit == default


# ---------------------------------------------------------------------------
# T2 — data-availability contract: a chart never renders on a defaulted
# (i.e. absent-from-the-upload) business-critical column.
# ---------------------------------------------------------------------------

def test_select_renderable_charts_omits_a_chart_missing_its_required_column():
    spec = template_specs.load_template("default")
    section = spec.section("analytics")
    renderable, omitted = template_specs.select_renderable_charts(section, {"revenue_usd"})

    renderable_captions = {c.caption for c in renderable}
    omitted_captions = {o["caption"] for o in omitted}
    assert omitted_captions == {"Weekly revenue", "Revenue by channel"}
    assert renderable_captions == {"Weekly sessions by channel", "Conversion rate by channel", "Sessions by device"}
    for note in omitted:
        assert note["missing_columns"] == ["revenue_usd"]
        assert "Weekly revenue" in note["reason"] or "Revenue by channel" in note["reason"]


def test_select_renderable_charts_is_a_noop_when_nothing_is_missing():
    spec = template_specs.load_template("default")
    section = spec.section("sales")
    renderable, omitted = template_specs.select_renderable_charts(section, set())
    assert len(renderable) == len(section.charts)
    assert omitted == []


def _analytics_csv_without_revenue() -> tuple[str, io.BytesIO]:
    """A real, plausible shape: a non-ecommerce site's GA export, which
    genuinely has no revenue column at all -- not a malformed one."""
    text = (
        "date,channel_group,device_category,sessions,new_users,conversions\n"
        "2026-01-01,Organic Search,desktop,100,40,5\n"
        "2026-01-08,Organic Search,desktop,120,45,6\n"
        "2026-01-15,Paid Search,mobile,80,30,3\n"
    )
    buf = io.BytesIO(text.encode("utf-8"))
    return "analytics.csv", buf


@pytest.fixture
def force_deterministic_narrative(monkeypatch):
    """This environment has a local Ollama server reachable, so an
    unpatched build_report() call hits the real (slow, non-deterministic)
    local model -- exactly what test_report_object.py's/test_run_qa_cli.py's
    "don't route through build_report() for fixtures" convention exists to
    avoid. Forcing the deterministic fallback keeps these T2 tests fast and
    reproducible; T2's own mechanism (chart selection) doesn't care which
    narrative provider ran."""
    monkeypatch.setattr("app.agent._ollama_available", lambda: False)


def test_a_report_missing_revenue_omits_revenue_charts_but_keeps_the_rest(force_deterministic_narrative):
    name, buf = _analytics_csv_without_revenue()
    result = report_builder.build_report({"analytics": (name, buf)}, BRANDING, report_id="t2-no-revenue-test")
    obj = result["report_object"]

    captions = {c.caption for c in obj.charts}
    assert "Weekly revenue" not in captions
    assert "Revenue by channel" not in captions
    assert "Weekly sessions by channel" in captions
    assert "Conversion rate by channel" in captions
    assert "Sessions by device" in captions

    # Never a blank/0 chart: the omitted captions never made it into
    # section_charts at all (proven above), and the reason is explicit.
    omitted = obj.qa["data_availability"]["omitted_charts"]
    assert {o["caption"] for o in omitted} == {"Weekly revenue", "Revenue by channel"}

    # Visible in the output too, not just the QA JSON -- report.html already
    # renders report.data_quality.details[].message verbatim.
    dq = result["report"]["data_quality"]
    assert dq["by_kind"].get("chart_omitted") == 2  # one value (count=1) per omitted chart
    assert dq["by_kind"].get("column_missing") == 3  # by_kind sums row counts, not issue counts -- 3 rows
    details_by_kind = [d["kind"] for d in dq["details"]]
    assert details_by_kind.count("column_missing") == 1  # one distinct issue: revenue_usd
    assert details_by_kind.count("chart_omitted") == 2
    omitted_messages = [d["message"] for d in dq["details"] if d["kind"] == "chart_omitted"]
    assert any("Weekly revenue" in m for m in omitted_messages)
    assert any("Revenue by channel" in m for m in omitted_messages)


def test_omitted_chart_reason_is_a_real_sentence_not_a_code(force_deterministic_narrative):
    name, buf = _analytics_csv_without_revenue()
    result = report_builder.build_report({"analytics": (name, buf)}, BRANDING, report_id="t2-reason-test")
    reason = result["report_object"].qa["data_availability"]["omitted_charts"][0]["reason"]
    assert "revenue_usd" in reason
    assert "analytics" in reason


def test_data_availability_omission_never_flips_the_qa_badge_to_fail(force_deterministic_narrative):
    """Omitting a chart is a designed degrade, not a QA failure -- the badge
    computation (qa.py) is completely untouched by T2."""
    name, buf = _analytics_csv_without_revenue()
    result = report_builder.build_report({"analytics": (name, buf)}, BRANDING, report_id="t2-badge-test")
    assert result["report_object"].qa["badge"] in {"PASS", "PASS-WITH-WARNINGS"}


def test_a_report_with_every_required_column_has_no_data_availability_key(default_report_object):
    """aurora-home-goods has real revenue/amount data -- nothing should be
    omitted, and the QA JSON shouldn't grow a data_availability key at all
    when there's nothing to say (additive, not always-present)."""
    assert "data_availability" not in default_report_object.qa
