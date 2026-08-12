"""
Rule-based chart suggestions from a profiled dataset — no LLM. Candidates
are generated purely from column names/types already present in the
DatasetProfile, so a suggestion can never reference a column that isn't
actually in the file. Each candidate is checked against suitability.py
before being offered, so a suggestion is never something the engine would
itself flag as a poor fit.

Ranking is deterministic: time-over-trend first (temporal x, numeric y —
generally the highest-value view of a dataset), then category breakdowns
(categorical x, numeric y), then numeric-vs-numeric relationships last.
Within a tier, candidates keep the profile's own column order — no
randomness, so the same file always yields the same suggestions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .profiler import DatasetProfile
from .suitability import evaluate_suitability

#: Categorical x columns above this cardinality make a noisy suggestion —
#: same ceiling suitability.py uses for a readable bar chart.
MAX_SUGGESTED_CATEGORICAL_CARDINALITY = 30


@dataclass
class ChartSuggestion:
    x_col: str
    y_col: str
    chart_type: str
    label: str
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def _numeric_columns(profile: DatasetProfile) -> list[str]:
    return [name for name, c in profile.columns.items() if c.inferred_type == "numeric_quantity"]


def _temporal_columns(profile: DatasetProfile) -> list[str]:
    return [name for name, c in profile.columns.items() if c.inferred_type == "temporal"]


def _categorical_columns(profile: DatasetProfile) -> list[str]:
    return [
        name for name, c in profile.columns.items()
        if c.inferred_type == "categorical" and not c.is_free_text
        and 1 < c.cardinality <= MAX_SUGGESTED_CATEGORICAL_CARDINALITY
    ]


def _try_add(candidates: list[ChartSuggestion], x_col: str, y_col: str, chart_type: str,
             label: str, reason: str, x_type: str, y_type: str, x_cardinality: int) -> None:
    verdict = evaluate_suitability(chart_type, x_type, y_type, x_cardinality)
    if verdict.verdict == "good":
        candidates.append(ChartSuggestion(x_col=x_col, y_col=y_col, chart_type=chart_type, label=label, reason=reason))


def suggest_charts(profile: DatasetProfile, n: int = 5) -> list[ChartSuggestion]:
    numeric_cols = _numeric_columns(profile)
    temporal_cols = _temporal_columns(profile)
    categorical_cols = _categorical_columns(profile)

    tier1: list[ChartSuggestion] = []  # trend over time
    for x_col in temporal_cols:
        for y_col in numeric_cols:
            _try_add(
                tier1, x_col, y_col, "line", f"{y_col} over time",
                f"{x_col} is a date/time field and {y_col} is numeric — trend over time is usually the most useful first view",
                profile.columns[x_col].inferred_type, profile.columns[y_col].inferred_type,
                profile.columns[x_col].cardinality,
            )

    tier2: list[ChartSuggestion] = []  # breakdown by category
    for x_col in categorical_cols:
        for y_col in numeric_cols:
            _try_add(
                tier2, x_col, y_col, "bar", f"{y_col} by {x_col}",
                f"{x_col} is a discrete grouping ({profile.columns[x_col].cardinality} categories) — a breakdown of {y_col} by group",
                profile.columns[x_col].inferred_type, profile.columns[y_col].inferred_type,
                profile.columns[x_col].cardinality,
            )

    tier3: list[ChartSuggestion] = []  # numeric relationship
    for i, x_col in enumerate(numeric_cols):
        for y_col in numeric_cols[i + 1:]:
            _try_add(
                tier3, x_col, y_col, "scatter", f"{y_col} vs {x_col}",
                f"both {x_col} and {y_col} are continuous — worth checking whether they move together",
                profile.columns[x_col].inferred_type, profile.columns[y_col].inferred_type,
                profile.columns[x_col].cardinality,
            )

    return (tier1 + tier2 + tier3)[:n]
