"""
Track B4 — KPI alerts: flag a drop when it happens, not on report day.

Reuses B1's period_diff.py deltas verbatim -- an alert rule is a threshold
checked against a MetricDelta that obj.period_comparison already carries
(attached by scheduler._attach_period_comparison), never a second recompute.
"Deterministic and traceable" falls out of reusing F0/B1's own numbers
rather than needing its own guarantee: an alert's current/prior/pct_delta
are the exact same floats period_diff.py already computed, not re-derived.

Delivery reuses delivery.py's channel implementations and logging verbatim
too (see deliver_alerts) -- an alert notification is a different body/
subject through the same send mechanism, not a second delivery system.

Rule config and the fired-dedup ledger are backed by the `alert_configs`/
`alert_fired_ledger` tables (see app/store_models.py) -- two separate
tables for the same reason they were two separate files: independent
lifecycles. Each function opens and commits its own short-lived DB session
internally rather than taking a `db: Session` parameter (see scheduler.py's
module docstring for the fuller rationale -- some callers have no FastAPI
request to draw a `Depends(get_db)` session from).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import delivery
from . import db as db_mod
from .report_object import ReportObject
from .store_models import AlertConfigRecord, AlertFiredLedger

_DIRECTIONS = ("pct_drop", "pct_rise")

#: Below this absolute PRIOR-period value, a percent swing is noise, not a
#: signal -- 2 sessions dropping to 1 is technically "-50%" but isn't a real
#: drop worth alerting anyone over. One step past period_diff's own
#: None-pct_delta-when-prior-is-zero guard, same spirit: small-N suppressed,
#: not shouted.
SMALL_N_FLOOR = 10.0


@dataclass
class AlertRule:
    id: str
    #: "source.field" into obj.period_comparison, e.g. "analytics.revenue_usd"
    #: (matches _DIFFABLE_SOURCES / diff_totals's field keys in scheduler.py).
    metric_path: str
    direction: str  # "pct_drop" | "pct_rise"
    threshold_pct: float  # positive; direction already encodes the sign
    label: str = ""

    def __post_init__(self):
        if self.direction not in _DIRECTIONS:
            raise ValueError(f"direction must be one of {_DIRECTIONS}, got {self.direction!r}")
        if self.threshold_pct <= 0:
            raise ValueError("threshold_pct must be positive (direction already encodes sign)")


@dataclass
class AlertConfig:
    tenant_id: str
    client_id: str
    rules: list[AlertRule] = field(default_factory=list)


@dataclass
class Alert:
    rule_id: str
    label: str
    metric_path: str
    #: Lifted verbatim from the SAME MetricDelta obj.period_comparison
    #: already carries -- never re-derived. Traceable to F0/B1 by
    #: construction: this IS the number, not a copy an LLM transcribed.
    current: float
    prior: float
    pct_delta: float
    as_of: str

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Per-client rule persistence
# ---------------------------------------------------------------------------

def save_alert_config(config: AlertConfig) -> None:
    rules = [asdict(r) for r in config.rules]
    with db_mod.SessionLocal() as session:
        row = AlertConfigRecord(tenant_id=config.tenant_id, client_id=config.client_id, rules=rules)
        session.merge(row)
        session.commit()


def load_alert_config(tenant_id: str, client_id: str) -> AlertConfig | None:
    with db_mod.SessionLocal() as session:
        row = session.query(AlertConfigRecord).filter_by(tenant_id=tenant_id, client_id=client_id).one_or_none()
        if row is None:
            return None
        return AlertConfig(tenant_id=row.tenant_id, client_id=row.client_id, rules=[AlertRule(**r) for r in row.rules])


# ---------------------------------------------------------------------------
# Breach detection
# ---------------------------------------------------------------------------

def _resolve_delta(obj: ReportObject, metric_path: str) -> dict | None:
    source, _, field_name = metric_path.partition(".")
    section = (obj.period_comparison or {}).get(source)
    if not section:
        return None
    return section.get(field_name)


def evaluate_rules(obj: ReportObject, rules: list[AlertRule], as_of: str) -> list[Alert]:
    """Pure function over already-computed numbers: never mutates obj,
    never re-queries a source. A rule with no prior period (first-ever
    report) or a zero prior (pct_delta undefined) simply can't breach --
    that's period_diff.py's own None-when-undefined guard, respected here
    rather than worked around."""
    triggered: list[Alert] = []
    for rule in rules:
        delta = _resolve_delta(obj, rule.metric_path)
        if delta is None or delta.get("pct_delta") is None:
            continue
        if abs(delta["prior"]) < SMALL_N_FLOOR:
            continue  # small-N suppressed, not shouted
        pct = delta["pct_delta"]
        breached = (
            (rule.direction == "pct_drop" and pct <= -rule.threshold_pct)
            or (rule.direction == "pct_rise" and pct >= rule.threshold_pct)
        )
        if breached:
            triggered.append(Alert(
                rule_id=rule.id, label=rule.label or rule.metric_path, metric_path=rule.metric_path,
                current=delta["current"], prior=delta["prior"], pct_delta=pct, as_of=as_of,
            ))
    return triggered


# ---------------------------------------------------------------------------
# Rate-limiting / dedup: an already-fired rule for this exact as_of never
# re-fires, so re-running a period (idempotent B2 re-run, or a manual
# re-check) never produces an alert storm.
# ---------------------------------------------------------------------------

def _load_fired(tenant_id: str, client_id: str) -> dict[str, list[str]]:
    with db_mod.SessionLocal() as session:
        row = session.query(AlertFiredLedger).filter_by(tenant_id=tenant_id, client_id=client_id).one_or_none()
        return row.fired if row else {}


def _save_fired(tenant_id: str, client_id: str, fired: dict[str, list[str]]) -> None:
    with db_mod.SessionLocal() as session:
        row = AlertFiredLedger(tenant_id=tenant_id, client_id=client_id, fired=fired)
        session.merge(row)
        session.commit()


def check_alerts(obj: ReportObject, tenant_id: str, client_id: str, as_of: str) -> list[Alert]:
    """The real entry point (called from scheduler.run_schedule): evaluate
    this client's saved rules against obj, gate on the SAME QA badge B3
    gates delivery on (an alert about a FAIL-badge report is exactly the
    kind of untrustworthy number this product exists to never surface), and
    dedup against what's already fired for this exact as_of."""
    if (obj.qa or {}).get("badge") == "FAIL":
        return []
    config = load_alert_config(tenant_id, client_id)
    if not config or not config.rules:
        return []
    triggered = evaluate_rules(obj, config.rules, as_of)

    fired = _load_fired(tenant_id, client_id)
    already = set(fired.get(as_of, []))
    new_alerts = [a for a in triggered if a.rule_id not in already]

    if new_alerts:
        fired.setdefault(as_of, [])
        fired[as_of].extend(a.rule_id for a in new_alerts)
        _save_fired(tenant_id, client_id, fired)

    return new_alerts


# ---------------------------------------------------------------------------
# Delivery -- reuses delivery.py's channels/logging, not a parallel system
# ---------------------------------------------------------------------------

def _alerts_html(alerts: list[Alert]) -> str:
    rows = "".join(
        f"<li>{a.label}: {a.current:g} (was {a.prior:g}, {a.pct_delta:+.1f}%)</li>" for a in alerts
    )
    return f"<html><body><h2>KPI Alerts</h2><ul>{rows}</ul></body></html>"


def deliver_alerts(alerts: list[Alert], recipients: list[str], tenant_id: str, client_id: str,
                    channel: str = "email", channel_impl=None) -> delivery.DeliveryAttempt:
    if not alerts:
        raise ValueError("no alerts to deliver")
    if channel not in delivery.CHANNELS:
        raise ValueError(f"Unknown channel {channel!r}. Choose one of: {sorted(delivery.CHANNELS)}")
    channel_impl = channel_impl or delivery.CHANNELS[channel]()

    subject = f"[ALERT] {len(alerts)} KPI breach(es) for {client_id}"
    body_html = _alerts_html(alerts)
    send_result = channel_impl.send(to=recipients, subject=subject, body_html=body_html, attachments=[])

    attempt = delivery.DeliveryAttempt(
        report_id=f"alerts-{client_id}-{alerts[0].as_of}", channel=channel, recipients=recipients,
        status=send_result.status, reason=send_result.reason,
    )
    delivery._log_attempt(tenant_id, attempt)
    return attempt
