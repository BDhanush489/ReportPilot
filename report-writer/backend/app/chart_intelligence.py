"""
Track A1 — auto chart-type: given the data actually feeding a chart (not a
schema declaration, the real resolved values for this report), decide
whether the chart type already chosen for it is a good fit, and why.

Reuses app/viz/suitability.py's deterministic (chart_type, x_type, y_type,
x_cardinality) -> verdict rules verbatim rather than re-deriving them —
those rules are already tested (tests/test_viz_suitability.py) and this
node's job is field-type inference + wiring, not re-litigating what "too
many pie slices" means. Field-type inference here is intentionally narrow
(numeric / temporal / categorical, no free-text/id distinction) because
every field this module is ever asked to classify comes from metrics.py's
fixed, known-shape output — never an arbitrary user-uploaded column — so
there's no schema-agnostic guessing to do, only "is this actually a number,
an ISO-ish date, or neither."
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .viz.suitability import evaluate_suitability

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?")


@dataclass
class ChartTypeChoice:
    chart_type: str
    #: "good" | "discouraged" | "ambiguous_data" -- the last is this node's
    #: own safe-fallback state, not one of suitability.py's two verdicts;
    #: it means there wasn't enough resolved data to evaluate at all.
    verdict: str
    reason: str
    alternatives: list[str] = field(default_factory=list)


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _looks_temporal(v) -> bool:
    return isinstance(v, str) and bool(_ISO_DATE_RE.match(v))


def infer_field_type(values: list) -> str:
    """numeric / temporal / categorical -- see module docstring for why this
    three-way split is enough for this pipeline's fixed metric shapes."""
    non_null = [v for v in values if v is not None]
    if not non_null:
        return "categorical"
    if all(_is_number(v) for v in non_null):
        return "numeric"
    if all(_looks_temporal(v) for v in non_null):
        return "temporal"
    return "categorical"


def extract_xy(resolved, shape: str, x_field: str | None, y_field: str | None) -> tuple[list, list] | None:
    """Shared by this module's suitability check and chart_annotation.py's
    notable-point detection -- one place that knows how to read (x, y) pairs
    out of the three resolved data shapes a chart can be built from."""
    if shape == "records":
        if not isinstance(resolved, list) or not resolved:
            return None
        xs = [r.get(x_field) for r in resolved if isinstance(r, dict)]
        ys = [r.get(y_field) for r in resolved if isinstance(r, dict)]
        if not xs or not ys or all(x is None for x in xs) or all(y is None for y in ys):
            return None
        return xs, ys
    if shape == "dict_counts":
        if not isinstance(resolved, dict) or not resolved:
            return None
        return list(resolved.keys()), list(resolved.values())
    if shape == "pairs":
        if not isinstance(resolved, list) or not resolved:
            return None
        try:
            return [p[0] for p in resolved], [p[1] for p in resolved]
        except (IndexError, TypeError, KeyError):
            return None
    return None


def choose_chart_type(resolved, shape: str, x_field: str | None, y_field: str | None,
                       requested_chart_type: str) -> ChartTypeChoice:
    """resolved: whatever ReportObject.resolve(metric_path) returned for this
    chart. Never raises on bad/missing/ambiguous data -- falls back to an
    explicit "ambiguous_data" verdict instead, per this node's exit
    criterion that ambiguous data degrades safely, not with a crash."""
    xy = extract_xy(resolved, shape, x_field, y_field)
    if xy is None:
        return ChartTypeChoice(
            chart_type=requested_chart_type, verdict="ambiguous_data",
            reason="not enough resolved data to evaluate chart-type suitability against",
        )
    xs, ys = xy
    x_type = infer_field_type(xs)
    y_type = infer_field_type(ys)
    x_cardinality = len({x for x in xs if x is not None})

    verdict = evaluate_suitability(requested_chart_type, x_type, y_type, x_cardinality)
    return ChartTypeChoice(
        chart_type=requested_chart_type, verdict=verdict.verdict,
        reason=verdict.reason, alternatives=verdict.alternatives,
    )
