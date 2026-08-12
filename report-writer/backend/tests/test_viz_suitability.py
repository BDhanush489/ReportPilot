"""Tests for app/viz/suitability.py — the deterministic chart-type verdict matrix."""
import pytest

from app.viz.suitability import evaluate_suitability


def test_line_is_good_for_temporal_x_numeric_y():
    v = evaluate_suitability("line", "temporal", "numeric_quantity", x_cardinality=200)
    assert v.verdict == "good"
    assert v.alternatives == []


def test_pie_of_a_200_point_time_series_is_discouraged_and_suggests_line():
    # The exact scenario named in the exit criteria.
    v = evaluate_suitability("pie", "temporal", "numeric_quantity", x_cardinality=200)
    assert v.verdict == "discouraged"
    assert "line" in v.alternatives


def test_pie_with_too_many_categories_is_discouraged_and_suggests_bar():
    # 15 categories: too many for a pie (cap 8) but still fine for a bar
    # (cap 30) -- a real, viable alternative, not just "any other type."
    v = evaluate_suitability("pie", "categorical", "numeric_quantity", x_cardinality=15)
    assert v.verdict == "discouraged"
    assert "bar" in v.alternatives


def test_pie_with_too_many_categories_for_any_chart_type_has_no_fake_alternative():
    # 40 categories exceeds *both* pie's cap (8) and bar's cap (30) -- there
    # genuinely isn't a good fit among the 4 supported chart types without a
    # data transformation (top-N) that's outside suitability's job. An empty
    # alternatives list here is the honest answer, not a bug to paper over.
    v = evaluate_suitability("pie", "categorical", "numeric_quantity", x_cardinality=40)
    assert v.verdict == "discouraged"
    assert v.alternatives == []


def test_pie_with_few_categories_is_good():
    v = evaluate_suitability("pie", "categorical", "numeric_quantity", x_cardinality=4)
    assert v.verdict == "good"


def test_bar_is_good_for_categorical_x_within_readable_cardinality():
    v = evaluate_suitability("bar", "categorical", "numeric_quantity", x_cardinality=6)
    assert v.verdict == "good"


def test_bar_with_pathological_cardinality_has_no_fake_alternative():
    # 500 raw categories doesn't fit any of the 4 supported chart types --
    # the honest answer is "none of these," conveyed via the reason string,
    # not a fabricated alternative that isn't actually a good fit either.
    v = evaluate_suitability("bar", "categorical", "numeric_quantity", x_cardinality=500)
    assert v.verdict == "discouraged"
    assert v.alternatives == []
    assert "too many" in v.reason


def test_scatter_is_good_for_numeric_x_numeric_y():
    v = evaluate_suitability("scatter", "numeric_quantity", "numeric_quantity", x_cardinality=1000)
    assert v.verdict == "good"


def test_scatter_is_discouraged_for_categorical_x():
    v = evaluate_suitability("scatter", "categorical", "numeric_quantity", x_cardinality=5)
    assert v.verdict == "discouraged"
    assert "bar" in v.alternatives


def test_non_numeric_y_is_always_discouraged_regardless_of_chart_type():
    for chart_type in ("line", "bar", "pie", "scatter"):
        v = evaluate_suitability(chart_type, "categorical", "categorical", x_cardinality=5)
        assert v.verdict == "discouraged"
        assert "numeric" in v.reason


def test_unknown_chart_type_raises_value_error():
    with pytest.raises(ValueError, match="unknown chart_type"):
        evaluate_suitability("waffle", "categorical", "numeric_quantity", x_cardinality=5)


def test_bar_for_temporal_x_is_good_when_few_buckets_discouraged_when_many():
    few = evaluate_suitability("bar", "temporal", "numeric_quantity", x_cardinality=6)
    assert few.verdict == "good"
    many = evaluate_suitability("bar", "temporal", "numeric_quantity", x_cardinality=200)
    assert many.verdict == "discouraged"
    assert "line" in many.alternatives
