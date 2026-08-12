"""
Generates a deliberately MESSY client dataset — the kind of export cleaning.py
exists to handle: currency-formatted numbers, mixed date formats, stray
whitespace/casing, typo'd categories, duplicate rows, and blank/placeholder
cells. Starts from the same generator as the other synthetic clients, then
corrupts a copy — so there's a concrete way to prove the ingestion -> cleaning
-> transformation -> modelling -> report pipeline survives real-world-messy
input, not just already-clean CSVs.

Run: python sample_data/generate_messy_data.py
Writes: sample_data/messy-demo/web_analytics.csv, seo_audit.csv, sales_pipeline.xlsx
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from generate_sample_data import generate_client_dataset

OUT = Path(__file__).parent / "messy-demo"
rng = np.random.default_rng(7)

_CHANNEL_TYPOS = {
    "Organic Search": ["organic search", "Organic  Search", "ORGANIC SEARCH", "Orgnic Search", " Organic Search "],
    "Paid Search": ["paid search", "Paid  Search", "Paid-Search"],
    "Paid Social": ["paid social", "PAID SOCIAL", "Paid Soical"],
    "Email": ["email", " Email "],
    "Direct": ["direct", "DIRECT"],
    "Referral": ["referral", "Referal"],
}


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _paren_negative(x: float) -> str:
    return f"({x:,.2f})"


def _messy_date(ts: pd.Timestamp, style: int) -> str:
    return {
        0: ts.strftime("%m/%d/%Y"),
        1: ts.strftime("%d-%b-%Y"),
        2: ts.strftime("%B %d, %Y"),
    }.get(style, ts.strftime("%Y-%m-%d"))


def messify_analytics(clean: pd.DataFrame) -> pd.DataFrame:
    df = clean.copy()
    dates = pd.to_datetime(df["date"])
    df["date"] = dates.dt.strftime("%Y-%m-%d")
    df["revenue_usd"] = df["revenue_usd"].astype(object)
    df["conversions"] = df["conversions"].astype(object)
    df["channel_group"] = df["channel_group"].astype(object)
    df["device_category"] = df["device_category"].astype(object)

    n = len(df)
    order = rng.permutation(n)

    money_rows = order[: n // 6]
    df.loc[money_rows, "revenue_usd"] = [_money(float(v)) for v in df.loc[money_rows, "revenue_usd"]]

    paren_rows = order[n // 6: n // 6 + 4]
    df.loc[paren_rows, "revenue_usd"] = [_paren_negative(float(v)) for v in df.loc[paren_rows, "revenue_usd"]]

    blank_rows = order[n // 6 + 4: n // 6 + 18]
    df.loc[blank_rows, "conversions"] = list(rng.choice(["N/A", "", "unknown", "-"], size=len(blank_rows)))

    date_rows = order[n // 6 + 18: n // 6 + 60]
    styles = rng.integers(0, 4, size=len(date_rows))
    for i, style in zip(date_rows, styles):
        df.at[i, "date"] = _messy_date(dates.iloc[i], int(style))

    df["channel_group"] = [
        rng.choice(_CHANNEL_TYPOS.get(v, [v])) if rng.random() < 0.5 else v for v in df["channel_group"]
    ]
    df["device_category"] = [
        rng.choice([v, str(v).upper(), f" {v} ", str(v).capitalize()]) if rng.random() < 0.35 else v
        for v in df["device_category"]
    ]

    dupes = df.sample(n=8, random_state=7)
    blanks = pd.DataFrame([{c: np.nan for c in df.columns} for _ in range(2)])
    df = pd.concat([df, dupes, blanks], ignore_index=True)
    return df.sample(frac=1, random_state=3).reset_index(drop=True)


def messify_seo(clean: pd.DataFrame) -> pd.DataFrame:
    df = clean.copy()
    df["url"] = df["url"].astype(object)
    df["ctr"] = df["ctr"].astype(object)
    df["word_count"] = df["word_count"].astype(object)

    n = len(df)
    order = rng.permutation(n)

    df.loc[order[:10], "url"] = [f"  {u}  " for u in df.loc[order[:10], "url"]]
    df.loc[order[10:30], "ctr"] = [f"{float(v) * 100:.2f}%" for v in df.loc[order[10:30], "ctr"]]
    df.loc[order[30:40], "word_count"] = "N/A"

    dupes = df.sample(n=5, random_state=7)
    return pd.concat([df, dupes], ignore_index=True).sample(frac=1, random_state=3).reset_index(drop=True)


def messify_deals(clean: pd.DataFrame) -> pd.DataFrame:
    df = clean.copy()
    dates = pd.to_datetime(df["close_date"])
    df["close_date"] = dates.dt.strftime("%Y-%m-%d")
    df["amount_usd"] = df["amount_usd"].astype(object)
    df["deal_stage"] = df["deal_stage"].astype(object)
    df["sales_rep"] = df["sales_rep"].astype(object)

    n = len(df)
    order = rng.permutation(n)

    money_rows = order[: n // 5]
    df.loc[money_rows, "amount_usd"] = [_money(float(v)) for v in df.loc[money_rows, "amount_usd"]]

    date_rows = order[n // 5: n // 5 + 15]
    styles = rng.integers(0, 4, size=len(date_rows))
    for i, style in zip(date_rows, styles):
        df.at[i, "close_date"] = _messy_date(dates.iloc[i], int(style))

    stage_variants = {"Closed Won": ["closed won", "CLOSED-WON", "Closed  Won"],
                       "Closed Lost": ["closed lost", "CLOSED-LOST"]}
    df["deal_stage"] = [
        rng.choice(stage_variants.get(v, [v])) if rng.random() < 0.4 else v for v in df["deal_stage"]
    ]
    df["sales_rep"] = [f" {v} " if rng.random() < 0.2 else v for v in df["sales_rep"]]

    dupes = df.sample(n=4, random_state=7)
    return pd.concat([df, dupes], ignore_index=True).sample(frac=1, random_state=3).reset_index(drop=True)


def main() -> None:
    data = generate_client_dataset(
        seed=7, days=120, start=date(2026, 1, 1),
        channels=["Organic Search", "Paid Search", "Paid Social", "Email", "Direct", "Referral"],
        base_sessions={"Organic Search": 400, "Paid Search": 250, "Paid Social": 200,
                       "Email": 120, "Direct": 150, "Referral": 60},
        conv_rate={"Organic Search": 0.022, "Paid Search": 0.03, "Paid Social": 0.02,
                   "Email": 0.04, "Direct": 0.028, "Referral": 0.02},
        aov={"Organic Search": 90, "Paid Search": 80, "Paid Social": 70,
             "Email": 95, "Direct": 100, "Referral": 85},
        trend_fn=lambda day_index, channel: 1.0, n_urls=60, seo_issue_rate=0.02,
        reps=["Jamie Foster", "Alex Rivera"], products=["Widget Pro", "Widget Lite"],
        regions=["East", "West"],
        lead_sources=["Organic Search", "Paid Search", "Email", "Referral"],
        lead_source_weights=[0.35, 0.25, 0.25, 0.15],
        win_rate=0.65, deal_rate=1.4, deal_amount_mean_log=6.2,
        url_slug_pool=["widget-pro", "widget-lite", "faq", "pricing", "support"],
        url_prefix="messyfixture",
    )

    OUT.mkdir(parents=True, exist_ok=True)
    messy_analytics = messify_analytics(data["analytics"])
    messy_seo = messify_seo(data["seo"])
    messy_deals = messify_deals(data["deals"])

    messy_analytics.to_csv(OUT / "web_analytics.csv", index=False)
    messy_seo.to_csv(OUT / "seo_audit.csv", index=False)
    with pd.ExcelWriter(OUT / "sales_pipeline.xlsx", engine="openpyxl") as writer:
        messy_deals.to_excel(writer, sheet_name="Deals", index=False)

    print(
        f"{OUT}: web_analytics.csv ({len(messy_analytics)} rows), "
        f"seo_audit.csv ({len(messy_seo)} rows), "
        f"sales_pipeline.xlsx ({len(messy_deals)} deal rows) — deliberately messy, for testing cleaning.py"
    )


if __name__ == "__main__":
    main()
