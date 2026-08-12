"""Tests for app/chart_annotation.py (Track A2 — on-chart annotation) and
its wiring into report_builder.py / report.html / html_dashboard.py."""
from app.chart_annotation import ChartAnnotation, detect_notable_point
from app.html_dashboard import _chart_highlights
from app.report_builder import _build_chart_refs
from app.report_object import Period, ReportObject, SourceInfo

# ---------------------------------------------------------------------------
# detect_notable_point: deterministic, real detection -- not a rubber stamp
# ---------------------------------------------------------------------------

def test_detects_a_genuine_outlier():
    records = [{"channel": c, "revenue_usd": v} for c, v in
               [("A", 100), ("B", 110), ("C", 95), ("D", 105), ("E", 5000)]]  # E is a real IQR outlier
    ann = detect_notable_point(records, "records", "channel", "revenue_usd")
    assert ann.kind == "outlier"
    assert ann.x_label == "E"
    assert ann.y_value == 5000


def test_detects_the_largest_adjacent_step_when_no_outlier_and_x_is_temporal():
    records = [{"week": w, "sessions": v} for w, v in
               [("2026-01-05", 100), ("2026-01-12", 105), ("2026-01-19", 108),
                ("2026-01-26", 250), ("2026-02-02", 253)]]
    ann = detect_notable_point(records, "records", "week", "sessions")
    assert ann.kind == "largest_delta"
    assert ann.x_label == "2026-01-26"
    assert ann.y_value == 250


def test_detects_a_peak_instead_of_largest_delta_when_x_is_categorical():
    """"Largest adjacent step" only means something for an ordered
    (temporal) x-axis -- row order in a categorical breakdown like
    lead_source is arbitrary, so this must fall through to peak instead of
    treating list order as if it were a timeline."""
    records = [{"lead_source": s, "revenue_usd": v} for s, v in
               [("Referral", 100), ("Partner", 200), ("Outbound", 300), ("Website", 400)]]
    ann = detect_notable_point(records, "records", "lead_source", "revenue_usd")
    assert ann.kind == "peak"
    assert ann.x_label == "Website"
    assert ann.y_value == 400


def test_midnight_iso_timestamp_x_label_is_trimmed_to_a_plain_date():
    """Series promoted via pandas to_json(date_format="iso") render a
    midnight timestamp as "2026-01-19T00:00:00.000" -- correct but not
    something a consultant wants to read in an annotation caption. Caught
    live rendering a real PDF page; this is display formatting on an
    already-correct string, never a value change."""
    records = [{"week": w, "sessions": v} for w, v in
               [("2026-01-05T00:00:00.000", 100), ("2026-01-12T00:00:00.000", 5000)]]
    ann = detect_notable_point(records, "records", "week", "sessions")
    assert ann.x_label == "2026-01-12"
    assert "T00:00:00" not in ann.text


def test_non_midnight_timestamp_x_label_is_left_untouched():
    records = [{"week": w, "sessions": v} for w, v in
               [("2026-01-05T14:30:00.000", 100), ("2026-01-12T09:15:00.000", 5000)]]
    ann = detect_notable_point(records, "records", "week", "sessions")
    assert ann.x_label == "2026-01-12T09:15:00.000"  # not silently altered -- only midnight is trimmed


def test_annotation_number_is_lifted_verbatim_from_the_source_series():
    """The annotation's y_value must be one of the actual values in the
    series it was computed from -- traceability by construction, not by a
    separate check."""
    records = [{"channel": c, "revenue_usd": v} for c, v in
               [("A", 100), ("B", 110), ("C", 95), ("D", 105), ("E", 5000)]]
    ann = detect_notable_point(records, "records", "channel", "revenue_usd")
    assert ann.y_value in [r["revenue_usd"] for r in records]


# ---------------------------------------------------------------------------
# Graceful degradation: "nothing notable" is a real, reachable state
# ---------------------------------------------------------------------------

def test_returns_none_for_a_perfectly_flat_series():
    records = [{"channel": c, "revenue_usd": 100} for c in ["A", "B", "C"]]
    assert detect_notable_point(records, "records", "channel", "revenue_usd") is None


def test_returns_none_for_a_single_point():
    records = [{"channel": "A", "revenue_usd": 100}]
    assert detect_notable_point(records, "records", "channel", "revenue_usd") is None


def test_returns_none_for_empty_data():
    assert detect_notable_point([], "records", "channel", "revenue_usd") is None
    assert detect_notable_point(None, "records", "channel", "revenue_usd") is None


def test_returns_none_when_field_is_missing_rather_than_crash():
    records = [{"other": 1}, {"other": 2}]
    assert detect_notable_point(records, "records", "channel", "revenue_usd") is None


# ---------------------------------------------------------------------------
# Priority ordering: outlier > largest_delta > peak, and exactly one result
# ---------------------------------------------------------------------------

def test_outlier_takes_priority_over_a_large_adjacent_step():
    # A->B is a big step (100->900), but E is the real statistical outlier
    # -- and the far more extreme one (100 also technically falls outside
    # the tight fence formed by the 900/910/920 cluster, but E deviates
    # much further, so it must be the one picked, not just "first by index").
    records = [{"x": x, "y": y} for x, y in
               [("A", 100), ("B", 900), ("C", 920), ("D", 910), ("E", 50000)]]
    ann = detect_notable_point(records, "records", "x", "y")
    assert ann.kind == "outlier"
    assert ann.x_label == "E"


# ---------------------------------------------------------------------------
# Wiring: report_builder._build_chart_refs attaches annotations; report.html
# and html_dashboard.py's chart-highlights list both render the same text.
# ---------------------------------------------------------------------------

def test_build_chart_refs_attaches_annotation_to_charts_that_have_one():
    # IQR needs >=4 points to compute meaningful quartiles (viz.outliers'
    # own documented threshold) -- 3 reps isn't enough for "outlier" to ever
    # fire, so this uses 4 to exercise the real path _build_chart_refs
    # wires through, not an artificially small fixture.
    metrics = {
        "sales": {"by_rep": [{"sales_rep": r, "revenue_usd": v} for r, v in
                              [("Alex", 1000), ("Sam", 1100), ("Kim", 1050), ("Jo", 50000)]]},
    }
    section_charts = {"sales": [{"caption": "Revenue by sales rep", "img": "x"}]}
    refs = _build_chart_refs(section_charts, ["sales"], metrics, {})
    assert refs[0].annotation is not None
    assert refs[0].annotation["kind"] == "outlier"
    assert refs[0].annotation["x_label"] == "Jo"


def test_build_chart_refs_leaves_annotation_none_when_nothing_notable():
    metrics = {
        "sales": {"by_rep": [{"sales_rep": r, "revenue_usd": 1000} for r in ["Alex", "Sam", "Jo"]]},
    }
    section_charts = {"sales": [{"caption": "Revenue by sales rep", "img": "x"}]}
    refs = _build_chart_refs(section_charts, ["sales"], metrics, {})
    assert refs[0].annotation is None


def _obj_with_annotated_chart() -> ReportObject:
    metrics = {
        "sales": {"by_rep": [{"sales_rep": r, "revenue_usd": v} for r, v in
                              [("Alex", 1000), ("Sam", 1100), ("Jo", 50000)]]},
    }
    section_charts = {"sales": [{"caption": "Revenue by sales rep", "img": "img1"}]}
    refs = _build_chart_refs(section_charts, ["sales"], metrics, {})
    return ReportObject(
        report_id="ann-test", period=Period(label="p"),
        sources={"sales": SourceInfo(row_count=3, sha256="a" * 64)},
        metrics=metrics, series={}, charts=refs,
        narrative={
            "report_title": "t", "period_label": "p", "executive_summary": "",
            "highlights": [], "watchouts": [],
            "sections": [{"heading": "Sales Performance", "narrative": "n", "recommendations": []}],
            "next_steps": [],
        },
        qa={"badge": "PASS", "failing_checks": [], "traceability": {}, "aggregation_sanity": {}, "unsupported_claims": {}},
        branding={}, section_order=["sales"],
    )


def test_render_pdf_from_object_includes_the_annotation_text():
    from app.report_builder import render_pdf_from_object
    obj = _obj_with_annotated_chart()
    html, _ = render_pdf_from_object(obj)
    assert obj.charts[0].annotation["text"] in html
    assert "chart-annotation" in html


def test_dashboard_chart_highlights_carries_the_same_annotation_text():
    obj = _obj_with_annotated_chart()
    highlights = _chart_highlights(obj)
    assert len(highlights) == 1
    assert highlights[0]["annotation"]["text"] == obj.charts[0].annotation["text"]
    assert highlights[0]["caption"] == "Revenue by sales rep"


def test_pdf_and_dashboard_render_the_identical_annotation_text():
    """The exit criterion is explicit: PDF and dashboard render annotations
    identically. Confirms it's the literal same string in both outputs."""
    from app.html_dashboard import build_dashboard
    from app.report_builder import render_pdf_from_object

    obj = _obj_with_annotated_chart()
    pdf_html, _ = render_pdf_from_object(obj)
    dashboard_html = build_dashboard(obj)

    annotation_text = obj.charts[0].annotation["text"]
    assert annotation_text in pdf_html
    assert annotation_text in dashboard_html


def test_dashboard_omits_highlights_section_when_nothing_notable():
    obj = _obj_with_annotated_chart()
    obj.charts[0].annotation = None
    from app.html_dashboard import build_dashboard
    html = build_dashboard(obj)
    assert 'id="chart-highlights"' not in html
