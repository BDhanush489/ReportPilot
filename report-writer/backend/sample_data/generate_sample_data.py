"""
Generates realistic synthetic client data for demoing the AI report writer:
  - web_analytics.csv   (GA4-style channel/date export)
  - seo_audit.csv       (site crawl / technical SEO audit, one row per URL)
  - sales_pipeline.xlsx (CRM export: closed deals + monthly summary)

The generation logic is a reusable function (generate_client_dataset) so
generate_multi_client_data.py can produce additional fictitious clients with
different business profiles (scale, trend, SEO health, team size) without
duplicating this file. Running this script directly regenerates the default
client, "Aurora Home Goods" (mid-size DTC e-commerce brand, declining organic
channel), into this directory — unchanged from before this refactor.
"""
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import date, timedelta

DEVICES = ["desktop", "mobile", "tablet"]


def generate_client_dataset(
    seed: int,
    days: int,
    start: date,
    channels: list[str],
    base_sessions: dict[str, float],
    conv_rate: dict[str, float],
    aov: dict[str, float],
    trend_fn,
    n_urls: int,
    seo_issue_rate: float,
    reps: list[str],
    products: list[str],
    regions: list[str],
    lead_sources: list[str],
    lead_source_weights: list[float],
    win_rate: float,
    deal_rate: float,
    deal_amount_mean_log: float,
    url_slug_pool: list[str],
    url_prefix: str,
    seasonal_amplitude: float = 0.18,
) -> dict[str, pd.DataFrame]:
    """Returns {"analytics": df, "seo": df, "deals": df, "monthly": df}.

    trend_fn(day_index: int, channel: str) -> float multiplier — lets each
    client have its own growth/decline story per channel (e.g. Aurora's
    declining organic + growing paid social; a healthier client might have no
    decline at all).
    """
    rng = np.random.default_rng(seed)

    # --- 1. Web analytics -------------------------------------------------
    rows = []
    for d in range(days):
        day = start + timedelta(days=d)
        dow_factor = 1.15 if day.weekday() in (5, 6) else 1.0
        seasonal = 1.0 + seasonal_amplitude * np.sin(2 * np.pi * d / max(days, 1))
        for channel in channels:
            mult = trend_fn(d, channel)
            noise = rng.normal(1.0, 0.09)
            sessions = max(5, int(base_sessions[channel] * dow_factor * seasonal * mult * noise))
            new_users = int(sessions * rng.uniform(0.55, 0.78))
            engaged = int(sessions * rng.uniform(0.42, 0.68))
            conversions = rng.binomial(sessions, max(0.001, conv_rate[channel] * rng.uniform(0.85, 1.15)))
            revenue = round(conversions * aov[channel] * rng.uniform(0.85, 1.2), 2)
            bounce_rate = round(rng.uniform(0.28, 0.62), 3)
            avg_duration_sec = int(rng.uniform(45, 260))
            device = rng.choice(DEVICES, p=[0.42, 0.5, 0.08])
            rows.append({
                "date": day.isoformat(), "channel_group": channel, "device_category": device,
                "sessions": sessions, "new_users": new_users, "engaged_sessions": engaged,
                "conversions": conversions, "revenue_usd": revenue, "bounce_rate": bounce_rate,
                "avg_session_duration_sec": avg_duration_sec,
            })
    analytics_df = pd.DataFrame(rows)

    # --- 2. SEO / site audit ----------------------------------------------
    sections = ["/", "/products/", "/collections/", "/blog/", "/pages/"]
    url_rows = []
    for i in range(n_urls):
        if i < max(1, n_urls // 7):
            path, url = "/", f"https://{url_prefix}.example.com/"
        else:
            section = rng.choice(sections[1:])
            slug = f"{rng.choice(url_slug_pool)}-{i}"
            path = f"{section}{slug}"
            url = f"https://{url_prefix}.example.com{path}"

        bad_p = seo_issue_rate
        status_code = rng.choice([200, 301, 404, 500],
                                  p=_normalize([max(0.05, 0.93 - bad_p * 2), 0.03,
                                                max(0.001, 0.03 + bad_p), max(0.001, 0.01 + bad_p)]))
        is_indexable = status_code == 200 and rng.random() > (0.08 + seo_issue_rate)
        load_time_ms = int(rng.gamma(4, 420 * (1 + seo_issue_rate)))
        title_len = int(rng.normal(58, 14))
        meta_desc_len = int(rng.normal(148, 35))
        h1_count = rng.choice([1, 1, 1, 0, 2], p=[0.72, 0.1, 0.08, 0.06, 0.04])
        word_count = max(40, int(rng.gamma(3, 260)))
        has_canonical = rng.random() > (0.06 + seo_issue_rate)
        mobile_friendly = rng.random() > (0.05 + seo_issue_rate)
        broken_links = rng.poisson(0.3 + seo_issue_rate * 2)
        images_missing_alt = rng.poisson(1.4)
        impressions = int(rng.gamma(2, 380)) if is_indexable else int(rng.gamma(1, 40))
        ctr = round(rng.uniform(0.01, 0.09), 4)
        clicks = int(impressions * ctr)
        avg_position = round(rng.uniform(3, 68), 1)
        organic_sessions = int(clicks * rng.uniform(0.85, 1.1))

        issues = []
        if status_code >= 400:
            issues.append("broken_page")
        if status_code == 500:
            issues.append("server_error")
        if not is_indexable:
            issues.append("noindex_or_blocked")
        if title_len < 30 or title_len > 65:
            issues.append("title_length")
        if meta_desc_len < 70 or meta_desc_len > 165:
            issues.append("meta_description_length")
        if h1_count != 1:
            issues.append("h1_count")
        if word_count < 150:
            issues.append("thin_content")
        if not has_canonical:
            issues.append("missing_canonical")
        if not mobile_friendly:
            issues.append("not_mobile_friendly")
        if broken_links > 0:
            issues.append("broken_internal_links")
        if images_missing_alt > 2:
            issues.append("images_missing_alt")
        if load_time_ms > 2500:
            issues.append("slow_load_time")

        if any(x in issues for x in ["broken_page", "server_error"]):
            severity = "critical"
        elif issues:
            severity = "warning"
        else:
            severity = "good"

        url_rows.append({
            "url": url, "path": path, "status_code": status_code, "is_indexable": is_indexable,
            "load_time_ms": load_time_ms, "title_length": max(0, title_len),
            "meta_description_length": max(0, meta_desc_len), "h1_count": h1_count,
            "word_count": word_count, "has_canonical": has_canonical, "mobile_friendly": mobile_friendly,
            "broken_internal_links": broken_links, "images_missing_alt": images_missing_alt,
            "impressions_28d": impressions, "clicks_28d": clicks, "ctr": ctr,
            "avg_position": avg_position, "organic_sessions_28d": organic_sessions,
            "issue_severity": severity, "issues": ";".join(issues) if issues else "",
        })
    seo_df = pd.DataFrame(url_rows)

    # --- 3. Sales pipeline --------------------------------------------------
    deal_rows = []
    deal_id = 10000
    for d in range(days):
        day = start + timedelta(days=d)
        n_deals_today = rng.poisson(deal_rate + deal_rate * 0.4 * np.sin(2 * np.pi * d / max(days, 1)))
        for _ in range(n_deals_today):
            deal_id += 1
            amount = round(float(rng.lognormal(mean=deal_amount_mean_log, sigma=0.55)), 2)
            stage = rng.choice(["Closed Won", "Closed Lost"], p=[win_rate, 1 - win_rate])
            deal_rows.append({
                "deal_id": deal_id, "close_date": day.isoformat(), "sales_rep": rng.choice(reps),
                "product": rng.choice(products), "region": rng.choice(regions),
                "lead_source": rng.choice(lead_sources, p=lead_source_weights),
                "deal_stage": stage, "amount_usd": amount if stage == "Closed Won" else 0.0,
                "potential_amount_usd": amount, "days_to_close": int(rng.gamma(3, 6)),
            })
    deals_df = pd.DataFrame(deal_rows)

    monthly = (
        deals_df.assign(month=pd.to_datetime(deals_df["close_date"]).dt.to_period("M").astype(str))
        .groupby("month")
        .agg(
            deals_won=("deal_stage", lambda s: (s == "Closed Won").sum()),
            deals_lost=("deal_stage", lambda s: (s == "Closed Lost").sum()),
            revenue_usd=("amount_usd", "sum"),
            avg_deal_size=("amount_usd", lambda s: s[s > 0].mean()),
        )
        .reset_index()
    )
    monthly["win_rate"] = (monthly["deals_won"] / (monthly["deals_won"] + monthly["deals_lost"])).round(3)

    return {"analytics": analytics_df, "seo": seo_df, "deals": deals_df, "monthly": monthly}


def _normalize(weights: list[float]) -> list[float]:
    total = sum(weights)
    return [w / total for w in weights]


def write_client_dataset(data: dict[str, pd.DataFrame], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    data["analytics"].to_csv(out_dir / "web_analytics.csv", index=False)
    data["seo"].to_csv(out_dir / "seo_audit.csv", index=False)
    with pd.ExcelWriter(out_dir / "sales_pipeline.xlsx", engine="openpyxl") as writer:
        data["deals"].to_excel(writer, sheet_name="Deals", index=False)
        data["monthly"].to_excel(writer, sheet_name="Monthly Summary", index=False)
    print(f"{out_dir}: web_analytics.csv ({len(data['analytics'])} rows), "
          f"seo_audit.csv ({len(data['seo'])} rows), "
          f"sales_pipeline.xlsx ({len(data['deals'])} deal rows, {len(data['monthly'])} monthly rows)")


if __name__ == "__main__":
    OUT = Path(__file__).parent
    START = date(2026, 1, 1)
    DAYS = 181
    CHANNELS = ["Organic Search", "Paid Search", "Paid Social", "Email", "Direct", "Referral"]

    def aurora_trend(day_index: int, channel: str) -> float:
        day = START + timedelta(days=day_index)
        if channel == "Paid Social":
            return 1.0 + max(0, (day - date(2026, 4, 1)).days) * 0.0035
        if channel == "Organic Search":
            return max(0.55, 1.0 - max(0, (day - date(2026, 2, 15)).days) * 0.0012)
        return 1.0

    data = generate_client_dataset(
        seed=42, days=DAYS, start=START, channels=CHANNELS,
        base_sessions={"Organic Search": 620, "Paid Search": 310, "Paid Social": 260,
                        "Email": 140, "Direct": 190, "Referral": 90},
        conv_rate={"Organic Search": 0.021, "Paid Search": 0.034, "Paid Social": 0.016,
                   "Email": 0.041, "Direct": 0.028, "Referral": 0.019},
        aov={"Organic Search": 78, "Paid Search": 72, "Paid Social": 61,
             "Email": 84, "Direct": 91, "Referral": 69},
        trend_fn=aurora_trend, n_urls=220, seo_issue_rate=0.0,
        reps=["Jordan Lee", "Priya Nair", "Sam Osei", "Taylor Brooks", "Morgan Diaz"],
        products=["Living Room Bundle", "Bedroom Refresh Kit", "Dining Essentials",
                  "Outdoor Collection", "Starter Set"],
        regions=["Northeast", "Midwest", "South", "West"],
        lead_sources=["Organic Search", "Paid Search", "Paid Social", "Email", "Referral", "Trade Show"],
        lead_source_weights=[0.28, 0.19, 0.16, 0.16, 0.13, 0.08],
        win_rate=0.68, deal_rate=3.4, deal_amount_mean_log=6.1,
        url_slug_pool=["cozy-throw-blanket", "ceramic-dinnerware-set", "linen-bedding-bundle",
                       "oak-side-table", "wool-area-rug", "brass-candle-holders", "rattan-basket-set",
                       "velvet-accent-chair", "marble-coasters", "woven-wall-hanging", "glass-vase-trio",
                       "stoneware-mug-set", "bamboo-cutting-board", "cotton-throw-pillow", "iron-plant-stand"],
        url_prefix="aurorahome",
    )
    write_client_dataset(data, OUT)
    print("Done.")
