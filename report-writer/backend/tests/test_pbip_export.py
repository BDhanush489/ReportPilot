"""
Tests for Track D2 — app/pbip_export.py's parameterized Power BI generator
(D2.0: semantic model / tables; D2.1: chart -> visual mapping) and
app/pbip_validate.py's reused validation test gates.

Generates against a REAL ReportObject built from the already-onboarded
"aurora-home-goods" SQLite data context (report-writer/backend/data_contexts/
aurora-home-goods.json) -- not a hand-built fixture -- so these tests exercise
the exact same build_report_from_data_context() path a real scheduled run
would use. tests/fixtures/pbip_reference/ is the committed golden snapshot
from that real object (SemanticModel + Report + the .pbip root file): the
NEW reference D2.0's plan established, not a byte-diff against the old
hand-built d:\\IMDollars\\powerbi\\ demo, which is a row-level model and
therefore structurally different from this aggregates-only one by design.
"""
import filecmp
import json
from pathlib import Path

import pytest

from app import pbip_export, pbip_validate, report_builder

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "pbip_reference"
BRANDING = {"agency_name": "Test Agency", "client_name": "Aurora Home Goods",
            "primary_color": "#2a78d6", "accent_color": "#eda100"}

#: Track E1 -- the checked-in "aurora-home-goods" SQLite data context fixture
#: lives under this fixed tenant_id (data_contexts/demo-tenant/aurora-home-goods.json).
TENANT = "demo-tenant"

_EXPECTED_TABLES = [
    "AnalyticsTotals", "AnalyticsByChannel", "AnalyticsByDevice",
    "AnalyticsWeeklyByChannel", "AnalyticsWeeklyTotals",
    "SeoTotals", "SeoSeverityCounts", "SeoTopIssues",
    "SalesTotals", "SalesByRep", "SalesByLeadSource", "SalesByProduct", "SalesMonthly",
    "SeoWorstPages", "SeoOpportunityPages",
    "QaSummary",  # D2.2 -- "the badge travels"
]

# A tiny real 1x1 PNG, base64-encoded -- same fixture other tests in this repo use.
_TINY_PNG_DATA_URI = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture(scope="module")
def aurora_report_object():
    """Real generation, not a hand-built stub -- built once per test module
    since it's a genuine end-to-end report generation (SQLite query + LLM
    narrative), the same cost every other real-data test in this suite pays.

    Forces the deterministic fallback narrative (this environment has a
    reachable local Ollama server) -- module-scoped monkeypatch isn't
    available from pytest's function-scoped `monkeypatch` fixture, so this
    saves/restores app.agent._ollama_available by hand. Matters now in a way
    it never used to: D2.2's QaSummary table embeds qa["badge"], and badge
    depends on narrative content (check_traceability/check_unsupported_claims
    scan it) -- with a live/local model in the loop that made the golden-
    reference byte-comparison genuinely flaky the moment badge started
    appearing in a committed file, not just an in-memory assertion."""
    from app import agent
    from tests.conftest import seed_aurora_home_goods_data_context

    seed_aurora_home_goods_data_context()
    original = agent._ollama_available
    agent._ollama_available = lambda: False
    try:
        result = report_builder.build_report_from_data_context(
            TENANT, "aurora-home-goods", BRANDING, report_id="pbip-d2-test")
    finally:
        agent._ollama_available = original
    return result["report_object"]


def _all_tmdl_files(model_dir: Path) -> dict[str, str]:
    return {f.name: f.read_text(encoding="utf-8") for f in (model_dir / "definition" / "tables").glob("*.tmdl")}


def _visual_json(report_dir: Path, section: str, visual_name: str) -> dict:
    path = report_dir / "definition" / "pages" / section / "visuals" / visual_name / "visual.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# D2.0: real generation produces the expected tables, no client-specific literals
# ---------------------------------------------------------------------------

def test_generates_every_expected_table_with_no_skips(tmp_path, aurora_report_object):
    summary = pbip_export.build_pbip(aurora_report_object, tmp_path)
    assert sorted(summary["tables_written"]) == sorted(_EXPECTED_TABLES)
    assert summary["tables_skipped"] == []
    assert summary["project_name"] == "AuroraHomeGoods"


def test_grep_for_client_specific_strings_returns_zero_hits_for_a_different_client(tmp_path, aurora_report_object):
    """D2.0's own exit criterion, literally: feed a DIFFERENT ReportObject
    (different branding) and confirm nothing about the *previous* client
    leaks into the output -- and confirm the new client's own name is what
    actually appears, proving it's derived, not coincidentally absent."""
    import copy
    other = copy.deepcopy(aurora_report_object)
    other.branding = {**other.branding, "client_name": "Beacon Fitness Co"}

    summary = pbip_export.build_pbip(other, tmp_path)
    assert summary["project_name"] == "BeaconFitnessCo"

    all_text = "\n".join(_all_tmdl_files(Path(summary["model_dir"])).values())
    assert "Aurora" not in all_text
    assert "AuroraHomeGoods" not in str(summary["model_dir"])


# ---------------------------------------------------------------------------
# Determinism: same object in -> byte-identical PBIP out, across two runs
# ---------------------------------------------------------------------------

def test_two_independent_runs_produce_byte_identical_output(tmp_path, aurora_report_object):
    out_a, out_b = tmp_path / "run_a", tmp_path / "run_b"
    summary_a = pbip_export.build_pbip(aurora_report_object, out_a)
    summary_b = pbip_export.build_pbip(aurora_report_object, out_b)

    assert summary_a["tables_written"] == summary_b["tables_written"]
    assert summary_a["visuals_written"] == summary_b["visuals_written"]

    files_a = _all_tmdl_files(Path(summary_a["model_dir"]))
    files_b = _all_tmdl_files(Path(summary_b["model_dir"]))
    assert files_a == files_b  # every table's TMDL text is character-for-character identical

    platform_a = json.loads((Path(summary_a["model_dir"]) / ".platform").read_text())
    platform_b = json.loads((Path(summary_b["model_dir"]) / ".platform").read_text())
    assert platform_a["config"]["logicalId"] == platform_b["config"]["logicalId"]  # uuid5, not uuid4

    chart_a = _visual_json(Path(summary_a["report_dir"]), "sales", "chart_sales-1-revenue-by-sales-rep")
    chart_b = _visual_json(Path(summary_b["report_dir"]), "sales", "chart_sales-1-revenue-by-sales-rep")
    assert chart_a == chart_b


# ---------------------------------------------------------------------------
# Golden-reference regression: regenerating today matches the committed
# reference snapshot (SemanticModel + Report + .pbip) from a real run.
# ---------------------------------------------------------------------------

def test_regeneration_matches_the_committed_golden_reference(tmp_path, aurora_report_object):
    pbip_export.build_pbip(aurora_report_object, tmp_path)

    fresh_files = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file())
    reference_files = sorted(p.relative_to(FIXTURE_DIR) for p in FIXTURE_DIR.rglob("*") if p.is_file())
    assert fresh_files == reference_files, "generated file set no longer matches the committed reference tree"

    mismatches = [str(rel) for rel in fresh_files if not filecmp.cmp(tmp_path / rel, FIXTURE_DIR / rel, shallow=False)]
    assert mismatches == [], f"regenerated output drifted from the committed reference: {mismatches}"


# ---------------------------------------------------------------------------
# D2.2 — measure correctness: DAX measures from ONE spec that also emits a
# pandas-style recompute, reconciled against the canonical ReportObject,
# FAIL blocks export, and the QA badge + methodology note ship in the
# artifact (the global "the badge travels" invariant, applied to this
# export surface specifically).
# ---------------------------------------------------------------------------

def test_measures_are_emitted_on_the_tables_that_host_them(tmp_path, aurora_report_object):
    summary = pbip_export.build_pbip(aurora_report_object, tmp_path)
    tmdl = _all_tmdl_files(Path(summary["model_dir"]))
    assert "measure 'Total Revenue' = SUM(AnalyticsByChannel[revenue_usd])" in tmdl["AnalyticsByChannel.tmdl"]
    assert "measure 'Total Sessions' = SUM(AnalyticsByChannel[sessions])" in tmdl["AnalyticsByChannel.tmdl"]
    assert "measure 'Sales Total Revenue' = SUM(SalesByRep[revenue_usd])" in tmdl["SalesByRep.tmdl"]
    assert "measure 'Total Pages Crawled' = SUM(SeoSeverityCounts[count])" in tmdl["SeoSeverityCounts.tmdl"]
    # SeoTopIssues is a top-8 subset, not exhaustive -- deliberately no measure there.
    assert "measure" not in tmdl["SeoTopIssues.tmdl"]


def test_every_emitted_measure_actually_reconciles_with_the_real_canonical_object(aurora_report_object):
    """The recompute itself, run directly against real data (not trusting
    build_pbip's internal call to not have a bug of its own): sum the exact
    rows pbip_export would embed and confirm each equals the canonical
    ReportObject value the mission's own invariant says it must."""
    for spec in pbip_export._MEASURE_SPECS:
        table_spec = next(t for t in pbip_export._TABLE_SPECS if t.name == spec.table)
        result = pbip_export._resolve_table(aurora_report_object, table_spec)
        assert result is not None, f"{spec.table} unexpectedly unavailable"
        columns, rows = result
        idx = [c[0] for c in columns].index(spec.recompute_column)
        recomputed = sum(row[idx] for row in rows)
        canonical = aurora_report_object.resolve(spec.reconciles_with)
        assert abs(recomputed - canonical) < max(1e-6, abs(canonical) * 1e-6), spec.name


def test_a_tampered_metric_blocks_the_export_not_just_warns(tmp_path, aurora_report_object):
    """FAIL blocks export, literally: corrupt the canonical total this
    report's own AnalyticsByChannel rows would otherwise reconcile with,
    and confirm build_pbip refuses to write anything rather than shipping a
    workbook whose 'Total Revenue' measure would show a number that
    disagrees with the report it came from."""
    import copy
    tampered = copy.deepcopy(aurora_report_object)
    tampered.metrics["analytics"]["totals"]["revenue_usd"] += 999999.0

    with pytest.raises(pbip_export.MeasureReconciliationError, match="Total Revenue"):
        pbip_export.build_pbip(tampered, tmp_path)

    # Nothing left behind -- not a partial/broken export.
    assert not any(tmp_path.iterdir())


def test_qa_summary_table_carries_the_same_badge_every_other_surface_shows(tmp_path, aurora_report_object):
    summary = pbip_export.build_pbip(aurora_report_object, tmp_path)
    tmdl = _all_tmdl_files(Path(summary["model_dir"]))
    qa_tmdl = tmdl["QaSummary.tmdl"]
    assert aurora_report_object.qa["badge"] in qa_tmdl
    assert "deterministically" in qa_tmdl  # the methodology note, not just the bare badge


def test_no_qa_summary_table_when_the_object_carries_no_qa_verdict(tmp_path, aurora_report_object):
    import copy
    no_qa = copy.deepcopy(aurora_report_object)
    no_qa.qa = {}
    summary = pbip_export.build_pbip(no_qa, tmp_path)
    assert "QaSummary" not in summary["tables_written"]


# ---------------------------------------------------------------------------
# Missing sections: skipped with a stated reason, never a crash
# ---------------------------------------------------------------------------

def test_a_report_missing_a_section_skips_its_tables_and_visuals_with_a_reason(tmp_path, aurora_report_object):
    import copy
    analytics_only = copy.deepcopy(aurora_report_object)
    analytics_only.metrics.pop("seo", None)
    analytics_only.metrics.pop("sales", None)
    analytics_only.series.pop("sales", None)
    analytics_only.charts = [c for c in analytics_only.charts if c.section == "analytics"]

    summary = pbip_export.build_pbip(analytics_only, tmp_path)

    assert set(summary["tables_written"]) == {
        "AnalyticsTotals", "AnalyticsByChannel", "AnalyticsByDevice",
        "AnalyticsWeeklyByChannel", "AnalyticsWeeklyTotals",
        "QaSummary",  # D2.2 -- cross-cutting, written whenever the object carries a QA verdict at all
    }
    skipped_names = {s["table"] for s in summary["tables_skipped"]}
    assert skipped_names == {
        "SeoTotals", "SeoSeverityCounts", "SeoTopIssues", "SeoWorstPages", "SeoOpportunityPages",
        "SalesTotals", "SalesByRep", "SalesByLeadSource", "SalesByProduct", "SalesMonthly",
    }
    assert all(s["reason"] for s in summary["tables_skipped"])  # every skip states why, never blank
    assert len(summary["visuals_written"]) == 5  # only the analytics charts
    assert summary["visuals_skipped"] == []


def test_an_empty_list_metric_is_skipped_not_written_as_a_columnless_table(tmp_path, aurora_report_object):
    import copy
    no_reps = copy.deepcopy(aurora_report_object)
    no_reps.metrics["sales"]["by_rep"] = []

    summary = pbip_export.build_pbip(no_reps, tmp_path)
    assert "SalesByRep" not in summary["tables_written"]
    assert any(s["table"] == "SalesByRep" for s in summary["tables_skipped"])
    # D2.1: the backing table being gone means its chart is skipped too, with its own stated reason.
    rep_chart_skips = [s for s in summary["visuals_skipped"] if "SalesByRep" in s["reason"]]
    assert len(rep_chart_skips) == 1
    assert "chart_id" in rep_chart_skips[0]


def test_an_unregistered_caption_is_skipped_with_a_reason_not_a_crash(tmp_path, aurora_report_object):
    import copy
    from app.report_object import ChartRef
    mystery = copy.deepcopy(aurora_report_object)
    mystery.charts = [ChartRef(id="mystery-1", section="analytics", caption="A Chart Nobody Registered",
                                img="", chart_type="bar")]

    summary = pbip_export.build_pbip(mystery, tmp_path)
    assert summary["visuals_written"] == []
    assert len(summary["visuals_skipped"]) == 1
    assert summary["visuals_skipped"][0]["chart_id"] == "mystery-1"
    assert "no visual binding spec" in summary["visuals_skipped"][0]["reason"]


# ---------------------------------------------------------------------------
# D2.1: chart -> visual mapping is real, not a stub -- every chart in a real
# report gets a correctly-bound visual, multi-series charts bind every
# series, and annotations become real, traceable textbox text.
# ---------------------------------------------------------------------------

def test_every_chart_in_the_real_report_produces_a_visual(tmp_path, aurora_report_object):
    summary = pbip_export.build_pbip(aurora_report_object, tmp_path)
    assert set(summary["visuals_written"]) == {c.id for c in aurora_report_object.charts}
    assert summary["visuals_skipped"] == []


def test_chart_type_maps_to_the_single_visual_type_table(tmp_path, aurora_report_object):
    """Confirms chart_type is never re-decided here -- every visual's
    visualType comes straight from pbip_export._CHART_TYPE_TO_VISUAL keyed
    by the ChartRef's own chart_type, and every chart type this app
    actually emits (line/bar/pie) is covered."""
    summary = pbip_export.build_pbip(aurora_report_object, tmp_path)
    report_dir = Path(summary["report_dir"])
    for chart in aurora_report_object.charts:
        visual = _visual_json(report_dir, chart.section, f"chart_{chart.id}")
        expected = pbip_export._CHART_TYPE_TO_VISUAL[chart.chart_type]
        assert visual["visual"]["visualType"] == expected


def test_multi_series_chart_binds_both_series_not_just_the_first(tmp_path, aurora_report_object):
    summary = pbip_export.build_pbip(aurora_report_object, tmp_path)
    monthly_chart = next(c for c in aurora_report_object.charts if c.caption == "Monthly revenue & win rate")
    visual = _visual_json(Path(summary["report_dir"]), "sales", f"chart_{monthly_chart.id}")

    y_props = [p["field"]["Column"]["Property"] for p in visual["visual"]["query"]["queryState"]["Y"]["projections"]]
    assert y_props == ["revenue_usd", "win_rate"]


def test_series_grouped_chart_binds_a_series_dimension(tmp_path, aurora_report_object):
    summary = pbip_export.build_pbip(aurora_report_object, tmp_path)
    weekly_chart = next(c for c in aurora_report_object.charts if c.caption == "Weekly sessions by channel")
    visual = _visual_json(Path(summary["report_dir"]), "analytics", f"chart_{weekly_chart.id}")

    query_state = visual["visual"]["query"]["queryState"]
    assert query_state["Series"]["projections"][0]["field"]["Column"]["Property"] == "channel_group"


def test_annotation_becomes_a_textbox_carrying_the_real_annotation_text(tmp_path, aurora_report_object):
    charts_with_annotations = [c for c in aurora_report_object.charts if c.annotation]
    assert charts_with_annotations, "fixture data should produce at least one real annotation to test against"

    summary = pbip_export.build_pbip(aurora_report_object, tmp_path)
    report_dir = Path(summary["report_dir"])
    for chart in charts_with_annotations:
        assert chart.id in summary["annotations_written"]
        textbox = _visual_json(report_dir, chart.section, f"note_{chart.id}")
        assert textbox["visual"]["visualType"] == "textbox"
        run = textbox["visual"]["objects"]["general"][0]["properties"]["paragraphs"][0]["textRuns"][0]
        assert run["value"] == chart.annotation["text"]  # traces verbatim to A2's own computed text


def test_pages_are_only_created_for_sections_with_charts(tmp_path, aurora_report_object):
    summary = pbip_export.build_pbip(aurora_report_object, tmp_path)
    pages_json = json.loads((Path(summary["report_dir"]) / "definition" / "pages" / "pages.json").read_text())
    assert pages_json["pageOrder"] == ["analytics", "seo", "sales"]  # SECTION_ORDER, all three present
    assert pages_json["activePageName"] == "analytics"


def test_charts_gone_but_kpi_cards_remain_still_produces_a_report(tmp_path, aurora_report_object):
    """Zero charts no longer means zero content: KPI cards and the extra
    SEO tables populate a page independently of report_object.charts (see
    ensure_page() being called from the cards/extra-table loops too, not
    only the chart loop) -- a report with real totals but literally no
    ChartRefs should still show them, not an empty Report/.pbip. This is a
    real, intentional broadening from D2.1 alone, not a bug -- caught by
    this exact test failing when the KPI-cards feature was added, which is
    why it's asserted explicitly now instead of just updated silently."""
    import copy
    no_charts = copy.deepcopy(aurora_report_object)
    no_charts.charts = []

    summary = pbip_export.build_pbip(no_charts, tmp_path)
    assert summary["report_dir"] is not None
    assert summary["visuals_written"] == []
    assert len(summary["extra_content_written"]) > 0  # KPI cards + SEO tables + slicer, still real content
    assert (tmp_path / f"{summary['project_name']}.pbip").exists()


def test_no_report_or_pbip_is_written_when_a_report_has_no_content_at_all(tmp_path, aurora_report_object):
    """The genuinely-empty case: no charts AND no metrics/series data for any
    section, so KPI cards/extra tables/slicer all have nothing to bind to
    either -- this is where "don't write a half-empty Report/.pbip" still
    applies."""
    import copy
    empty = copy.deepcopy(aurora_report_object)
    empty.charts = []
    empty.metrics = {}
    empty.series = {}

    summary = pbip_export.build_pbip(empty, tmp_path)
    assert summary["report_dir"] is None
    assert not (tmp_path / f"{summary['project_name']}.pbip").exists()
    assert not (tmp_path / f"{summary['project_name']}.Report").exists()
    assert (tmp_path / f"{summary['project_name']}.SemanticModel").exists()  # D2.0's half is unaffected


# ---------------------------------------------------------------------------
# Branding: custom theme (client's real palette, not Power BI's default),
# and an optional logo -- both real, schema-checked resources, not stubs.
# ---------------------------------------------------------------------------

def test_custom_theme_reuses_theme_py_palette_and_this_reports_branding(tmp_path, aurora_report_object):
    from app import theme as theme_mod

    summary = pbip_export.build_pbip(aurora_report_object, tmp_path)
    assert summary["theme_written"] is True

    theme_path = Path(summary["report_dir"]) / "StaticResources" / "RegisteredResources" / "AuroraHomeGoodsTheme.json"
    theme_json = json.loads(theme_path.read_text(encoding="utf-8"))
    assert theme_json["dataColors"] == theme_mod.CATEGORICAL
    assert theme_json["good"] == theme_mod.STATUS["good"]
    assert theme_json["bad"] == theme_mod.STATUS["critical"]
    assert theme_json["accent"] == aurora_report_object.branding["primary_color"]

    report_json = json.loads((Path(summary["report_dir"]) / "definition" / "report.json").read_text())
    assert report_json["themeCollection"]["customTheme"]["name"] == "AuroraHomeGoodsTheme.json"
    registered = next(p for p in report_json["resourcePackages"] if p["name"] == "RegisteredResources")
    assert {"name": "AuroraHomeGoodsTheme.json", "path": "AuroraHomeGoodsTheme.json", "type": "CustomTheme"} in registered["items"]


def test_percent_format_string_does_not_double_multiply():
    """win_rate_pct is already *100 in metrics.py (66.9, not 0.669) -- a
    real DAX "0.0%" format token would ALSO multiply by 100 and show
    6690.0%. Confirms the literal-quoted "%" suffix is used instead."""
    assert pbip_export._format_string("double", "win_rate_pct") == '0.0"%"'
    assert pbip_export._format_string("double", "conversion_rate") == '0.0"%"'


def test_no_logo_means_no_logo_resource_or_image_visual(tmp_path, aurora_report_object):
    summary = pbip_export.build_pbip(aurora_report_object, tmp_path)  # BRANDING has no logo_data_uri
    assert summary["logo_written"] is False
    report_json = json.loads((Path(summary["report_dir"]) / "definition" / "report.json").read_text())
    registered = next(p for p in report_json["resourcePackages"] if p["name"] == "RegisteredResources")
    assert not any(item["type"] == "Image" for item in registered["items"])
    assert not any("logo_" in n for n in summary["visuals_written"] + summary["extra_content_written"])


def test_a_real_logo_is_decoded_registered_and_shown_on_every_page(tmp_path, aurora_report_object):
    import copy
    with_logo = copy.deepcopy(aurora_report_object)
    with_logo.branding = {**with_logo.branding, "logo_data_uri": _TINY_PNG_DATA_URI}

    summary = pbip_export.build_pbip(with_logo, tmp_path)
    assert summary["logo_written"] is True

    logo_path = Path(summary["report_dir"]) / "StaticResources" / "RegisteredResources" / "logo.png"
    assert logo_path.exists() and logo_path.stat().st_size > 0

    report_json = json.loads((Path(summary["report_dir"]) / "definition" / "report.json").read_text())
    registered = next(p for p in report_json["resourcePackages"] if p["name"] == "RegisteredResources")
    assert {"name": "logo.png", "path": "logo.png", "type": "Image"} in registered["items"]

    for section in ("analytics", "seo", "sales"):
        logo_visual = _visual_json(Path(summary["report_dir"]), section, f"logo_{section}")
        item_name = logo_visual["visual"]["objects"]["general"][0]["properties"]["imageUrl"]["expr"]["ResourcePackageItem"]["ItemName"]
        assert item_name == "logo.png"


def test_a_malformed_logo_is_dropped_not_fatal(tmp_path, aurora_report_object):
    import copy
    bad_logo = copy.deepcopy(aurora_report_object)
    bad_logo.branding = {**bad_logo.branding, "logo_data_uri": "not a data uri"}

    summary = pbip_export.build_pbip(bad_logo, tmp_path)  # must not raise
    assert summary["logo_written"] is False


# ---------------------------------------------------------------------------
# More content: KPI cards, previously-uncharted SEO tables, and one real
# interactive slicer -- all bound to real D2.0 tables, not decorative stubs.
# ---------------------------------------------------------------------------

def test_kpi_cards_reuse_html_dashboards_exact_labels(tmp_path, aurora_report_object):
    summary = pbip_export.build_pbip(aurora_report_object, tmp_path)
    card = _visual_json(Path(summary["report_dir"]), "sales", "kpi_sales_win_rate_pct")
    assert card["visual"]["visualType"] == "cardVisual"
    projection = card["visual"]["query"]["queryState"]["Data"]["projections"][0]
    assert projection["field"]["Column"]["Property"] == "win_rate_pct"
    assert projection["field"]["Column"]["Expression"]["SourceRef"]["Entity"] == "SalesTotals"
    title = card["visual"]["visualContainerObjects"]["title"][0]["properties"]["text"]["expr"]["Literal"]["Value"]
    assert title == "'Win Rate'"  # same label html_dashboard.py's _kpi_cards() already uses


def test_extra_content_tables_have_no_pdf_path_chart_but_still_get_a_visual(tmp_path, aurora_report_object):
    summary = pbip_export.build_pbip(aurora_report_object, tmp_path)
    assert "table_SeoWorstPages" in summary["extra_content_written"]
    assert "table_SeoOpportunityPages" in summary["extra_content_written"]

    table_visual = _visual_json(Path(summary["report_dir"]), "seo", "table_SeoWorstPages")
    assert table_visual["visual"]["visualType"] == "tableEx"
    fields = [p["field"]["Column"]["Property"] for p in table_visual["visual"]["query"]["queryState"]["Values"]["projections"]]
    assert fields == ["url", "issue_severity", "issues", "impressions_28d", "organic_sessions_28d"]


def test_analytics_slicer_is_bound_and_interactive(tmp_path, aurora_report_object):
    summary = pbip_export.build_pbip(aurora_report_object, tmp_path)
    assert "slicer_channel" in summary["extra_content_written"]

    slicer = _visual_json(Path(summary["report_dir"]), "analytics", "slicer_channel")
    assert slicer["visual"]["visualType"] == "slicer"
    projection = slicer["visual"]["query"]["queryState"]["Values"]["projections"][0]
    assert projection["field"]["Column"]["Property"] == "channel"
    assert projection["field"]["Column"]["Expression"]["SourceRef"]["Entity"] == "AnalyticsByChannel"


def test_no_visuals_overlap_within_a_page(tmp_path, aurora_report_object):
    """Cheap, real correctness check for the whole header/cards/charts/
    tables/slicer layout together: every visual's (y, y+height) interval on
    a page is disjoint from every other visual's on that same page."""
    summary = pbip_export.build_pbip(aurora_report_object, tmp_path)
    for section in ("analytics", "seo", "sales"):
        page_dir = Path(summary["report_dir"]) / "definition" / "pages" / section / "visuals"
        intervals = []
        for visual_dir in page_dir.iterdir():
            doc = json.loads((visual_dir / "visual.json").read_text())
            pos = doc["position"]
            intervals.append((pos["x"], pos["x"] + pos["width"], pos["y"], pos["y"] + pos["height"]))
        for i, (x1a, x1b, y1a, y1b) in enumerate(intervals):
            for x2a, x2b, y2a, y2b in intervals[i + 1:]:
                x_overlap = x1a < x2b and x2a < x1b
                y_overlap = y1a < y2b and y2a < y1b
                assert not (x_overlap and y_overlap), f"overlapping visuals on page {section}"


# ---------------------------------------------------------------------------
# Reused validators (parameterized from d:\IMDollars\powerbi\'s scripts)
# actually run clean against real generated output, D2.0 and D2.1 combined.
# ---------------------------------------------------------------------------

def test_reused_field_reference_checker_finds_no_errors(tmp_path, aurora_report_object):
    summary = pbip_export.build_pbip(aurora_report_object, tmp_path)
    errors = pbip_validate.check_field_references(tmp_path)
    assert errors == []
    model = pbip_validate.load_model(tmp_path)
    assert set(model) == set(summary["tables_written"])


def test_reused_schema_validator_passes_every_real_json_file(tmp_path, aurora_report_object):
    """Unlike D2.0 alone (which had zero *.json-named files to check), D2.1
    adds a real *.Report/ tree -- page.json/visual.json/report.json/etc --
    so this must now find and pass a real, non-zero number of files
    against Microsoft's own published schemas."""
    pbip_export.build_pbip(aurora_report_object, tmp_path)
    result = pbip_validate.validate_schemas(tmp_path)
    assert result.checked > 0
    assert result.failures == []
    assert result.ok is True


def test_specific_table_columns_match_the_real_metrics_shape(tmp_path, aurora_report_object):
    """Spot-checks a couple of tables against the exact keys metrics.py is
    known to emit (see this session's read of metrics.py), rather than only
    trusting the golden-fixture diff to catch a shape regression."""
    pbip_export.build_pbip(aurora_report_object, tmp_path)
    model = pbip_validate.load_model(tmp_path)

    assert model["SalesTotals"] == {"revenue_usd", "deals_won", "deals_lost", "win_rate_pct", "avg_deal_size_usd"}
    assert model["SeoTopIssues"] == {"issue", "count"}
    # D2.2 -- SeoSeverityCounts also hosts a measure now; parse_table_members
    # deliberately unifies column/measure names into one set (a Power BI
    # visual can bind to either), so this member set is columns UNION measures.
    assert model["SeoSeverityCounts"] == {"severity", "count", "Total Pages Crawled"}
    assert "channel" in model["AnalyticsByChannel"] and "revenue_usd" in model["AnalyticsByChannel"]


# ---------------------------------------------------------------------------
# The actual product surface: GET /api/report/{id}/export/pbip. Real HTTP
# round trip, not just calling export_pbip() directly -- this is what "can I
# export the graphs as a Power BI dashboard" cashes out to for a real user.
# ---------------------------------------------------------------------------

def test_export_endpoint_returns_a_downloadable_zip_of_a_real_dashboard(client, db_session):
    import zipfile
    from io import BytesIO

    from app import report_store
    from tests.conftest import seed_aurora_home_goods_data_context, seed_tenant

    tenant_id = seed_tenant(db_session, client, google_sub="g-pbip", email="a@northlight.com")
    seed_aurora_home_goods_data_context()
    result = report_builder.build_report_from_data_context(TENANT, "aurora-home-goods", BRANDING, report_id="pbip-endpoint-test")
    report_store.persist_report(tenant_id, "pbip-endpoint-test", result, BRANDING)

    resp = client.get("/api/report/pbip-endpoint-test/export/pbip")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert resp.headers["content-disposition"] == 'attachment; filename="report-pbip-endpoint-test.zip"'

    names = zipfile.ZipFile(BytesIO(resp.content)).namelist()
    assert any(n.endswith(".Report/definition/report.json") for n in names)  # real charts -> a real Report folder
    assert any("chart_analytics-0-weekly-sessions-by-channel" in n for n in names)
