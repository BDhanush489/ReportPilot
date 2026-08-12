"""
Self-contained interactive HTML dashboard — the third deliverable alongside
the PDF and the Power BI dashboard.

F0: build_dashboard() takes the canonical ReportObject and nothing else —
the same object render_pdf_from_object() renders the PDF from — so "same
numbers as the PDF" is structural, not a convention two separately-threaded
dicts have to be kept in sync by hand. Visual language (colors, fonts,
number formats) comes from theme.py, the same module charts.py and
report.html use. The QA badge computed once in report_builder.py is
surfaced here too, not just in the PDF — the dashboard is a renderer like
any other under the mission's invariant that every rendered number passes
the QA badge.

Drill-down deliberately stays inside metrics' own pre-computed granularity
levels (totals -> by_channel, by_device, by_rep, ...) rather than embedding
raw source rows: those levels are already present, already small regardless
of how large the underlying source table was, and are the same breakdowns
the PDF and Power BI show — there's no drill-down target here that isn't
already one of these fields.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import theme
from .insights import compute_insights
from .report_object import ReportObject

_env = Environment(
    loader=FileSystemLoader(searchpath=str(Path(__file__).parent / "templates")),
    autoescape=select_autoescape(["html"]),
)


def _kpi_cards(metrics_payload: dict) -> list[dict]:
    cards = []

    a = metrics_payload.get("analytics")
    if a:
        totals = a.get("totals", {})
        cards.append({"id": "web_sessions", "label": "Web Sessions",
                      "value": totals.get("sessions", 0), "formatted": theme.format_count(totals.get("sessions", 0)),
                      "drill": "analytics_by_channel"})
        cards.append({"id": "web_revenue", "label": "Web Revenue",
                      "value": totals.get("revenue_usd", 0), "formatted": theme.format_currency(totals.get("revenue_usd", 0)),
                      "drill": "analytics_by_channel"})
        cards.append({"id": "conversion_rate", "label": "Conversion Rate",
                      "value": totals.get("conversion_rate", 0), "formatted": theme.format_percent(totals.get("conversion_rate", 0)),
                      "drill": "analytics_by_device"})

    s = metrics_payload.get("seo")
    if s:
        cards.append({"id": "pages_crawled", "label": "Pages Crawled",
                      "value": s.get("total_urls_crawled", 0), "formatted": theme.format_count(s.get("total_urls_crawled", 0)),
                      "drill": "seo_top_issues"})
        cards.append({"id": "indexable_pct", "label": "Indexable Pages",
                      "value": s.get("indexable_pct", 0), "formatted": theme.format_percent(s.get("indexable_pct", 0)),
                      "drill": None})

    sl = metrics_payload.get("sales")
    if sl:
        totals = sl.get("totals", {})
        cards.append({"id": "sales_revenue", "label": "Closed-Won Revenue",
                      "value": totals.get("revenue_usd", 0), "formatted": theme.format_currency(totals.get("revenue_usd", 0)),
                      "drill": "sales_by_rep"})
        cards.append({"id": "win_rate", "label": "Win Rate",
                      "value": totals.get("win_rate_pct", 0), "formatted": theme.format_percent(totals.get("win_rate_pct", 0)),
                      "drill": "sales_by_lead_source"})

    return cards


def _drill_tables(metrics_payload: dict) -> dict:
    """Named drill-down datasets a KPI card can reveal, keyed to match each
    card's "drill" field above. Every value here is a field metrics.py
    already computed and metrics.json already persists — nothing new."""
    tables: dict = {}

    a = metrics_payload.get("analytics")
    if a:
        if a.get("by_channel"):
            tables["analytics_by_channel"] = a["by_channel"]
        if a.get("by_device"):
            tables["analytics_by_device"] = a["by_device"]

    s = metrics_payload.get("seo")
    if s and s.get("top_issues"):
        tables["seo_top_issues"] = [{"issue": k, "pages_affected": v} for k, v in s["top_issues"]]

    sl = metrics_payload.get("sales")
    if sl:
        if sl.get("by_rep"):
            tables["sales_by_rep"] = sl["by_rep"]
        if sl.get("by_lead_source"):
            tables["sales_by_lead_source"] = sl["by_lead_source"]

    return tables


def _chart_highlights(obj: ReportObject) -> list[dict]:
    """A2: the same on-chart annotations report.html shows, surfaced here
    too -- same source (obj.charts), same text, so "notable point" means
    the same thing in both renderers. The dashboard doesn't embed chart
    images (a deliberate Lever-4 design choice — KPI cards + drill-down,
    not duplicated PNGs), so this is a compact text list rather than an
    image overlay, but it's the identical annotation data and text."""
    return [
        {"caption": c.caption, "section": c.section, "annotation": c.annotation}
        for c in obj.charts if c.annotation
    ]


def _format_metric_value(field_name: str, value) -> str:
    if "usd" in field_name:
        return theme.format_currency(value)
    if "pct" in field_name or "rate" in field_name:
        return theme.format_percent(value)
    return theme.format_count(value)


def _period_comparison_rows(obj: ReportObject) -> dict | None:
    """B1 -> B2: current-vs-prior-report deltas, only present when this
    report came from a schedule with an earlier run to diff against (see
    scheduler.py's _attach_period_comparison) -- None for a one-off upload
    or a schedule's first-ever run, not an error state."""
    comparison = obj.period_comparison
    if not comparison:
        return None
    rows = []
    for source in ("analytics", "sales"):
        for field_name, delta in (comparison.get(source) or {}).items():
            abs_delta = delta["abs_delta"]
            rows.append({
                "source": source,
                "field": field_name,
                "current": _format_metric_value(field_name, delta["current"]),
                "prior": _format_metric_value(field_name, delta["prior"]),
                "pct_delta": delta["pct_delta"],
                "direction": "up" if abs_delta > 0 else ("down" if abs_delta < 0 else "flat"),
            })
    return {"prior_report_id": comparison.get("prior_report_id"),
            "prior_period_label": comparison.get("prior_period_label"), "rows": rows}


def build_dashboard(obj: ReportObject) -> str:
    """obj is the only input — no re-querying, no recompute, no second copy
    of the numbers threaded in separately from branding/QA."""
    metrics_payload = obj.metrics or {}
    branding = obj.branding or {}
    qa = obj.qa or {}

    kpi_cards = _kpi_cards(metrics_payload)
    drill_tables = _drill_tables(metrics_payload)
    channels = sorted({c["channel"] for c in (metrics_payload.get("analytics", {}) or {}).get("by_channel", []) or []})
    chart_highlights = _chart_highlights(obj)
    period_comparison = _period_comparison_rows(obj)

    # compute_insights (see insights.py::_lead_source_efficiency) mutates the
    # dicts it's handed in place (adds "_avg_deal") — report_builder.py only
    # avoids this by deep-copying before calling it. drill_tables above holds
    # references, not copies, into metrics_payload, so without the same
    # deep-copy here that mutation silently leaks a stray column into the
    # dashboard's drill-down table (caught live: an unformatted "avg deal"
    # column appearing under the Win Rate KPI's drill-down).
    payload = {
        "metrics": metrics_payload,
        "kpi_cards": kpi_cards,
        "drill_tables": drill_tables,
        "insights": compute_insights(copy.deepcopy(metrics_payload)),
        "channels": channels,
        "qa": qa,
        "chart_highlights": chart_highlights,
        "period_comparison": period_comparison,
    }

    return _env.get_template("dashboard.html").render(
        branding=branding,
        theme=theme.to_template_context(branding.get("font_family")),
        period_label=metrics_payload.get("period_label", ""),
        kpi_cards=kpi_cards,
        channels=channels,
        qa=qa,
        chart_highlights=chart_highlights,
        period_comparison=period_comparison,
        payload_json=json.dumps(payload, default=str),
    )
