"""
The one place a field-pair chart's plotted values get computed.

Both engine.py (building a chart) and qa.py's chart-traceability extension
(re-checking one) call compute_aggregate() — never two independent
implementations of "how to aggregate," which is exactly the reuse pattern
qa.py's check_aggregation_sanity already established for the fixed-schema
pipeline (it recomputes via metrics.py's own functions, not a re-derived
copy of the logic).

Bucketing/grouping rules are deterministic and documented so a QA re-run
against the same source rows reproduces the identical points every time.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

VALID_AGG_FNS = ("sum", "mean", "median", "count")


@dataclass
class AggregatePoint:
    x: object
    y: float
    n: int  # row count backing this point — part of what's traceable, not just y


@dataclass
class AggregateResult:
    x_col: str
    y_col: str | None
    agg_fn: str
    bucket_rule: str
    points: list[AggregatePoint] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "x_col": self.x_col, "y_col": self.y_col, "agg_fn": self.agg_fn,
            "bucket_rule": self.bucket_rule, "points": [asdict(p) for p in self.points],
        }


def _time_bucket_rule(x: pd.Series) -> str:
    """Deterministic bucket-size choice from the span of dates present —
    the same df always yields the same rule, which is what makes a later
    QA recomputation reproduce identical points."""
    non_null = pd.to_datetime(x.dropna(), errors="coerce", format="mixed").dropna()
    if non_null.empty:
        return "day"
    span_days = (non_null.max() - non_null.min()).days
    if span_days > 180:
        return "month"
    if span_days > 30:
        return "week"
    return "day"


def _apply_time_bucket(x: pd.Series, rule: str) -> pd.Series:
    parsed = pd.to_datetime(x, errors="coerce", format="mixed")
    if rule == "month":
        return parsed.dt.to_period("M").dt.start_time
    if rule == "week":
        return parsed.dt.to_period("W").dt.start_time
    return parsed.dt.floor("D")


def compute_aggregate(
    df: pd.DataFrame, x_col: str, y_col: str, x_type: str, y_type: str, agg_fn: str = "sum",
) -> AggregateResult:
    """x_type/y_type come from profiler.py's inferred_type — this function
    doesn't re-detect them, it trusts the caller already validated the pair
    (see suitability.py). Never mutates df."""
    if agg_fn not in VALID_AGG_FNS:
        raise ValueError(f"unknown agg_fn {agg_fn!r}, must be one of {VALID_AGG_FNS}")
    if y_type != "numeric_quantity":
        raise ValueError(f"y_col {y_col!r} must be numeric to aggregate (got {y_type!r})")

    work = df[[x_col, y_col]].copy()
    work[y_col] = pd.to_numeric(work[y_col], errors="coerce")

    if x_type == "temporal":
        bucket_rule = _time_bucket_rule(work[x_col])
        work["_x_bucket"] = _apply_time_bucket(work[x_col], bucket_rule)
        work = work.dropna(subset=["_x_bucket", y_col])
        grouped = work.groupby("_x_bucket")[y_col]
        agg = getattr(grouped, agg_fn)()
        points = [AggregatePoint(x=idx.isoformat(), y=float(val), n=int(grouped.size()[idx]))
                  for idx, val in agg.sort_index().items()]
        return AggregateResult(x_col=x_col, y_col=y_col, agg_fn=agg_fn, bucket_rule=f"time:{bucket_rule}", points=points)

    if x_type in ("categorical", "numeric_identifier"):
        work = work.dropna(subset=[x_col, y_col])
        grouped = work.groupby(x_col)[y_col]
        agg = getattr(grouped, agg_fn)()
        agg = agg.sort_values(ascending=False)
        points = [AggregatePoint(x=str(idx), y=float(val), n=int(grouped.size()[idx])) for idx, val in agg.items()]
        return AggregateResult(x_col=x_col, y_col=y_col, agg_fn=agg_fn, bucket_rule="group_by_category", points=points)

    if x_type == "numeric_quantity":
        work = work.dropna(subset=[x_col, y_col])
        points = [AggregatePoint(x=float(row[x_col]), y=float(row[y_col]), n=1) for _, row in work.iterrows()]
        return AggregateResult(x_col=x_col, y_col=y_col, agg_fn="none", bucket_rule="raw_pairs", points=points)

    raise ValueError(f"x_col {x_col!r} has unsupported type {x_type!r} for aggregation")
