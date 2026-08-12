"""
Tests for app/report_object.py (F0 — canonical report object) and the
report_builder.py wiring that assembles/renders it.

Fixtures are hand-built (same convention as test_qa.py's CLEAN_METRICS /
CLEAN_REPORT) rather than routed through report_builder.build_report(),
which would require a live LLM call — these tests exercise the object
itself: round-tripping, path resolution, and render-from-object, not
narrative generation.
"""
import copy
import json

from app.report_builder import _CHART_METRIC_PATHS, _build_chart_refs, _resolve_report_id, render_pdf_from_object
from app.report_object import Period, ReportObject, SourceInfo, resolve_path

METRICS = {
    "analytics": {
        "totals": {"sessions": 1000, "revenue_usd": 5000.0},
        "by_channel": [{"channel": "Organic Search", "revenue_usd": 2000.0, "conversion_rate": 3.0}],
        "by_device": [{"device_category": "mobile", "sessions": 600}],
    },
    "seo": {
        "severity_counts": {"good": 10, "warning": 2, "critical": 1},
        "top_issues": [["Missing meta description", 5]],
    },
    "sales": {
        "totals": {"revenue_usd": 3000.0},
        "by_rep": [{"sales_rep": "Alex", "revenue_usd": 1500.0}],
        "by_lead_source": [{"lead_source": "Referral", "revenue_usd": 1000.0}],
        "by_product": [{"product": "Widget", "revenue_usd": 500.0}],
    },
    "period_label": "2026-01-01 to 2026-06-30",
}

SERIES = {
    "analytics": {
        "weekly_by_channel": [{"week": "2026-01-05", "channel": "Organic Search", "sessions": 100}],
        "weekly_totals": [{"week": "2026-01-05", "revenue_usd": 500.0}],
    },
    "sales": {
        # Two distinct series drawn from the same table -- the case the
        # "exhaustive per series, not per chart" test below exists to catch.
        "monthly": [{"month": "2026-01", "revenue_usd": 1000.0, "win_rate": 0.55, "deals_won": 4}],
    },
}

SECTION_ORDER = ["analytics", "seo", "sales"]

SECTION_CHARTS = {
    "analytics": [
        {"caption": "Weekly sessions by channel", "img": "img1"},
        {"caption": "Weekly revenue", "img": "img2"},
        {"caption": "Revenue by channel", "img": "img3"},
        {"caption": "Conversion rate by channel", "img": "img4"},
        {"caption": "Sessions by device", "img": "img5"},
    ],
    "seo": [
        {"caption": "Site health", "img": "img6"},
        {"caption": "Top technical issues", "img": "img7"},
    ],
    "sales": [
        {"caption": "Monthly revenue & win rate", "img": "img8"},
        {"caption": "Revenue by sales rep", "img": "img9"},
        {"caption": "Revenue by lead source", "img": "img10"},
        {"caption": "Revenue by product", "img": "img11"},
    ],
}

NARRATIVE = {
    "report_title": "Aurora Home Goods — Performance Report",
    "period_label": "2026-01-01 to 2026-06-30",
    "_ai_generated": False,
    "executive_summary": "Sessions grew to 1,000, driving $5,000 in revenue.",
    "highlights": ["Organic Search led revenue at $2,000."],
    "watchouts": ["No significant risks identified this period."],
    "insights": [
        {"id": "health_score", "tag": "score", "title": "Health score", "headline": "B", "sub": "82 / 100",
         "detail": "Solid overall performance."},
    ],
    "data_quality": {"total_issues_found": 1, "total_values_affected": 2, "details": [{"message": "1 blank row dropped."}]},
    "sections": [
        {"heading": "Web Analytics", "narrative": "Revenue reached $5,000.", "recommendations": ["Double down on Organic Search."]},
        {"heading": "SEO & Site Health", "narrative": "Site health is good overall.", "recommendations": []},
        {"heading": "Sales Performance", "narrative": "Win rate held steady at 55%.", "recommendations": ["Coach reps below quota."]},
    ],
    "next_steps": ["Review this report with the account team."],
}

BRANDING = {"agency_name": "Northlight", "client_name": "Aurora", "primary_color": "#2a78d6", "accent_color": "#eda100"}

QA_DICT = {
    "badge": "PASS",
    "failing_checks": [],
    "traceability": {"ok": True, "numbers_checked": 3, "fail": [], "warnings": []},
    "aggregation_sanity": {"ok": True, "mismatches": [], "inconclusive_sources": []},
    "unsupported_claims": {"ok": True, "claims_checked": 1, "unlinked": []},
}


def _build_object() -> ReportObject:
    chart_refs = _build_chart_refs(SECTION_CHARTS, SECTION_ORDER, METRICS, SERIES)
    return ReportObject(
        report_id="test-report-1",
        period=Period(label=METRICS["period_label"]),
        sources={
            "analytics": SourceInfo(row_count=100, sha256="a" * 64),
            "sales": SourceInfo(row_count=50, sha256="b" * 64),
        },
        metrics=METRICS,
        series=SERIES,
        charts=chart_refs,
        narrative=NARRATIVE,
        qa=QA_DICT,
        branding=BRANDING,
        section_order=SECTION_ORDER,
    )


# ---------------------------------------------------------------------------
# resolve_path / ReportObject.resolve
# ---------------------------------------------------------------------------

def test_resolve_path_walks_dict_keys():
    assert resolve_path({"a": {"b": {"c": 42}}}, "a.b.c") == 42


def test_resolve_path_walks_list_indices():
    assert resolve_path({"a": [{"x": 1}, {"x": 2}]}, "a.1.x") == 2


def test_resolve_path_returns_none_for_missing_segment():
    assert resolve_path({"a": {"b": 1}}, "a.missing") is None


def test_resolve_path_empty_string_returns_root():
    root = {"a": 1}
    assert resolve_path(root, "") is root


def test_object_resolve_dispatches_on_first_segment():
    obj = _build_object()
    assert obj.resolve("metrics.analytics.totals.revenue_usd") == 5000.0
    assert obj.resolve("series.sales.monthly") == SERIES["sales"]["monthly"]


def test_object_resolve_unknown_namespace_returns_none():
    obj = _build_object()
    assert obj.resolve("nonexistent.foo") is None


# ---------------------------------------------------------------------------
# Chart metric-path mapping: every real chart this app renders must map to
# a non-"unknown" chart_type and at least one metric_path, and every one of
# those paths must actually resolve against the object it was built into.
# A caption that drifts out of sync with _CHART_METRIC_PATHS is caught here,
# not discovered later by A2.
# ---------------------------------------------------------------------------

def test_every_known_chart_caption_maps_to_a_real_chart_type_and_path():
    for (section, caption), (chart_type, metric_paths) in _CHART_METRIC_PATHS.items():
        assert chart_type != "unknown", f"{section}/{caption} has no chart_type mapping"
        assert metric_paths, f"{section}/{caption} has no metric_paths mapping"


def test_all_chart_refs_built_from_fixture_have_resolvable_metric_paths():
    obj = _build_object()
    for chart in obj.charts:
        assert chart.chart_type != "unknown", chart.caption
        assert chart.metric_paths, chart.caption
        for path in chart.metric_paths:
            resolved = obj.resolve(path)
            assert resolved is not None, f"{chart.caption}: {path} did not resolve"


def test_multi_series_chart_resolves_every_field_it_actually_plots():
    """'Monthly revenue & win rate' draws two distinct series (revenue_usd,
    win_rate) from the same series.sales.monthly table. One resolvable path
    to the table root isn't enough on its own -- the resolved records must
    actually carry both fields the chart plots, or annotating the win-rate
    line later (A2) would silently have nothing to point at."""
    obj = _build_object()
    chart = next(c for c in obj.charts if c.caption == "Monthly revenue & win rate")
    assert len(chart.metric_paths) == 1
    records = obj.resolve(chart.metric_paths[0])
    assert records, "monthly series resolved empty"
    for record in records:
        assert "revenue_usd" in record
        assert "win_rate" in record


def test_chart_metric_paths_never_point_into_metrics_for_dataframe_backed_series():
    """The three charts drawn from what used to be private DataFrame keys
    (_weekly, _weekly_totals, _monthly) must reference `series.*`, not
    `metrics.*` -- keeping them out of metrics is what protects
    traceability's matching haystack (see report_object.py's docstring)."""
    dataframe_backed_captions = {"Weekly sessions by channel", "Weekly revenue", "Monthly revenue & win rate"}
    obj = _build_object()
    for chart in obj.charts:
        if chart.caption in dataframe_backed_captions:
            assert all(p.startswith("series.") for p in chart.metric_paths), chart.caption


# ---------------------------------------------------------------------------
# Round-trip losslessness
# ---------------------------------------------------------------------------

def test_round_trip_object_to_json_to_object_is_lossless():
    obj = _build_object()
    round_tripped = ReportObject.from_dict(json.loads(json.dumps(obj.to_dict())))
    assert round_tripped.to_dict() == obj.to_dict()


def test_to_dict_output_is_json_serializable():
    obj = _build_object()
    # Raises if anything in the tree isn't JSON-safe (e.g. a stray DataFrame).
    json.dumps(obj.to_dict())


# ---------------------------------------------------------------------------
# Render identity: the PDF/HTML renderer's only input is the object, so
# rendering the same object twice -- once directly, once through a
# serialize/deserialize round-trip -- must produce byte-identical HTML.
# (PDF bytes are NOT compared here: two independent report_builder runs
# against identical input were confirmed to differ by ~1KB from LLM
# narrative variance alone, so a PDF-byte-hash assertion would be flaky for
# reasons unrelated to what this test is actually checking. HTML string is
# the renderer's real, deterministic output surface once the object/
# narrative content is fixed.)
# ---------------------------------------------------------------------------

def test_render_identity_across_round_trip():
    obj = _build_object()
    html_direct, _ = render_pdf_from_object(obj)

    round_tripped = ReportObject.from_dict(json.loads(json.dumps(obj.to_dict())))
    html_round_tripped, _ = render_pdf_from_object(round_tripped)

    assert html_direct == html_round_tripped


def test_render_from_object_produces_nonempty_pdf():
    obj = _build_object()
    html, pdf_bytes = render_pdf_from_object(obj)
    assert html.strip().startswith("<!DOCTYPE") or "<html" in html.lower()
    assert len(pdf_bytes) > 0


def test_render_reflects_every_chart_from_the_object():
    """Confirms the render path actually goes through obj.charts (via
    to_legacy_report_dict), not some other copy -- every chart's img marker
    the fixture set should show up in the rendered HTML."""
    obj = _build_object()
    html, _ = render_pdf_from_object(obj)
    for chart in obj.charts:
        assert chart.img in html


def test_pdf_shows_the_qa_badge_with_legible_contrast():
    """The PDF renderer (xhtml2pdf) doesn't support 8-digit #RRGGBBAA hex
    the way a browser does -- an earlier version of this badge used that
    trick and rendered as same-color text on a same-color "tinted"
    background, effectively invisible. Guards against that regression by
    asserting the badge's own text and background tokens are never equal,
    for every badge tier, not just checking the text is present."""
    from app import theme

    obj = _build_object()
    obj.qa = {**QA_DICT, "badge": "FAIL"}
    html, _ = render_pdf_from_object(obj)
    assert "QA: FAIL" in html
    assert "qa-badge-fail" in html

    for tier, colors in theme.to_template_context()["badge"].items():
        assert colors["text"] != colors["bg"], f"{tier} badge text/background are identical -- illegible"


# ---------------------------------------------------------------------------
# to_legacy_report_dict
# ---------------------------------------------------------------------------

def test_to_legacy_report_dict_reattaches_charts_per_section_in_order():
    obj = _build_object()
    legacy = obj.to_legacy_report_dict()
    assert [s["heading"] for s in legacy["sections"]] == [s["heading"] for s in NARRATIVE["sections"]]
    captions_by_section = {s["heading"]: [c["caption"] for c in s["charts"]] for s in legacy["sections"]}
    assert captions_by_section["Web Analytics"] == [c["caption"] for c in SECTION_CHARTS["analytics"]]
    assert captions_by_section["SEO & Site Health"] == [c["caption"] for c in SECTION_CHARTS["seo"]]
    assert captions_by_section["Sales Performance"] == [c["caption"] for c in SECTION_CHARTS["sales"]]


def test_to_legacy_report_dict_does_not_mutate_the_object():
    obj = _build_object()
    before = copy.deepcopy(obj.narrative)
    obj.to_legacy_report_dict()
    assert obj.narrative == before


# ---------------------------------------------------------------------------
# report_id fallback: the object must always carry a real id, even on
# call paths (smoke_test.py, direct build_report() calls) that don't pass
# one through yet.
# ---------------------------------------------------------------------------

def test_resolve_report_id_falls_back_when_none():
    generated = _resolve_report_id(None)
    assert isinstance(generated, str) and len(generated) > 0


def test_resolve_report_id_prefers_caller_supplied_value():
    assert _resolve_report_id("caller-assigned-id") == "caller-assigned-id"


def test_resolve_report_id_fallback_values_are_unique():
    assert _resolve_report_id(None) != _resolve_report_id(None)
