"""
Outlier and abnormality detection for a chosen field pair.

Rule: IQR (Tukey's fences, k=1.5) — the standard, distribution-agnostic
boxplot rule. Chosen over z-score because z-score assumes a roughly normal
distribution and is itself distorted by the very outliers it's trying to
find (a few extreme points inflate the mean/std used to compute it); IQR's
quartiles are far more robust to that. Documented here once, not re-decided
per call.

Hard rule: nothing in this module mutates the source DataFrame or removes a
row from it. Outliers are *reported*, never dropped — the two aggregate
views (with/without) are separate computed results, not two different
versions of the input data.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

IQR_K = 1.5


@dataclass
class OutlierReport:
    method: str
    column: str
    lower_bound: float
    upper_bound: float
    outlier_row_indices: list[int] = field(default_factory=list)
    outlier_values: list[float] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.outlier_row_indices)

    def to_dict(self) -> dict:
        return {
            "method": self.method, "column": self.column,
            "lower_bound": self.lower_bound, "upper_bound": self.upper_bound,
            "count": self.count,
            "outlier_row_indices": self.outlier_row_indices,
            "outlier_values": self.outlier_values,
        }


def detect_outliers_iqr(df: pd.DataFrame, column: str, k: float = IQR_K) -> OutlierReport:
    """Row indices refer to df's own index — caller is responsible for using
    the same (unmodified, unsorted-away) df to look them back up."""
    numeric = pd.to_numeric(df[column], errors="coerce")
    non_null = numeric.dropna()
    if len(non_null) < 4:
        # Quartiles are meaningless on fewer than a handful of points --
        # reporting zero outliers here is honest ("not enough data to judge"),
        # not the same claim as "we checked and found none."
        return OutlierReport(method="iqr", column=column, lower_bound=float("nan"), upper_bound=float("nan"))

    q1, q3 = non_null.quantile([0.25, 0.75])
    iqr = q3 - q1
    lower = q1 - k * iqr
    upper = q3 + k * iqr
    mask = (non_null < lower) | (non_null > upper)
    outlier_series = non_null[mask]

    return OutlierReport(
        method="iqr", column=column, lower_bound=float(lower), upper_bound=float(upper),
        outlier_row_indices=[int(i) for i in outlier_series.index],
        outlier_values=[float(v) for v in outlier_series.values],
    )


@dataclass
class DataQualityFlags:
    x_col: str
    y_col: str
    null_rows_excluded: int
    duplicate_pair_count: int
    x_is_mixed_type: bool
    y_is_mixed_type: bool

    def to_dict(self) -> dict:
        return asdict(self)


def compute_data_quality_flags(df: pd.DataFrame, x_col: str, y_col: str, x_type: str, y_type: str) -> DataQualityFlags:
    """Flags relevant to *this specific pair* of columns, distinct from
    profiler.py's whole-row duplicate count -- two rows can be duplicates on
    (x_col, y_col) without being duplicates across the whole table, and
    that's what actually affects this chart."""
    pair = df[[x_col, y_col]]
    null_rows_excluded = int(pair.isna().any(axis=1).sum())
    duplicate_pair_count = int(pair.dropna().duplicated().sum())
    return DataQualityFlags(
        x_col=x_col, y_col=y_col,
        null_rows_excluded=null_rows_excluded,
        duplicate_pair_count=duplicate_pair_count,
        x_is_mixed_type=(x_type == "mixed"),
        y_is_mixed_type=(y_type == "mixed"),
    )
