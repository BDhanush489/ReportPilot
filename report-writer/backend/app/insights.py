"""
Second-order insights — the numbers a client would actually pay for.

Same trust rule as everywhere else in this app: every figure here is derived
with plain arithmetic from app/metrics.py's already-computed output. Nothing
here is written or estimated by an LLM. These run whether or not an AI
provider is configured, so they show up even in fully offline/demo mode.

Each insight is a self-contained card: {id, tag, title, headline, detail}.
"""
from __future__ import annotations

# Widely-cited average organic CTR by SERP position (Advanced Web Ranking /
# Backlinko-style benchmarks). Used only to size an opportunity, never
# reported as if it were the client's own measured data.
_CTR_BY_POSITION = {1: 0.28, 2: 0.15, 3: 0.11, 4: 0.08, 5: 0.07,
                    6: 0.05, 7: 0.04, 8: 0.03, 9: 0.03, 10: 0.02}


def _ctr(position: float) -> float:
    return _CTR_BY_POSITION.get(max(1, round(position)), 0.01)


def _health_score(analytics: dict | None, seo: dict | None, sales: dict | None) -> dict:
    score = 70.0
    reasons: list[str] = []

    if analytics:
        delta = analytics.get("sessions_change_pct")
        if delta is not None:
            bump = max(-15.0, min(15.0, delta))
            score += bump
            reasons.append(f"{'traffic growth' if bump >= 0 else 'traffic decline'} ({delta:+.1f}% sessions)")

    if seo:
        total = seo.get("total_urls_crawled") or 0
        critical = seo.get("severity_counts", {}).get("critical", 0)
        if total:
            ratio = critical / total
            penalty = min(20.0, ratio * 100)
            score -= penalty
            if penalty > 2:
                reasons.append(f"{critical} of {total} pages have critical technical issues")

    if sales:
        win_rate = sales.get("totals", {}).get("win_rate_pct")
        if win_rate is not None:
            bump = (win_rate - 50) * 0.3
            score += bump
            reasons.append(f"{win_rate:.1f}% deal win rate")

    score = max(0.0, min(100.0, round(score)))
    grade = "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"
    return {
        "id": "health_score",
        "tag": "score",
        "title": "Account Health Score",
        "headline": f"{grade}",
        "sub": f"{int(score)} / 100",
        "detail": "Driven by " + "; ".join(reasons) + "." if reasons else "Not enough data to break down the score.",
    }


def _seo_opportunity(seo: dict | None, analytics: dict | None) -> dict | None:
    if not seo:
        return None
    pages = seo.get("opportunity_pages") or []
    if not pages:
        return None

    additional_clicks = 0.0
    for p in pages:
        impressions = p.get("impressions_28d", 0) or 0
        current_clicks = p.get("clicks_28d", 0) or 0
        modeled_clicks = impressions * _ctr(3)  # target: reach position 3
        additional_clicks += max(0.0, modeled_clicks - current_clicks)
    additional_clicks = round(additional_clicks)
    if additional_clicks <= 0:
        return None

    revenue_note = ""
    headline = f"+{additional_clicks:,.0f} clicks/mo"
    if analytics:
        organic = next((c for c in analytics.get("by_channel", []) if c["channel"] == "Organic Search"), None)
        if organic and organic.get("sessions"):
            revenue_per_session = organic["revenue_usd"] / organic["sessions"]
            monthly_value = round(additional_clicks * revenue_per_session)
            if monthly_value > 0:
                headline = f"${monthly_value:,.0f}/mo"
                revenue_note = f", worth an estimated ${monthly_value:,.0f}/month at this site's current organic revenue-per-session"

    return {
        "id": "seo_opportunity",
        "tag": "opportunity",
        "title": "SEO Opportunity",
        "headline": headline,
        "sub": f"{len(pages)} pages already earning impressions below position 8",
        "detail": (
            f"{len(pages)} pages get meaningful search impressions but rank below position 8. Based on typical "
            f"click-through rates by position, moving them to around position 3 could add roughly "
            f"{additional_clicks:,.0f} organic clicks/month{revenue_note}."
        ),
    }


def _device_gap(analytics: dict | None) -> dict | None:
    if not analytics:
        return None
    by_device = {d["device_category"]: d for d in analytics.get("by_device", [])}
    desktop, mobile = by_device.get("desktop"), by_device.get("mobile")
    if not desktop or not mobile or not desktop.get("sessions") or not mobile.get("sessions"):
        return None

    cvr_desktop = desktop["conversions"] / desktop["sessions"] * 100
    cvr_mobile = mobile["conversions"] / mobile["sessions"] * 100
    if cvr_desktop <= 0:
        return None
    relative_gap = (cvr_desktop - cvr_mobile) / cvr_desktop * 100
    if relative_gap < 20:
        return None  # not a meaningful gap

    totals = analytics.get("totals", {})
    aov = totals["revenue_usd"] / totals["conversions"] if totals.get("conversions") else 0
    additional_conversions = mobile["sessions"] * (cvr_desktop - cvr_mobile) / 100
    additional_revenue = round(additional_conversions * aov)
    if additional_revenue <= 0:
        return None

    return {
        "id": "device_gap",
        "tag": "risk",
        "title": "Mobile Experience Gap",
        "headline": f"${additional_revenue:,.0f} left on the table",
        "sub": f"Mobile converts at {cvr_mobile:.1f}% vs. {cvr_desktop:.1f}% on desktop",
        "detail": (
            f"Mobile sessions convert at {cvr_mobile:.1f}% versus {cvr_desktop:.1f}% on desktop — a "
            f"{relative_gap:.0f}% relative gap. Closing it would be worth an estimated ${additional_revenue:,.0f} "
            f"in additional revenue at current mobile traffic and average order value."
        ),
    }


def _lead_source_efficiency(sales: dict | None) -> dict | None:
    if not sales:
        return None
    sources = [s for s in (sales.get("by_lead_source") or []) if s.get("deals_won")]
    if len(sources) < 2:
        return None
    for s in sources:
        s["_avg_deal"] = s["revenue_usd"] / s["deals_won"]
    sources.sort(key=lambda s: s["_avg_deal"], reverse=True)
    best, worst = sources[0], sources[-1]
    if worst["_avg_deal"] <= 0 or best["lead_source"] == worst["lead_source"]:
        return None
    ratio = best["_avg_deal"] / worst["_avg_deal"]
    if ratio < 1.3:
        return None  # not a meaningful spread

    return {
        "id": "lead_source_efficiency",
        "tag": "efficiency",
        "title": "Lead Source Efficiency",
        "headline": f"{ratio:.1f}x bigger deals",
        "sub": f"{best['lead_source']} vs. {worst['lead_source']}",
        "detail": (
            f"Deals from {best['lead_source']} average ${best['_avg_deal']:,.0f}, versus "
            f"${worst['_avg_deal']:,.0f} from {worst['lead_source']} — {ratio:.1f}x bigger. Shifting acquisition "
            f"budget toward {best['lead_source']} is the highest-leverage lever available in this data."
        ),
    }


def compute_insights(metrics: dict) -> list[dict]:
    analytics = metrics.get("analytics")
    seo = metrics.get("seo")
    sales = metrics.get("sales")

    cards = [
        _health_score(analytics, seo, sales),
        _seo_opportunity(seo, analytics),
        _device_gap(analytics),
        _lead_source_efficiency(sales),
    ]
    return [c for c in cards if c]
