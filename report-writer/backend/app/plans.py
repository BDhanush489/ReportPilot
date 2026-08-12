"""
Plan/billing tiers — declarative data, not scattered if/else branches,
same "config as data" convention template_specs.py established for report
templates. No real billing/Stripe integration exists yet (still explicitly
out of scope, see the E1 CHANGELOG entry) -- a tenant's plan is set directly
by a platform admin (POST /api/admin/tenants/{id}/plan), not by a checkout
flow. What IS real: the limits below are actually enforced (see main.py's
POST /api/schedules and the PBIP export route), not just marketing copy.

"active client" (the quantity Solo/Agency actually cap) is defined here as:
a distinct client_id with a saved Schedule for this tenant -- a schedule is
the literal ongoing/recurring relationship the pricing copy means by
"active"; a one-off report generated from an uploaded file is not "active"
in that sense and never counts against the cap. See
scheduler.count_active_clients_for_tenant.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Plan:
    id: str
    label: str
    tagline: str
    price_display: str  # "$39/mo" | "Custom" -- display only, never parsed
    cta_label: str
    #: None = uncapped (the "In-house" / custom-volume tier).
    max_active_clients: int | None
    can_schedule: bool  # "Recurring reports on a schedule"
    can_export_pbip: bool  # "PDF + dashboard + Power BI export"
    highlighted: bool = False  # "Most popular" badge
    features: list[str] = field(default_factory=list)


PLANS: dict[str, Plan] = {
    "solo": Plan(
        id="solo",
        label="Solo",
        tagline="For individual & fractional consultants",
        price_display="$39/mo",
        cta_label="Start free",
        max_active_clients=5,
        can_schedule=False,
        can_export_pbip=False,
        features=[
            "Up to 5 active clients",
            "PDF report + HTML dashboard",
            "CSV / Excel upload",
            "Full QA badge on every report",
            "Email support",
        ],
    ),
    "agency": Plan(
        id="agency",
        label="Agency",
        tagline="For boutique agencies running recurring reports",
        price_display="$149/mo",
        cta_label="Start free",
        max_active_clients=50,
        can_schedule=True,
        can_export_pbip=True,
        highlighted=True,
        features=[
            "Up to 50 active clients",
            "PDF + dashboard + Power BI export",
            "Recurring reports on a schedule",
            "Per-client branding",
            "Priority support",
        ],
    ),
    "inhouse": Plan(
        id="inhouse",
        label="In-house",
        tagline="For growth teams connecting a live warehouse",
        price_display="Custom",
        cta_label="Talk to us",
        max_active_clients=None,
        can_schedule=True,
        can_export_pbip=True,
        features=[
            "Live SQL warehouse connection",
            "SSO & role-based access",
            "Custom branding & domains",
            "Dedicated support",
            "Volume-based pricing",
        ],
    ),
}

DEFAULT_PLAN_ID = "solo"


def get_plan(plan_id: str) -> Plan:
    """Falls back to the default plan for an unrecognized/stale id (e.g. a
    plan retired after a tenant was assigned it) rather than raising --
    a tenant's own plan field is trusted input from admin.py, not user
    input, but this stays defensive since it gates real feature access."""
    return PLANS.get(plan_id, PLANS[DEFAULT_PLAN_ID])
