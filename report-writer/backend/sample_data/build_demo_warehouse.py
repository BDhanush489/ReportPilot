"""
Builds a local SQLite database that stands in for "Aurora Home Goods's
warehouse" — same underlying data as the CSV/XLSX samples, but with
deliberately different table and column names (the way a real client's
Snowflake/BigQuery schema would never match our canonical field names).

This is what proves the connector + data-context onboarding flow is real: if
the demo warehouse used our own canonical names, the "mapping" step would be
a trivial no-op. Column names below are realistic-but-different on purpose:

  web_analytics.csv           -> ga_sessions_daily   (event_date, channel, device, session_count, ...)
  seo_audit.csv                -> crawl_results        (page_url, http_status, index_status, ...)
  sales_pipeline.xlsx (Deals)  -> crm_opportunities    (opp_id, closed_on, owner, sku, ...)

Run: python sample_data/build_demo_warehouse.py
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
DB_PATH = HERE / "aurora_warehouse.sqlite"


def main() -> None:
    wa = pd.read_csv(HERE / "web_analytics.csv")
    wa = wa.rename(columns={
        "date": "event_date", "channel_group": "channel", "device_category": "device",
        "sessions": "session_count", "new_users": "new_user_count",
        "engaged_sessions": "engaged_session_count", "conversions": "goal_completions",
        "revenue_usd": "total_revenue", "bounce_rate": "bounce_pct",
        "avg_session_duration_sec": "avg_duration_sec",
    })

    seo = pd.read_csv(HERE / "seo_audit.csv")
    seo = seo.rename(columns={
        "url": "page_url", "status_code": "http_status", "is_indexable": "index_status",
        "load_time_ms": "ttfb_ms", "impressions_28d": "impressions", "clicks_28d": "clicks",
        "avg_position": "serp_position", "organic_sessions_28d": "organic_traffic",
        "issue_severity": "severity", "issues": "issue_notes",
    })

    deals = pd.read_excel(HERE / "sales_pipeline.xlsx", sheet_name="Deals")
    deals = deals.rename(columns={
        "deal_id": "opp_id", "close_date": "closed_on", "sales_rep": "owner",
        "product": "sku", "lead_source": "channel", "deal_stage": "status",
        "amount_usd": "closed_amount", "potential_amount_usd": "pipeline_amount",
    })

    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    wa.to_sql("ga_sessions_daily", conn, index=False)
    seo.to_sql("crawl_results", conn, index=False)
    deals.to_sql("crm_opportunities", conn, index=False)
    conn.close()
    print(f"Wrote {DB_PATH} — tables: ga_sessions_daily ({len(wa)} rows), "
          f"crawl_results ({len(seo)} rows), crm_opportunities ({len(deals)} rows)")


if __name__ == "__main__":
    main()
