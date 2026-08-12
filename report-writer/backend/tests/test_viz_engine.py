"""Tests for app/viz/engine.py — the chart-request orchestrator."""
import pandas as pd

from app.viz.engine import build_chart, validate_field_pair
from app.viz.profiler import profile_dataframe


def _sales_df() -> pd.DataFrame:
    return pd.DataFrame({
        "channel": ["Organic", "Organic", "Paid", "Paid", "Paid", "Email"],
        "revenue": [100.0, 150.0, 80.0, 90.0, 70.0, 50000.0],  # 50000 is a deliberate outlier
        "notes": [f"free text comment number {i} about this deal in detail" for i in range(6)],
    })


def test_valid_pair_is_accepted_and_builds_a_full_chart():
    df = _sales_df()
    profile = profile_dataframe(df)
    result = build_chart(df, profile, "channel", "revenue", "bar")
    assert result.accepted
    assert result.rejection_reason is None
    assert result.chart.suitability.verdict == "good"
    assert result.chart.aggregate_with_outliers.points


def test_two_free_text_columns_are_rejected_with_a_reason_not_an_exception():
    df = _sales_df()
    profile = profile_dataframe(df)
    df["notes2"] = [f"another long free text field entry number {i} here" for i in range(len(df))]
    result = build_chart(df, profile_dataframe(df), "notes", "notes2", "bar")
    assert result.accepted is False
    assert "free text" in result.rejection_reason
    assert result.chart is None


def test_unknown_column_is_rejected_with_a_reason():
    df = _sales_df()
    profile = profile_dataframe(df)
    result = build_chart(df, profile, "channel", "does_not_exist", "bar")
    assert not result.accepted
    assert "not found" in result.rejection_reason


def test_same_column_for_x_and_y_is_rejected():
    df = _sales_df()
    profile = profile_dataframe(df)
    result = build_chart(df, profile, "revenue", "revenue", "scatter")
    assert not result.accepted
    assert "different columns" in result.rejection_reason


def test_non_numeric_y_is_rejected():
    df = _sales_df()
    profile = profile_dataframe(df)
    result = build_chart(df, profile, "revenue", "channel", "bar")
    assert not result.accepted
    assert "numeric" in result.rejection_reason


def test_engine_renders_the_requested_chart_and_flags_a_bad_choice_with_alternatives():
    # A poor-fit chart type still gets *built* -- the engine renders what
    # was asked AND emits a verdict, it doesn't refuse to answer.
    # Irregular, non-sequential, non-integer values -- a real revenue series
    # is never a pure step-1 arithmetic sequence (which would legitimately
    # look like an autoincrement id).
    df = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=200, freq="D").astype(str),
        "revenue": [round(100 + (i * 37 % 89) * 3.3, 2) for i in range(200)],
    })
    profile = profile_dataframe(df)
    result = build_chart(df, profile, "date", "revenue", "pie")
    assert result.accepted  # still builds
    assert result.chart.suitability.verdict == "discouraged"
    assert "line" in result.chart.suitability.alternatives
    assert result.chart.aggregate_with_outliers.points  # data was still computed


def test_outliers_are_never_silently_removed_from_the_default_view():
    df = _sales_df()
    profile = profile_dataframe(df)
    result = build_chart(df, profile, "channel", "revenue", "bar")
    assert result.chart.outliers.count == 1
    assert 50000.0 in result.chart.outliers.outlier_values
    # the default ("with outliers") aggregate must still include the outlier's contribution
    email_point = next(p for p in result.chart.aggregate_with_outliers.points if p.x == "Email")
    assert email_point.y == 50000.0
    # the alternate view exists and differs, but isn't a mutation of anything
    assert result.chart.aggregate_without_outliers.points != result.chart.aggregate_with_outliers.points


def test_validate_field_pair_returns_none_for_a_good_pair():
    df = _sales_df()
    profile = profile_dataframe(df)
    assert validate_field_pair(profile, "channel", "revenue") is None
