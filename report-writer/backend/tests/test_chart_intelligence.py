"""Tests for app/chart_intelligence.py (Track A1 — auto chart-type) and its
wiring into report_builder.py's _build_chart_refs."""
from app.chart_intelligence import choose_chart_type, infer_field_type
from app.report_builder import _CHART_SPECS, _build_chart_refs

# ---------------------------------------------------------------------------
# infer_field_type
# ---------------------------------------------------------------------------

def test_infers_numeric():
    assert infer_field_type([1, 2.5, 3]) == "numeric"


def test_infers_temporal_from_iso_dates():
    assert infer_field_type(["2026-01-05", "2026-02-01"]) == "temporal"


def test_infers_temporal_from_iso_year_month():
    assert infer_field_type(["2026-01", "2026-02"]) == "temporal"


def test_infers_categorical_for_plain_strings():
    assert infer_field_type(["Organic Search", "Paid Search"]) == "categorical"


def test_empty_values_fall_back_to_categorical_not_a_crash():
    assert infer_field_type([]) == "categorical"
    assert infer_field_type([None, None]) == "categorical"


# ---------------------------------------------------------------------------
# choose_chart_type: real, non-trivial verdicts -- both "good" and a
# genuinely triggered "discouraged", not just an always-good rubber stamp.
# ---------------------------------------------------------------------------

def test_bar_over_a_handful_of_categories_is_good():
    records = [{"channel": c, "revenue_usd": 100.0 * i} for i, c in enumerate(["Organic", "Paid", "Email"])]
    choice = choose_chart_type(records, "records", "channel", "revenue_usd", "bar")
    assert choice.verdict == "good"


def test_pie_over_many_categories_is_genuinely_discouraged():
    """The whole point of A1: this isn't a static per-caption assumption --
    feed it data this report actually has, and a pie chart over 15 lead
    sources must come back discouraged, with bar suggested instead."""
    records = [{"lead_source": f"Source {i}", "revenue_usd": 100.0} for i in range(15)]
    choice = choose_chart_type(records, "records", "lead_source", "revenue_usd", "pie")
    assert choice.verdict == "discouraged"
    assert "bar" in choice.alternatives


def test_pie_over_a_handful_of_categories_is_good():
    records = [{"lead_source": f"Source {i}", "revenue_usd": 100.0} for i in range(4)]
    choice = choose_chart_type(records, "records", "lead_source", "revenue_usd", "pie")
    assert choice.verdict == "good"


def test_line_over_temporal_x_is_good():
    records = [{"week": "2026-01-05", "revenue_usd": 500.0}, {"week": "2026-01-12", "revenue_usd": 600.0}]
    choice = choose_chart_type(records, "records", "week", "revenue_usd", "line")
    assert choice.verdict == "good"


def test_line_over_categorical_x_is_discouraged():
    records = [{"channel": "Organic", "revenue_usd": 500.0}, {"channel": "Paid", "revenue_usd": 600.0}]
    choice = choose_chart_type(records, "records", "channel", "revenue_usd", "line")
    assert choice.verdict == "discouraged"


def test_dict_counts_shape_is_read_as_keys_and_values():
    choice = choose_chart_type({"good": 10, "warning": 2, "critical": 1}, "dict_counts", None, None, "bar")
    assert choice.verdict == "good"


def test_pairs_shape_is_read_as_first_and_second_element():
    choice = choose_chart_type([("title_length", 68), ("h1_count", 20)], "pairs", None, None, "bar")
    assert choice.verdict == "good"


# ---------------------------------------------------------------------------
# Ambiguous / missing data degrades safely -- never a crash, always a
# labeled reason.
# ---------------------------------------------------------------------------

def test_empty_resolved_data_is_ambiguous_not_a_crash():
    choice = choose_chart_type([], "records", "channel", "revenue_usd", "bar")
    assert choice.verdict == "ambiguous_data"
    assert choice.reason


def test_none_resolved_data_is_ambiguous_not_a_crash():
    choice = choose_chart_type(None, "records", "channel", "revenue_usd", "bar")
    assert choice.verdict == "ambiguous_data"


def test_missing_field_in_records_is_ambiguous_not_a_crash():
    records = [{"other_field": 1}]
    choice = choose_chart_type(records, "records", "channel", "revenue_usd", "bar")
    assert choice.verdict == "ambiguous_data"


def test_malformed_pairs_shape_is_ambiguous_not_a_crash():
    choice = choose_chart_type([{"not": "a pair"}], "pairs", None, None, "bar")
    assert choice.verdict == "ambiguous_data"


# ---------------------------------------------------------------------------
# Wiring: every registered chart spec produces a real verdict against
# realistic data, and the mechanism is actually exercised end to end via
# _build_chart_refs (not just called directly in isolation above).
# ---------------------------------------------------------------------------

def _fixture_metrics_and_series():
    metrics = {
        "analytics": {
            "by_channel": [{"channel": "Organic Search", "revenue_usd": 2000.0, "conversion_rate": 3.0}],
            "by_device": [{"device_category": "mobile", "sessions": 600}],
        },
        "seo": {
            "severity_counts": {"good": 10, "warning": 2, "critical": 1},
            "top_issues": [["Missing meta description", 5]],
        },
        "sales": {
            "by_rep": [{"sales_rep": "Alex", "revenue_usd": 1500.0}],
            "by_lead_source": [{"lead_source": "Referral", "revenue_usd": 1000.0}],
            "by_product": [{"product": "Widget", "revenue_usd": 500.0}],
        },
    }
    series = {
        "analytics": {
            "weekly_by_channel": [{"week": "2026-01-05", "channel": "Organic Search", "sessions": 100}],
            "weekly_totals": [{"week": "2026-01-05", "revenue_usd": 500.0}],
        },
        "sales": {"monthly": [{"month": "2026-01", "revenue_usd": 1000.0, "win_rate": 0.55}]},
    }
    return metrics, series


def test_every_registered_chart_spec_produces_a_real_verdict_via_build_chart_refs():
    metrics, series = _fixture_metrics_and_series()
    section_charts = {}
    for (section, caption) in _CHART_SPECS:
        section_charts.setdefault(section, []).append({"caption": caption, "img": "x"})

    refs = _build_chart_refs(section_charts, ["analytics", "seo", "sales"], metrics, series)
    assert len(refs) == len(_CHART_SPECS)
    for ref in refs:
        assert ref.suitability_verdict in ("good", "discouraged"), (ref.caption, ref.suitability_verdict)
        assert ref.suitability_reason
