"""
Deterministic chart-type suitability rules: given what's actually being
plotted (x/y column types + x's cardinality), is the requested chart type a
good fit? If not, why not, and what would be better?

Every rule here is a plain, statable condition on (chart_type, x_type,
y_type, x_cardinality) — nothing probabilistic, nothing the LLM decides.
The LLM (not wired in yet — see html_dashboard/qa precedent of staying
deterministic-first) may eventually turn a verdict into friendlier prose,
but the verdict itself is computed here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

CHART_TYPES = ("line", "bar", "pie", "scatter")

#: Pie charts stop being readable well before this many slices — standard
#: dataviz guidance, not a number pulled from this dataset.
PIE_MAX_CARDINALITY = 8

#: A bar chart with more categories than this needs a top-N cut or a
#: different chart type to stay readable.
BAR_MAX_CARDINALITY = 30

#: This module is shared by two callers with two different, non-overlapping
#: type vocabularies: chart_intelligence.py's own narrow 3-way classifier
#: (numeric/temporal/categorical -- its own module docstring explains why it
#: deliberately doesn't distinguish ids/free-text) and app/viz/profiler.py's
#: richer schema-agnostic one (numeric_quantity/numeric_identifier/...).
#: Neither vocabulary ever produces the other's spelling, so accepting both
#: as synonyms here keeps this one set of rules genuinely shared instead of
#: forking it per caller.
_NUMERIC_MAGNITUDE_TYPES = ("numeric", "numeric_quantity")
_DISCRETE_LABEL_TYPES = ("categorical", "id", "numeric_identifier")


@dataclass
class SuitabilityVerdict:
    chart_type: str
    verdict: str  # "good" | "discouraged"
    reason: str
    alternatives: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _evaluate_one(chart_type: str, x_type: str, y_type: str, x_cardinality: int) -> tuple[str, str]:
    """Returns (verdict, reason) for one chart type, no alternatives yet."""
    if y_type not in _NUMERIC_MAGNITUDE_TYPES:
        return "discouraged", f"{chart_type} needs a numeric y-axis field; {y_type!r} can't be plotted as a magnitude"

    if chart_type == "line":
        if x_type == "temporal" or x_type in _NUMERIC_MAGNITUDE_TYPES:
            return "good", "x is ordered (temporal/numeric), which is what a line's continuity implies"
        return "discouraged", f"line implies an ordered x-axis; {x_type!r} has no inherent order"

    if chart_type == "bar":
        if x_type in _DISCRETE_LABEL_TYPES:
            if x_cardinality > BAR_MAX_CARDINALITY:
                return "discouraged", f"{x_cardinality} bars is too many to read — needs a top-N cut or a different chart"
            return "good", "x is a discrete grouping with a readable number of categories"
        if x_type == "temporal":
            if x_cardinality > BAR_MAX_CARDINALITY:
                return "discouraged", f"{x_cardinality} time buckets is too many bars — a line reads a trend better"
            return "good", "x is a small number of time buckets, readable as bars"
        return "discouraged", f"bar needs a discrete x-axis; {x_type!r} isn't discrete"

    if chart_type == "pie":
        if x_type == "temporal":
            return "discouraged", "pie charts can't show a sequence over time — order and trend both disappear"
        if x_type not in _DISCRETE_LABEL_TYPES:
            return "discouraged", f"pie needs a discrete x-axis; {x_type!r} isn't discrete"
        if x_cardinality > PIE_MAX_CARDINALITY:
            return "discouraged", f"{x_cardinality} slices is unreadable as a pie (readable limit ~{PIE_MAX_CARDINALITY})"
        return "good", "a small number of categories summing to a meaningful whole"

    if chart_type == "scatter":
        if x_type in _NUMERIC_MAGNITUDE_TYPES:
            return "good", "both axes are continuous — scatter is exactly for looking at their relationship"
        return "discouraged", f"scatter needs a continuous x-axis to be meaningful; {x_type!r} has no continuous position"

    raise ValueError(f"unknown chart_type {chart_type!r}")


def evaluate_suitability(chart_type: str, x_type: str, y_type: str, x_cardinality: int) -> SuitabilityVerdict:
    if chart_type not in CHART_TYPES:
        raise ValueError(f"unknown chart_type {chart_type!r}, must be one of {CHART_TYPES}")

    verdict, reason = _evaluate_one(chart_type, x_type, y_type, x_cardinality)
    alternatives: list[str] = []
    if verdict == "discouraged":
        for candidate in CHART_TYPES:
            if candidate == chart_type:
                continue
            candidate_verdict, _ = _evaluate_one(candidate, x_type, y_type, x_cardinality)
            if candidate_verdict == "good":
                alternatives.append(candidate)

    return SuitabilityVerdict(chart_type=chart_type, verdict=verdict, reason=reason, alternatives=alternatives)
