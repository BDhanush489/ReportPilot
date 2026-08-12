"""
End-to-end, headless fixture suite for the schema-agnostic viz engine
(exit criterion 7) — runs on a real CSV file (tests/fixtures/viz_e2e_sample.csv,
generated once, checked in) with:
  (a) a known injected outlier — row 42's revenue is 750000.0, everything
      else is in the 100-350 range.
  (b) a known best-fit chart — date (temporal) x revenue (numeric), line.
  (c) a deliberately wrong requested chart — pie of that same 60-point
      time series, the exact scenario named in the exit criteria.

Ties every other module in app/viz/ together on one file, the way an actual
caller would use them: load -> profile -> request a chart -> get suggestions
-> check traceability. No UI, no server, no browser -- pure function calls,
which is what "headless" means for this engine (unlike Lever 4's HTML
dashboard, there's no rendered page to drive here).
"""
from pathlib import Path

import pytest

from app.qa import run_chart_qa
from app.viz.engine import build_chart
from app.viz.profiler import load_any, profile_dataframe
from app.viz.suggestions import suggest_charts

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "viz_e2e_sample.csv"
INJECTED_OUTLIER_VALUE = 750000.0


def _load_fixture():
    with open(FIXTURE_PATH, "rb") as fh:
        df, _load_meta = load_any(fh, FIXTURE_PATH.name)
    return df


def test_fixture_loads_and_profiles_with_no_hardcoded_schema():
    df = _load_fixture()
    profile = profile_dataframe(df)
    assert profile.row_count == 60
    assert profile.columns["date"].inferred_type == "temporal"
    assert profile.columns["channel"].inferred_type == "categorical"
    assert profile.columns["revenue"].inferred_type == "numeric_quantity"
    # "ORD00042"-style tokens aren't numeric-parseable at all (the "ORD"
    # prefix), so this is a short-token id-LIKE text, not a numeric_identifier
    # -- see profiler.py's own _infer_type / test_viz_profiler.py's identical
    # "isn't numeric-parseable, so it can't be numeric_identifier" distinction.
    assert profile.columns["order_id"].inferred_type == "categorical"
    assert profile.columns["notes"].is_free_text


def test_the_injected_outlier_is_caught_and_never_silently_removed():
    df = _load_fixture()
    profile = profile_dataframe(df)
    result = build_chart(df, profile, "date", "revenue", "line")
    assert result.accepted

    outliers = result.chart.outliers
    assert outliers.count == 1
    assert outliers.outlier_values == [INJECTED_OUTLIER_VALUE]

    # "with outliers" (the default) must still include its contribution
    with_total = sum(p.y for p in result.chart.aggregate_with_outliers.points)
    without_total = sum(p.y for p in result.chart.aggregate_without_outliers.points)
    assert with_total > without_total
    assert with_total - without_total == pytest.approx(INJECTED_OUTLIER_VALUE)

    # and the raw fixture file on disk / loaded df is untouched -- df is
    # string-dtype (load_any's own "no silent dtype inference" contract), so
    # compare the parsed values, not the raw text.
    import pandas as pd
    assert INJECTED_OUTLIER_VALUE in pd.to_numeric(df["revenue"]).values


def test_the_known_best_fit_chart_is_approved_with_no_alternatives_needed():
    df = _load_fixture()
    profile = profile_dataframe(df)
    result = build_chart(df, profile, "date", "revenue", "line")
    assert result.accepted
    assert result.chart.suitability.verdict == "good"
    assert result.chart.suitability.alternatives == []


def test_the_deliberately_wrong_chart_is_flagged_and_line_is_suggested():
    # Pie of a 60-point time series -- the exact scenario the exit criteria
    # name explicitly.
    df = _load_fixture()
    profile = profile_dataframe(df)
    result = build_chart(df, profile, "date", "revenue", "pie")
    assert result.accepted  # still renders what was asked
    assert result.chart.suitability.verdict == "discouraged"
    assert "line" in result.chart.suitability.alternatives
    assert result.chart.aggregate_with_outliers.points  # data was still computed despite the bad fit


def test_suggestions_surface_the_known_best_fit_chart_without_inventing_columns():
    df = _load_fixture()
    profile = profile_dataframe(df)
    suggestions = suggest_charts(profile, n=5)

    real_columns = set(profile.columns.keys())
    for s in suggestions:
        assert s.x_col in real_columns
        assert s.y_col in real_columns

    top = suggestions[0]
    assert top.x_col == "date"
    assert top.y_col == "revenue"
    assert top.chart_type == "line"

    # id/free-text columns never get suggested as axes
    assert all(s.x_col not in ("order_id", "notes") and s.y_col not in ("order_id", "notes") for s in suggestions)


def test_every_plotted_value_traces_via_the_qa_badge():
    df = _load_fixture()
    profile = profile_dataframe(df)
    result = build_chart(df, profile, "channel", "revenue", "bar")
    assert result.accepted

    qa_result = run_chart_qa(result.chart, df)
    assert qa_result.badge == "PASS"
    assert qa_result.aggregation.ok


def test_wrong_chart_requests_numbers_still_trace_even_when_the_chart_type_is_bad():
    # Suitability and traceability are independent checks -- a poor chart
    # *choice* doesn't mean the *numbers* backing it are wrong.
    df = _load_fixture()
    profile = profile_dataframe(df)
    result = build_chart(df, profile, "date", "revenue", "pie")
    assert result.chart.suitability.verdict == "discouraged"

    qa_result = run_chart_qa(result.chart, df)
    assert qa_result.badge == "PASS"
