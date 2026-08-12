"""Tests for app/viz/outliers.py — IQR outlier detection + data-quality flags."""
import pandas as pd

from app.viz.outliers import compute_data_quality_flags, detect_outliers_iqr


def test_detects_a_known_injected_outlier():
    values = [10, 12, 11, 13, 12, 11, 10, 12, 13, 11, 9999]  # 9999 is the injected outlier
    df = pd.DataFrame({"value": values})
    report = detect_outliers_iqr(df, "value")
    assert report.count == 1
    assert report.outlier_values == [9999.0]
    assert df["value"].iloc[-1] == 9999  # confirms source df untouched


def test_no_outliers_in_a_tight_distribution():
    df = pd.DataFrame({"value": [10, 11, 10, 12, 11, 10, 11, 12]})
    report = detect_outliers_iqr(df, "value")
    assert report.count == 0


def test_too_few_points_reports_no_outliers_not_a_false_positive():
    df = pd.DataFrame({"value": [1, 1000]})
    report = detect_outliers_iqr(df, "value")
    assert report.count == 0
    assert report.lower_bound != report.lower_bound  # NaN -- "couldn't judge," not "checked, clean"


def test_source_dataframe_is_never_mutated_by_outlier_detection():
    df = pd.DataFrame({"value": [10, 12, 11, 13, 12, 11, 10, 12, 13, 11, 9999]})
    before = df.copy(deep=True)
    detect_outliers_iqr(df, "value")
    pd.testing.assert_frame_equal(df, before)


def test_outlier_row_indices_map_back_to_the_original_dataframe():
    df = pd.DataFrame({"value": [10, 12, 11, 13, 12, 11, 10, 12, 13, 11, 9999]}, index=range(100, 111))
    report = detect_outliers_iqr(df, "value")
    assert report.outlier_row_indices == [110]
    assert df.loc[110, "value"] == 9999


def test_data_quality_flags_count_nulls_and_duplicates_for_the_pair():
    df = pd.DataFrame({
        "channel": ["A", "A", "B", None, "C"],
        "revenue": [10, 10, 20, 30, None],
    })
    flags = compute_data_quality_flags(df, "channel", "revenue", "categorical", "numeric")
    assert flags.null_rows_excluded == 2  # row 3 (None channel), row 4 (None revenue)
    assert flags.duplicate_pair_count == 1  # rows 0/1 are an exact (channel, revenue) duplicate


def test_data_quality_flags_report_mixed_type_columns():
    df = pd.DataFrame({"a": ["1", "x", "2"], "b": [1, 2, 3]})
    flags = compute_data_quality_flags(df, "a", "b", "mixed", "numeric")
    assert flags.x_is_mixed_type
    assert not flags.y_is_mixed_type
