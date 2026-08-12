"""Tests for app/viz/suggestions.py — rule-based, no-LLM chart suggestions."""
import pandas as pd

from app.viz.profiler import profile_dataframe
from app.viz.suggestions import suggest_charts

N = 40


def _dataset() -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=N, freq="D").astype(str),
        "channel": [["Organic", "Paid", "Email"][i % 3] for i in range(N)],
        "revenue": [round(100 + i * 3.3, 2) for i in range(N)],
        "conversions": [i % 10 for i in range(N)],
        "customer_comments": [f"a long free text comment about the order number {i}" for i in range(N)],
        "order_id": [f"ORD{i:05d}" for i in range(N)],
    })


def test_suggests_a_time_trend_first():
    profile = profile_dataframe(_dataset())
    suggestions = suggest_charts(profile, n=5)
    assert suggestions[0].x_col == "date"
    assert suggestions[0].chart_type == "line"
    assert "over time" in suggestions[0].label


def test_suggests_a_category_breakdown():
    profile = profile_dataframe(_dataset())
    suggestions = suggest_charts(profile, n=10)
    breakdowns = [s for s in suggestions if s.x_col == "channel"]
    assert breakdowns
    assert breakdowns[0].chart_type == "bar"
    assert "by channel" in breakdowns[0].label


def test_suggestions_never_reference_a_column_not_in_the_file():
    profile = profile_dataframe(_dataset())
    real_columns = set(profile.columns.keys())
    suggestions = suggest_charts(profile, n=20)
    for s in suggestions:
        assert s.x_col in real_columns
        assert s.y_col in real_columns


def test_suggestions_never_include_free_text_or_id_columns():
    profile = profile_dataframe(_dataset())
    suggestions = suggest_charts(profile, n=20)
    for s in suggestions:
        assert s.x_col not in ("customer_comments", "order_id")
        assert s.y_col not in ("customer_comments", "order_id")


def test_respects_the_requested_count():
    profile = profile_dataframe(_dataset())
    assert len(suggest_charts(profile, n=2)) == 2
    assert len(suggest_charts(profile, n=1)) == 1


def test_suggestions_are_deterministic():
    profile = profile_dataframe(_dataset())
    a = [s.to_dict() for s in suggest_charts(profile, n=5)]
    b = [s.to_dict() for s in suggest_charts(profile, n=5)]
    assert a == b


def test_no_suggestions_for_a_dataset_with_no_numeric_columns():
    df = pd.DataFrame({"channel": ["A", "B", "C"] * 5, "region": ["N", "S", "E"] * 5})
    profile = profile_dataframe(df)
    assert suggest_charts(profile, n=5) == []


def test_every_suggestion_is_actually_a_good_fit_per_suitability():
    from app.viz.suitability import evaluate_suitability
    profile = profile_dataframe(_dataset())
    for s in suggest_charts(profile, n=20):
        x_type = profile.columns[s.x_col].inferred_type
        y_type = profile.columns[s.y_col].inferred_type
        cardinality = profile.columns[s.x_col].cardinality
        verdict = evaluate_suitability(s.chart_type, x_type, y_type, cardinality)
        assert verdict.verdict == "good", s
