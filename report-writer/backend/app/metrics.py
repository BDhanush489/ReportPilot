"""
All numbers in the final report come from here, computed deterministically
with pandas. The LLM agent (app/agent.py) is only ever shown these already-
computed figures and writes narrative around them — it never calculates or
invents a metric itself. This is the load-bearing design choice that keeps
client-facing reports numerically trustworthy.
"""
from __future__ import annotations

import pandas as pd


def _pct_change(new: float, old: float) -> float | None:
    if old == 0:
        return None
    return round((new - old) / old * 100, 1)


def analytics_metrics(df: pd.DataFrame) -> dict:
    df = df.sort_values("date")
    start, end = df["date"].min(), df["date"].max()
    total_days = (end - start).days + 1
    half = total_days // 2
    midpoint = start + pd.Timedelta(days=half)
    recent = df[df["date"] >= midpoint]
    prior = df[df["date"] < midpoint]

    def totals(frame: pd.DataFrame) -> dict:
        return {
            "sessions": int(frame["sessions"].sum()),
            "new_users": int(frame["new_users"].sum()),
            "conversions": int(frame["conversions"].sum()),
            "revenue_usd": round(float(frame["revenue_usd"].sum()), 2),
        }

    totals_recent, totals_prior = totals(recent), totals(prior)
    overall = totals(df)
    overall["conversion_rate"] = round(overall["conversions"] / overall["sessions"] * 100, 2) if overall["sessions"] else 0

    by_channel = []
    for channel, grp in df.groupby("channel_group"):
        rec = totals(grp[grp["date"] >= midpoint])
        pri = totals(grp[grp["date"] < midpoint])
        sessions = int(grp["sessions"].sum())
        conversions = int(grp["conversions"].sum())
        by_channel.append({
            "channel": channel,
            "sessions": sessions,
            "conversions": conversions,
            "revenue_usd": round(float(grp["revenue_usd"].sum()), 2),
            "conversion_rate": round(conversions / sessions * 100, 2) if sessions else 0,
            "share_of_sessions_pct": round(sessions / overall["sessions"] * 100, 1) if overall["sessions"] else 0,
            "session_change_pct": _pct_change(rec["sessions"], pri["sessions"]),
            "revenue_change_pct": _pct_change(rec["revenue_usd"], pri["revenue_usd"]),
        })
    by_channel.sort(key=lambda r: r["revenue_usd"], reverse=True)

    by_device = (
        df.groupby("device_category")
        .agg(sessions=("sessions", "sum"), conversions=("conversions", "sum"), revenue_usd=("revenue_usd", "sum"))
        .reset_index()
        .to_dict("records")
    )

    weekly = (
        df.assign(week=df["date"].dt.to_period("W").apply(lambda p: p.start_time))
        .groupby(["week", "channel_group"], as_index=False)
        .agg(sessions=("sessions", "sum"), revenue_usd=("revenue_usd", "sum"), conversions=("conversions", "sum"))
    )
    weekly_totals = weekly.groupby("week", as_index=False).agg(
        sessions=("sessions", "sum"), revenue_usd=("revenue_usd", "sum"), conversions=("conversions", "sum")
    )

    top_channels_by_sessions = [c["channel"] for c in sorted(by_channel, key=lambda r: r["sessions"], reverse=True)[:5]]

    movers = sorted(
        [c for c in by_channel if c["session_change_pct"] is not None],
        key=lambda r: r["session_change_pct"], reverse=True,
    )

    return {
        "date_range": {"start": start.date().isoformat(), "end": end.date().isoformat(), "days": total_days},
        "totals": overall,
        "totals_recent_half": totals_recent,
        "totals_prior_half": totals_prior,
        "sessions_change_pct": _pct_change(totals_recent["sessions"], totals_prior["sessions"]),
        "revenue_change_pct": _pct_change(totals_recent["revenue_usd"], totals_prior["revenue_usd"]),
        "by_channel": by_channel,
        "by_device": by_device,
        "top_growing_channel": movers[0] if movers else None,
        "top_declining_channel": movers[-1] if movers else None,
        "_weekly": weekly,
        "_weekly_totals": weekly_totals,
        "_top_channels": top_channels_by_sessions,
    }


def seo_metrics(df: pd.DataFrame) -> dict:
    total_urls = len(df)
    severity_counts = df["issue_severity"].value_counts().to_dict()
    for key in ("good", "warning", "critical"):
        severity_counts.setdefault(key, 0)

    issue_series = df["issues"].str.split(";").explode()
    issue_series = issue_series[issue_series.str.len() > 0]
    issue_counts = issue_series.value_counts()

    indexable_pct = round(df["is_indexable"].mean() * 100, 1) if total_urls else 0
    avg_load_time = round(df["load_time_ms"].mean(), 0) if total_urls else 0

    total_impressions = int(df["impressions_28d"].sum())
    total_clicks = int(df["clicks_28d"].sum())
    overall_ctr = round(total_clicks / total_impressions * 100, 2) if total_impressions else 0
    avg_position = round(df.loc[df["impressions_28d"] > 0, "avg_position"].mean(), 1) if (df["impressions_28d"] > 0).any() else 0

    worst_pages = (
        df[df["issue_severity"].isin(["critical", "warning"])]
        .assign(_score=lambda d: d["impressions_28d"] + d["organic_sessions_28d"] * 5)
        .sort_values("_score", ascending=False)
        .head(8)[["url", "issue_severity", "issues", "impressions_28d", "organic_sessions_28d"]]
        .to_dict("records")
    )

    opportunity_pages = (
        df[(df["impressions_28d"] > df["impressions_28d"].quantile(0.75)) & (df["avg_position"] > 8)]
        .sort_values("impressions_28d", ascending=False)
        .head(8)[["url", "impressions_28d", "avg_position", "ctr", "clicks_28d"]]
        .to_dict("records")
    )

    return {
        "total_urls_crawled": total_urls,
        "indexable_pct": indexable_pct,
        "avg_load_time_ms": avg_load_time,
        "severity_counts": {k: int(v) for k, v in severity_counts.items()},
        "top_issues": [(k, int(v)) for k, v in issue_counts.head(8).items()],
        "search_performance": {
            "impressions_28d": total_impressions,
            "clicks_28d": total_clicks,
            "ctr_pct": overall_ctr,
            "avg_position": avg_position,
        },
        "worst_pages": worst_pages,
        "opportunity_pages": opportunity_pages,
    }


def sales_metrics(deals: pd.DataFrame, monthly: pd.DataFrame | None) -> dict:
    won = deals[deals["deal_stage"].str.contains("won", case=False, na=False)]
    lost = deals[deals["deal_stage"].str.contains("lost", case=False, na=False)]
    total_revenue = round(float(won["amount_usd"].sum()), 2)
    deals_won, deals_lost = len(won), len(lost)
    win_rate = round(deals_won / (deals_won + deals_lost) * 100, 1) if (deals_won + deals_lost) else 0
    avg_deal_size = round(total_revenue / deals_won, 2) if deals_won else 0

    if monthly is None or monthly.empty:
        monthly = (
            deals.assign(month=deals["close_date"].dt.to_period("M").astype(str))
            .groupby("month")
            .agg(
                deals_won=("deal_stage", lambda s: s.str.contains("won", case=False, na=False).sum()),
                deals_lost=("deal_stage", lambda s: s.str.contains("lost", case=False, na=False).sum()),
            )
            .reset_index()
        )
        # Revenue must match the headline definition above (won["amount_usd"]
        # .sum()) -- summing every deal regardless of stage would count open
        # and lost pipeline value as if it were closed revenue, so a
        # synthesized monthly chart could show a different "revenue" than
        # the headline number on the same report.
        monthly_revenue = (
            won.assign(month=won["close_date"].dt.to_period("M").astype(str))
            .groupby("month")["amount_usd"].sum()
            .rename("revenue_usd")
        )
        monthly = monthly.merge(monthly_revenue, on="month", how="left")
        monthly["revenue_usd"] = monthly["revenue_usd"].fillna(0.0)
        monthly["win_rate"] = (monthly["deals_won"] / (monthly["deals_won"] + monthly["deals_lost"]).replace(0, 1)).round(3)
        monthly["avg_deal_size"] = (monthly["revenue_usd"] / monthly["deals_won"].replace(0, 1)).round(2)

    by_rep = (
        won.groupby("sales_rep")
        .agg(revenue_usd=("amount_usd", "sum"), deals_won=("deal_id", "count"))
        .reset_index()
        .sort_values("revenue_usd", ascending=False)
        .to_dict("records")
    )
    by_source = (
        won.groupby("lead_source")
        .agg(revenue_usd=("amount_usd", "sum"), deals_won=("deal_id", "count"))
        .reset_index()
        .sort_values("revenue_usd", ascending=False)
        .to_dict("records")
    )
    by_product = (
        won.groupby("product")
        .agg(revenue_usd=("amount_usd", "sum"), deals_won=("deal_id", "count"))
        .reset_index()
        .sort_values("revenue_usd", ascending=False)
        .to_dict("records")
    )

    last_two = monthly.tail(2)
    momentum = None
    if len(last_two) == 2:
        prev, curr = last_two.iloc[0], last_two.iloc[1]
        momentum = _pct_change(curr["revenue_usd"], prev["revenue_usd"])

    return {
        "totals": {
            "revenue_usd": total_revenue, "deals_won": deals_won, "deals_lost": deals_lost,
            "win_rate_pct": win_rate, "avg_deal_size_usd": avg_deal_size,
        },
        "revenue_momentum_pct": momentum,
        "by_rep": by_rep,
        "by_lead_source": by_source,
        "by_product": by_product,
        "_monthly": monthly,
    }
