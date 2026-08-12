"""
Track A2 — on-chart annotation: mark the single most notable point on a
chart, deterministically. Every number this module puts in an annotation is
lifted verbatim from the chart's own resolved series (chart_intelligence.
extract_xy — the same values A1's suitability check already validated),
never computed or guessed independently, so it traces to a real value by
construction.

Priority when a chart has more than one candidate notable point: outlier >
largest_delta > peak. An outlier is the most surprising thing to flag if
one genuinely exists; a largest single-step swing is the next most legible
story, but only when x is actually ordered (temporal) -- "the biggest jump
between adjacent rows" is meaningless for a categorical axis like
lead_source, where row order is arbitrary, not a sequence. A plain peak is
the safe fallback for everything else. Exactly one annotation per chart —
more would compete for the same caption space and dilute "the" notable
point into noise. "Nothing notable" (too little data, or a perfectly flat
series where every point ties for the max) returns None rather than
forcing a label onto data that doesn't support one.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

import pandas as pd

from .chart_intelligence import extract_xy, infer_field_type
from .viz.outliers import detect_outliers_iqr

#: Series promoted through pandas' to_json(date_format="iso") (see
#: report_builder._json_safe_records) render a midnight timestamp as
#: "2026-01-19T00:00:00.000" -- correct, but not something a consultant
#: wants to read in an annotation. Trimming this is display formatting on
#: an already-correct string, the same category as theme.py's
#: format_currency/format_percent -- never a value change.
_MIDNIGHT_ISO_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T00:00:00(\.000)?Z?$")


def _format_x_label(x_label) -> str:
    text = str(x_label)
    match = _MIDNIGHT_ISO_RE.match(text)
    return match.group(1) if match else text


@dataclass
class ChartAnnotation:
    kind: str  # "outlier" | "largest_delta" | "peak"
    x_label: str
    y_value: float
    text: str
    #: A3 — "up" | "down" for a largest_delta annotation, None otherwise
    #: (an outlier or a peak has no inherent direction to reconcile a
    #: narrative claim's sign against). Structured so app/narrative_links.py
    #: can check a cited claim's direction without re-parsing `text`.
    direction: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _most_extreme_outlier(df: pd.DataFrame, report) -> int:
    """When IQR flags more than one point, "the" notable one is whichever
    sits furthest outside the fence -- not whichever happens to come first
    in row order, which is an accident of input ordering, not a signal."""
    def distance_beyond_fence(idx: int) -> float:
        y = df.loc[idx, "y"]
        return max(report.lower_bound - y, y - report.upper_bound)

    return max(report.outlier_row_indices, key=distance_beyond_fence)


def detect_notable_point(resolved, shape: str, x_field: str | None, y_field: str | None) -> ChartAnnotation | None:
    xy = extract_xy(resolved, shape, x_field, y_field)
    if xy is None:
        return None
    xs, ys = xy
    pairs = [(x, y) for x, y in zip(xs, ys) if _is_number(y)]
    if len(pairs) < 2:
        return None  # nothing to compare a single point against
    if len({y for _, y in pairs}) == 1:
        return None  # perfectly flat -- every point ties, nothing is "the" notable one

    # 1. Outlier -- reuses viz.outliers' IQR rule verbatim, not a second
    # outlier definition. Picks the most extreme when there's more than one.
    df = pd.DataFrame({"x": [p[0] for p in pairs], "y": [p[1] for p in pairs]})
    outlier_report = detect_outliers_iqr(df, "y")
    if outlier_report.count > 0:
        idx = _most_extreme_outlier(df, outlier_report)
        x_label, y_value = _format_x_label(df.loc[idx, "x"]), float(df.loc[idx, "y"])
        return ChartAnnotation(
            kind="outlier", x_label=x_label, y_value=y_value,
            text=f"{x_label}: {y_value:g} — an outlier relative to the rest of this series",
        )

    # 2. Largest adjacent step -- only meaningful when x is genuinely
    # ordered (temporal); for categorical x, "adjacent in the resolved
    # list" carries no sequence meaning, so skip straight to peak.
    if infer_field_type([p[0] for p in pairs]) == "temporal":
        deltas = [(abs(pairs[i][1] - pairs[i - 1][1]), i) for i in range(1, len(pairs))]
        biggest_delta, i = max(deltas, key=lambda d: d[0])
        if biggest_delta > 0:
            x_label, y_value, prev_y = _format_x_label(pairs[i][0]), pairs[i][1], pairs[i - 1][1]
            direction = "up" if y_value > prev_y else "down"
            return ChartAnnotation(
                kind="largest_delta", x_label=x_label, y_value=float(y_value), direction=direction,
                text=f"{x_label}: {direction} from {prev_y:g} to {y_value:g}, the largest step in this series",
            )

    # 3. Peak -- the safe fallback: categorical x, or a temporal series
    # whose every adjacent step happened to be exactly 0 despite differing
    # overall (a plateaued step function).
    raw_x_label, y_value = max(pairs, key=lambda p: p[1])
    x_label = _format_x_label(raw_x_label)
    return ChartAnnotation(
        kind="peak", x_label=x_label, y_value=float(y_value),
        text=f"{x_label}: {y_value:g}, the highest point in this series",
    )
