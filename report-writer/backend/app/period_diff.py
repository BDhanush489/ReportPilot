"""
Track B1 — period-over-period diff: current vs prior period, turned into
"what changed and why." Pure functions over two already-computed metrics
dicts (same shape as ReportObject.metrics's per-source sections) — no
re-querying, no recompute; deltas are arithmetic on numbers both periods
already carry.

"Why" narrative stays inside this module's own scope deliberately: every
sentence describe_delta()/describe_dimension_change() produces is built
only from the diff numbers themselves (field label + current/prior/delta),
never from anything outside the diff — so a future LLM-authored narrative
that cites these strings is structurally unable to cite a number the diff
didn't itself compute.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .metrics import _pct_change

#: Sources that carry a "totals" sub-dict worth diffing period-over-period.
#: seo_metrics has no equivalent "totals" shape (its top-level fields ARE
#: the totals), so it's left out here rather than guessed at. Single source
#: of truth for both scheduler.py's automatic current-vs-prior attachment
#: (B1 -> B2) and W2's on-demand report-to-report diff.
DIFFABLE_SOURCES = ("analytics", "sales")


@dataclass
class MetricDelta:
    field: str
    current: float
    prior: float
    abs_delta: float
    pct_delta: float | None  # None when prior == 0 -- undefined, not zero

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DimensionChange:
    key: str
    #: "continuing" (present both periods) | "new" (current only) | "dropped" (prior only)
    status: str
    deltas: dict[str, MetricDelta] = field(default_factory=dict)
    current_values: dict[str, float] | None = None
    prior_values: dict[str, float] | None = None

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "status": self.status,
            "deltas": {k: v.to_dict() for k, v in self.deltas.items()},
            "current_values": self.current_values,
            "prior_values": self.prior_values,
        }


def diff_totals(current: dict, prior: dict, fields: list[str] | None = None) -> dict[str, MetricDelta]:
    """current/prior: flat dicts of numeric KPIs (e.g. metrics.analytics.totals).
    fields: which keys to diff -- defaults to every numeric key present in
    BOTH dicts, so a field only one side has is silently skipped rather than
    guessed at (no dimension mismatch masquerading as a KPI change)."""
    keys = fields if fields is not None else [
        k for k in current if k in prior and _is_number(current.get(k)) and _is_number(prior.get(k))
    ]
    result = {}
    for k in keys:
        if k not in current or k not in prior:
            continue
        cur_v, pri_v = current[k], prior[k]
        if not (_is_number(cur_v) and _is_number(pri_v)):
            continue
        result[k] = MetricDelta(
            field=k, current=cur_v, prior=pri_v,
            abs_delta=round(cur_v - pri_v, 6),
            pct_delta=_pct_change(cur_v, pri_v),
        )
    return result


def diff_dimension(current_records: list[dict], prior_records: list[dict], key_field: str,
                    value_fields: list[str]) -> list[DimensionChange]:
    """current_records/prior_records: lists of dicts sharing key_field (e.g.
    by_channel's "channel"). Returns one DimensionChange per key seen in
    EITHER period, deterministically ordered: continuing keys (current's
    order) first, then new, then dropped."""
    current_by_key = {r[key_field]: r for r in current_records if key_field in r}
    prior_by_key = {r[key_field]: r for r in prior_records if key_field in r}

    changes: list[DimensionChange] = []
    for key, cur in current_by_key.items():
        if key in prior_by_key:
            pri = prior_by_key[key]
            deltas = diff_totals(cur, pri, fields=value_fields)
            changes.append(DimensionChange(key=key, status="continuing", deltas=deltas))
        else:
            changes.append(DimensionChange(
                key=key, status="new",
                current_values={f: cur[f] for f in value_fields if f in cur},
            ))
    for key, pri in prior_by_key.items():
        if key not in current_by_key:
            changes.append(DimensionChange(
                key=key, status="dropped",
                prior_values={f: pri[f] for f in value_fields if f in pri},
            ))
    return changes


def describe_delta(label: str, delta: MetricDelta) -> str:
    """A plain, factual sentence built only from this delta's own numbers —
    the "why" narrative's raw material. Deterministic; never guesses a
    reason, only states the change."""
    direction = "grew" if delta.abs_delta > 0 else ("declined" if delta.abs_delta < 0 else "held steady")
    if delta.pct_delta is None:
        return f"{label} {direction} from {delta.prior:g} to {delta.current:g}."
    return f"{label} {direction} {abs(delta.pct_delta):g}% ({delta.prior:g} → {delta.current:g})."


def describe_dimension_change(dimension_label: str, change: DimensionChange) -> str:
    if change.status == "new":
        return f"New {dimension_label}: {change.key} (not present in the prior period)."
    if change.status == "dropped":
        return f"{dimension_label} no longer present: {change.key} (was in the prior period, not in this one)."
    parts = [describe_delta(f"{change.key}", d) for d in change.deltas.values()]
    return " ".join(parts) if parts else f"{change.key}: no comparable metrics between periods."


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def diff_report_objects(current, prior) -> dict:
    """W2 / B1->B2: the single reusable core behind both scheduler.py's
    automatic current-vs-prior attachment and W2's on-demand "what changed
    between report A and report B" -- one implementation, two call sites,
    never two differs. current/prior are ReportObjects (duck-typed here,
    not imported, to keep this module's only real dependency on report
    shape, not report_object.py's class itself) -- needs .report_id,
    .period.label, .metrics."""
    comparison: dict = {
        "current_report_id": current.report_id,
        "prior_report_id": prior.report_id,
        "current_period_label": current.period.label,
        "prior_period_label": prior.period.label,
    }
    for source in DIFFABLE_SOURCES:
        current_totals = (current.metrics.get(source) or {}).get("totals")
        prior_totals = (prior.metrics.get(source) or {}).get("totals")
        if current_totals and prior_totals:
            deltas = diff_totals(current_totals, prior_totals)
            comparison[source] = {field_name: d.to_dict() for field_name, d in deltas.items()}
    return comparison
