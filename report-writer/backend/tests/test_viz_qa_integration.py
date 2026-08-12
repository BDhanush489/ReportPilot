"""Tests for qa.py's chart-traceability extension (Lever 5) — every plotted
value from the viz engine must trace to a deterministically recomputed one,
same badge concept as the narrative QA layer."""
import copy

import pandas as pd

from app.qa import check_chart_traceability, run_chart_qa
from app.viz.engine import build_chart
from app.viz.profiler import profile_dataframe


def _channel_revenue_df() -> pd.DataFrame:
    return pd.DataFrame({
        "channel": ["Organic", "Organic", "Paid", "Paid", "Paid", "Email"],
        "revenue": [100.0, 150.0, 80.0, 90.0, 70.0, 500.0],
    })


def test_a_correctly_built_chart_passes_traceability():
    df = _channel_revenue_df()
    profile = profile_dataframe(df)
    result = build_chart(df, profile, "channel", "revenue", "bar")
    assert result.accepted

    qa_result = run_chart_qa(result.chart, df)
    assert qa_result.badge == "PASS"
    assert qa_result.aggregation.ok


def test_a_tampered_plotted_value_fails_traceability():
    df = _channel_revenue_df()
    profile = profile_dataframe(df)
    result = build_chart(df, profile, "channel", "revenue", "bar")
    chart = result.chart

    tampered = copy.deepcopy(chart)
    tampered.aggregate_with_outliers.points[0].y += 99999.0

    qa_result = run_chart_qa(tampered, df)
    assert qa_result.badge == "FAIL"
    assert not qa_result.aggregation.ok
    assert qa_result.aggregation.mismatches[0].reported != qa_result.aggregation.mismatches[0].recomputed


def test_traceability_reports_recomputed_value_for_debugging():
    df = _channel_revenue_df()
    profile = profile_dataframe(df)
    result = build_chart(df, profile, "channel", "revenue", "bar")
    chart = result.chart
    tampered = copy.deepcopy(chart)
    tampered.aggregate_with_outliers.points[0].x = "Nonexistent Channel"

    qa_result = run_chart_qa(tampered, df)
    assert qa_result.badge == "FAIL"
    # a plotted x-value with no corresponding recomputed group is a mismatch
    # against nothing -- recomputed comes back NaN, not silently skipped.
    mismatch = qa_result.aggregation.mismatches[0]
    assert mismatch.recomputed != mismatch.recomputed  # NaN


def test_check_chart_traceability_is_json_serializable():
    import json
    df = _channel_revenue_df()
    profile = profile_dataframe(df)
    result = build_chart(df, profile, "channel", "revenue", "bar")
    qa_result = run_chart_qa(result.chart, df)
    json.dumps(qa_result.to_dict())  # must not raise
