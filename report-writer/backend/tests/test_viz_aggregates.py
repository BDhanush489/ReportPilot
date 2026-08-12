"""Tests for app/viz/aggregates.py — the single aggregation path shared by
the chart engine and the QA traceability extension."""
import pandas as pd
import pytest

from app.viz.aggregates import compute_aggregate


def test_categorical_x_numeric_y_groups_and_sums_sorted_descending():
    df = pd.DataFrame({
        "channel": ["Organic", "Organic", "Paid", "Paid", "Paid", "Email"],
        "revenue": [100, 150, 80, 90, 70, 500],
    })
    result = compute_aggregate(df, "channel", "revenue", "categorical", "numeric_quantity", "sum")
    assert result.bucket_rule == "group_by_category"
    values = {p.x: p.y for p in result.points}
    assert values == {"Email": 500, "Organic": 250, "Paid": 240}
    # sorted descending by value
    assert [p.x for p in result.points] == ["Email", "Organic", "Paid"]
    n_by_x = {p.x: p.n for p in result.points}
    assert n_by_x == {"Email": 1, "Organic": 2, "Paid": 3}


def test_temporal_x_numeric_y_buckets_by_day_for_short_span():
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=10, freq="D"),
        "sessions": list(range(10)),
    })
    result = compute_aggregate(df, "date", "sessions", "temporal", "numeric_quantity", "sum")
    assert result.bucket_rule == "time:day"
    assert len(result.points) == 10


def test_temporal_x_numeric_y_buckets_by_month_for_long_span():
    df = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=400, freq="D"),
        "sessions": [1] * 400,
    })
    result = compute_aggregate(df, "date", "sessions", "temporal", "numeric_quantity", "sum")
    assert result.bucket_rule == "time:month"
    assert len(result.points) <= 14  # ~400 days spans ~13-14 months


def test_numeric_x_numeric_y_returns_raw_pairs_no_aggregation():
    df = pd.DataFrame({"price": [1.0, 2.0, 3.0], "demand": [30.0, 20.0, 10.0]})
    result = compute_aggregate(df, "price", "demand", "numeric_quantity", "numeric_quantity", "sum")
    assert result.bucket_rule == "raw_pairs"
    assert result.agg_fn == "none"
    assert len(result.points) == 3
    assert all(p.n == 1 for p in result.points)


def test_mean_agg_fn_is_respected():
    df = pd.DataFrame({"channel": ["A", "A", "B"], "value": [10, 20, 100]})
    result = compute_aggregate(df, "channel", "value", "categorical", "numeric_quantity", "mean")
    values = {p.x: p.y for p in result.points}
    assert values["A"] == 15.0
    assert values["B"] == 100.0


def test_non_numeric_y_type_raises_value_error():
    df = pd.DataFrame({"channel": ["A", "B"], "region": ["North", "South"]})
    with pytest.raises(ValueError, match="must be numeric"):
        compute_aggregate(df, "channel", "region", "categorical", "categorical", "sum")


def test_unknown_agg_fn_raises_value_error():
    df = pd.DataFrame({"channel": ["A", "B"], "value": [1, 2]})
    with pytest.raises(ValueError, match="unknown agg_fn"):
        compute_aggregate(df, "channel", "value", "categorical", "numeric_quantity", "median_of_medians")


def test_source_dataframe_is_never_mutated():
    df = pd.DataFrame({"channel": ["A", "B"], "value": ["10", "20"]})  # value as strings
    before = df.copy(deep=True)
    compute_aggregate(df, "channel", "value", "categorical", "numeric_quantity", "sum")
    pd.testing.assert_frame_equal(df, before)


def test_result_is_deterministic_across_repeated_calls():
    # This IS the traceability property: QA re-runs this on re-loaded rows
    # and must get byte-identical points back.
    df = pd.DataFrame({
        "channel": ["Organic", "Paid", "Organic", "Email"],
        "revenue": [100.5, 80.25, 40.1, 500.0],
    })
    r1 = compute_aggregate(df, "channel", "revenue", "categorical", "numeric_quantity", "sum")
    r2 = compute_aggregate(df, "channel", "revenue", "categorical", "numeric_quantity", "sum")
    assert r1.to_dict() == r2.to_dict()


def test_null_rows_are_excluded_from_aggregation_not_crashing():
    df = pd.DataFrame({"channel": ["A", None, "B"], "value": [10, 20, None]})
    result = compute_aggregate(df, "channel", "value", "categorical", "numeric_quantity", "sum")
    values = {p.x: p.y for p in result.points}
    assert values == {"A": 10.0}
