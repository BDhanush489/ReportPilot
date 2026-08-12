"""
Orchestrates one chart request: validate the field pair, evaluate chart-type
suitability, compute the aggregate (with outliers included, the default —
and a second view with them excluded, for comparison), detect outliers and
data-quality issues, and package it all into one ChartResult.

A field pair that doesn't make sense (unknown column, two free-text columns,
a non-numeric y) is *rejected with a reason* — never an exception. An
uploaded file's columns are inherently unpredictable; treating a bad but
well-formed request as a crash would contradict the whole "schema-agnostic,
never crash on real customer data" premise this engine exists for.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from .aggregates import AggregateResult, compute_aggregate
from .outliers import DataQualityFlags, OutlierReport, compute_data_quality_flags, detect_outliers_iqr
from .profiler import DatasetProfile
from .suitability import SuitabilityVerdict, evaluate_suitability


@dataclass
class ChartResult:
    x_col: str
    y_col: str
    x_type: str
    y_type: str
    chart_type: str
    suitability: SuitabilityVerdict
    aggregate_with_outliers: AggregateResult
    aggregate_without_outliers: AggregateResult
    outliers: OutlierReport
    data_quality: DataQualityFlags

    def to_dict(self) -> dict:
        return {
            "x_col": self.x_col, "y_col": self.y_col, "x_type": self.x_type, "y_type": self.y_type,
            "chart_type": self.chart_type,
            "suitability": self.suitability.to_dict(),
            "aggregate_with_outliers": self.aggregate_with_outliers.to_dict(),
            "aggregate_without_outliers": self.aggregate_without_outliers.to_dict(),
            "outliers": self.outliers.to_dict(),
            "data_quality": self.data_quality.to_dict(),
        }


@dataclass
class ChartRequestResult:
    accepted: bool
    rejection_reason: str | None = None
    chart: ChartResult | None = None

    def to_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "rejection_reason": self.rejection_reason,
            "chart": self.chart.to_dict() if self.chart else None,
        }


def validate_field_pair(profile: DatasetProfile, x_col: str, y_col: str) -> str | None:
    """Returns a rejection reason string, or None if the pair is usable."""
    if x_col not in profile.columns:
        return f"column {x_col!r} not found in this dataset"
    if y_col not in profile.columns:
        return f"column {y_col!r} not found in this dataset"
    if x_col == y_col:
        return "x and y must be different columns"

    x_prof, y_prof = profile.columns[x_col], profile.columns[y_col]

    if x_prof.inferred_type == "empty":
        return f"{x_col!r} has no non-null values"
    if y_prof.inferred_type == "empty":
        return f"{y_col!r} has no non-null values"

    if x_prof.is_free_text and y_prof.is_free_text:
        return f"both {x_col!r} and {y_col!r} are free text — neither has a numeric or categorical axis to plot"

    if y_prof.inferred_type != "numeric_quantity":
        return f"y_col {y_col!r} must be numeric to chart a magnitude (it's {y_prof.inferred_type!r})"

    if x_prof.is_free_text:
        return f"x_col {x_col!r} is free text, not a usable grouping/ordering axis"

    if x_prof.inferred_type not in ("temporal", "categorical", "numeric_identifier", "numeric_quantity"):
        return f"x_col {x_col!r} has type {x_prof.inferred_type!r}, not chartable"

    return None


def build_chart(
    df: pd.DataFrame, profile: DatasetProfile, x_col: str, y_col: str, chart_type: str, agg_fn: str = "sum",
) -> ChartRequestResult:
    rejection = validate_field_pair(profile, x_col, y_col)
    if rejection:
        return ChartRequestResult(accepted=False, rejection_reason=rejection)

    x_prof, y_prof = profile.columns[x_col], profile.columns[y_col]

    suitability = evaluate_suitability(chart_type, x_prof.inferred_type, y_prof.inferred_type, x_prof.cardinality)

    outliers = detect_outliers_iqr(df, y_col)
    with_outliers = compute_aggregate(df, x_col, y_col, x_prof.inferred_type, y_prof.inferred_type, agg_fn)

    if outliers.count:
        without_df = df.drop(index=outliers.outlier_row_indices)
    else:
        without_df = df
    without_outliers = compute_aggregate(without_df, x_col, y_col, x_prof.inferred_type, y_prof.inferred_type, agg_fn)

    data_quality = compute_data_quality_flags(df, x_col, y_col, x_prof.inferred_type, y_prof.inferred_type)

    chart = ChartResult(
        x_col=x_col, y_col=y_col, x_type=x_prof.inferred_type, y_type=y_prof.inferred_type,
        chart_type=chart_type, suitability=suitability,
        aggregate_with_outliers=with_outliers, aggregate_without_outliers=without_outliers,
        outliers=outliers, data_quality=data_quality,
    )
    return ChartRequestResult(accepted=True, chart=chart)
