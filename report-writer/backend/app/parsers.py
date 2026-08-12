"""
Ingests raw client exports (analytics CSV, SEO/site-audit CSV, sales
spreadsheet) into normalized pandas DataFrames.

Real client exports rarely have identical column names across tools, so each
loader accepts a small set of common aliases and fills in sane defaults for
anything missing — degrading gracefully rather than crashing on a slightly
different column header. Real client exports also aren't clean: currency
strings, mixed date formats, stray whitespace, typo'd categories, and
duplicate rows are the norm — see cleaning.py, which every normalize_*
function below runs the data through before metrics.py ever sees it. Each
normalize_* function returns (clean_df, cleaning_issues) so callers can
surface what was found/fixed in the report's Data Quality section.
"""
from __future__ import annotations

import io
from typing import BinaryIO

import pandas as pd

from . import cleaning

KNOWN_CHANNELS = ["Organic Search", "Paid Search", "Paid Social", "Email", "Direct", "Referral", "Trade Show"]
KNOWN_DEVICES = ["desktop", "mobile", "tablet"]
KNOWN_DEAL_STAGES = ["Closed Won", "Closed Lost", "Open"]

#: T2 — data-availability contract. Every column below CAN be defaulted
#: without crashing (that's the whole point of the alias/default machinery
#: above), but a defaulted column in this set would make a chart look like
#: real data ("$0 revenue") instead of "we don't have this" — these are
#: exactly the columns app/template_specs/*.json's ChartSpec.requires_columns
#: currently names. Deliberately NOT every defaulted column: routine gaps
#: (engaged_sessions, bounce_rate, ...) are genuinely harmless and logging
#: them would just be noise in the Data Quality section.
_BUSINESS_CRITICAL_COLUMNS = {"analytics": {"revenue_usd"}, "seo": set(), "sales": {"amount_usd"}}


def _rename_by_alias(df: pd.DataFrame, alias_map: dict[str, list[str]]) -> pd.DataFrame:
    lower_cols = {c.lower().strip(): c for c in df.columns}
    rename = {}
    for canonical, aliases in alias_map.items():
        for alias in [canonical, *aliases]:
            key = alias.lower().strip()
            if key in lower_cols:
                rename[lower_cols[key]] = canonical
                break
    return df.rename(columns=rename)


def _synthesize_date_from_year_month(df: pd.DataFrame) -> pd.DataFrame:
    """Some exports (Google Analytics, Alteryx profiles) split a date into
    separate Year / "Month of the year" columns instead of one date column —
    _rename_by_alias has nothing to map those onto since it only handles
    1:1 renames, not combining two columns into one. Only runs when there's
    no date column after alias renaming and a year+month pair actually
    exists, so it never overrides a real date column. Builds the first of
    that month, since day-level detail isn't available in this shape."""
    if "date" in df.columns:
        return df
    lower_cols = {c.lower().strip(): c for c in df.columns}
    year_col = next((orig for key, orig in lower_cols.items() if key in ("year", "yr")), None)
    month_col = next((orig for key, orig in lower_cols.items() if "month" in key), None)
    if not year_col or not month_col:
        return df

    year = pd.to_numeric(df[year_col], errors="coerce")
    month = pd.to_numeric(df[month_col], errors="coerce")
    df["date"] = pd.to_datetime(pd.DataFrame({"year": year, "month": month, "day": 1}), errors="coerce")
    return df


def normalize_web_analytics(df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Applies defaults + dtype coercion to a DataFrame that already has
    canonical column names — shared by the CSV path (after alias-rename) and
    the SQL path (after SELECT ... AS aliasing in sql_source.py), so both
    sources feed metrics.py through identical normalization."""
    issues: list[dict] = []
    required_defaults = {
        "channel_group": "Unknown", "device_category": "unknown", "sessions": 0,
        "new_users": 0, "engaged_sessions": 0, "conversions": 0, "revenue_usd": 0.0,
        "bounce_rate": None, "avg_session_duration_sec": None,
    }
    for col, default in required_defaults.items():
        if col not in df.columns:
            if col in _BUSINESS_CRITICAL_COLUMNS["analytics"]:
                issues.append(cleaning.missing_column("analytics", col, len(df), default))
            df[col] = default
    if "date" not in df.columns:
        raise ValueError("Analytics data must include a date column")

    df["date"], date_issues = cleaning.clean_dates(df["date"], source="analytics", column="date")
    issues += date_issues

    for col in ["sessions", "new_users", "engaged_sessions", "conversions", "revenue_usd"]:
        df[col], col_issues = cleaning.clean_numeric(df[col], source="analytics", column=col)
        df[col] = df[col].fillna(0)
        issues += col_issues

    df["channel_group"], ch_issues = cleaning.clean_categorical(
        df["channel_group"], source="analytics", column="channel_group", known_values=KNOWN_CHANNELS)
    issues += ch_issues
    df["device_category"], dev_issues = cleaning.clean_categorical(
        df["device_category"], source="analytics", column="device_category", known_values=KNOWN_DEVICES)
    issues += dev_issues

    df, drop_issues = cleaning.drop_missing_required(df, source="analytics", required=["date"])
    issues += drop_issues
    df, dupe_issues = cleaning.dedupe(df, source="analytics", subset=["date", "channel_group", "device_category"])
    issues += dupe_issues

    return df, issues


def load_web_analytics(file: BinaryIO) -> tuple[pd.DataFrame, list[dict]]:
    df = pd.read_csv(file)
    df = _rename_by_alias(df, {
        "date": ["day", "event_date"],
        "channel_group": ["channel", "session default channel group", "default channel group", "source / medium", "medium"],
        "device_category": ["device", "device category"],
        "sessions": ["session count"],
        "new_users": ["new users"],
        "engaged_sessions": ["engaged sessions"],
        "conversions": ["conversions", "key events", "goal completions"],
        "revenue_usd": ["revenue", "total revenue", "purchase revenue"],
        "bounce_rate": ["bounce rate"],
        "avg_session_duration_sec": ["average session duration", "avg session duration"],
    })
    df = _synthesize_date_from_year_month(df)
    return normalize_web_analytics(df)


def normalize_seo_audit(df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Shared by the CSV path (after alias-rename) and the SQL path — see
    normalize_web_analytics() docstring."""
    issues: list[dict] = []
    if "url" not in df.columns:
        raise ValueError("SEO audit data must include a URL/page column")

    numeric_defaults = {
        "status_code": 200, "load_time_ms": 0, "title_length": 0, "meta_description_length": 0,
        "h1_count": 1, "word_count": 0, "broken_internal_links": 0, "images_missing_alt": 0,
        "impressions_28d": 0, "clicks_28d": 0, "ctr": 0.0, "avg_position": 0.0, "organic_sessions_28d": 0,
    }
    for col, default in numeric_defaults.items():
        if col not in df.columns:
            if col in _BUSINESS_CRITICAL_COLUMNS["seo"]:
                issues.append(cleaning.missing_column("seo", col, len(df), default))
            df[col] = default
        df[col], col_issues = cleaning.clean_numeric(df[col], source="seo", column=col)
        df[col] = df[col].fillna(default)
        issues += col_issues

    if "is_indexable" not in df.columns:
        df["is_indexable"] = df["status_code"].eq(200)
    if "has_canonical" not in df.columns:
        df["has_canonical"] = True
    if "mobile_friendly" not in df.columns:
        df["mobile_friendly"] = True
    if "issues" not in df.columns:
        df["issues"] = ""
    if "issue_severity" not in df.columns:
        df["issue_severity"] = df["status_code"].apply(lambda s: "critical" if s >= 400 else "good")
    df["issues"] = df["issues"].fillna("")

    df["url"], url_issues = cleaning.clean_categorical(df["url"], source="seo", column="url")
    issues += url_issues
    df, drop_issues = cleaning.drop_missing_required(df, source="seo", required=["url"])
    issues += drop_issues
    df, dupe_issues = cleaning.dedupe(df, source="seo", subset=["url"])
    issues += dupe_issues

    return df, issues


def load_seo_audit(file: BinaryIO) -> tuple[pd.DataFrame, list[dict]]:
    df = pd.read_csv(file)
    df = _rename_by_alias(df, {
        "url": ["page", "address", "page url"],
        "status_code": ["status", "http status", "response code"],
        "is_indexable": ["indexable", "indexed"],
        "load_time_ms": ["load time", "page load time (ms)", "ttfb"],
        "title_length": ["title length", "title char length"],
        "meta_description_length": ["meta description length", "meta desc length"],
        "h1_count": ["h1 count", "number of h1s"],
        "word_count": ["word count", "content word count"],
        "has_canonical": ["canonical", "has canonical tag"],
        "mobile_friendly": ["mobile friendly", "is mobile friendly"],
        "broken_internal_links": ["broken links", "broken internal links"],
        "images_missing_alt": ["images missing alt", "missing alt text"],
        "impressions_28d": ["impressions", "impressions (28 days)"],
        "clicks_28d": ["clicks", "clicks (28 days)"],
        "ctr": ["ctr", "click through rate"],
        "avg_position": ["average position", "avg position", "position"],
        "organic_sessions_28d": ["organic sessions", "organic traffic"],
        "issue_severity": ["severity"],
        "issues": ["issue", "issue types", "notes"],
    })
    return normalize_seo_audit(df)


def normalize_deals(deals: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Shared by the CSV/XLSX path (after alias-rename) and the SQL path — see
    normalize_web_analytics() docstring."""
    issues: list[dict] = []
    defaults = {
        "sales_rep": "Unassigned", "product": "Unknown", "region": "Unknown",
        "lead_source": "Unknown", "deal_stage": "Closed Won", "amount_usd": 0.0,
        "potential_amount_usd": 0.0, "days_to_close": 0,
    }
    for col, default in defaults.items():
        if col not in deals.columns:
            if col in _BUSINESS_CRITICAL_COLUMNS["sales"]:
                issues.append(cleaning.missing_column("sales", col, len(deals), default))
            deals[col] = default

    if "close_date" in deals.columns:
        deals["close_date"], date_issues = cleaning.clean_dates(deals["close_date"], source="sales", column="close_date")
        issues += date_issues

    for col in ["amount_usd", "potential_amount_usd", "days_to_close"]:
        deals[col], col_issues = cleaning.clean_numeric(deals[col], source="sales", column=col)
        deals[col] = deals[col].fillna(0)
        issues += col_issues

    for col in ["sales_rep", "product", "region", "lead_source"]:
        deals[col], txt_issues = cleaning.clean_categorical(deals[col], source="sales", column=col)
        issues += txt_issues
    deals["deal_stage"], stage_issues = cleaning.clean_categorical(
        deals["deal_stage"], source="sales", column="deal_stage", known_values=KNOWN_DEAL_STAGES)
    issues += stage_issues

    deals, dupe_issues = cleaning.dedupe(
        deals, source="sales", subset=["deal_id"] if "deal_id" in deals.columns else None)
    issues += dupe_issues

    return deals, issues


def load_sales_pipeline(file: BinaryIO) -> tuple[pd.DataFrame, pd.DataFrame | None, list[dict]]:
    """Accepts an .xlsx with a 'Deals' sheet (+ optional 'Monthly Summary'), or a flat CSV of deals."""
    filename = getattr(file, "name", "") or ""
    is_excel = filename.lower().endswith((".xlsx", ".xls"))

    if is_excel:
        xls = pd.ExcelFile(file)
        deals_sheet = next((s for s in xls.sheet_names if "deal" in s.lower()), xls.sheet_names[0])
        deals = pd.read_excel(xls, sheet_name=deals_sheet)
        monthly_sheet = next((s for s in xls.sheet_names if "month" in s.lower() or "summary" in s.lower()), None)
        monthly = pd.read_excel(xls, sheet_name=monthly_sheet) if monthly_sheet else None
    else:
        deals = pd.read_csv(file)
        monthly = None

    deals = _rename_by_alias(deals, {
        "deal_id": ["id", "opportunity id"],
        "close_date": ["date", "close date", "closed date"],
        "sales_rep": ["rep", "owner", "sales rep", "account owner"],
        "product": ["product", "sku", "plan"],
        "region": ["region", "territory"],
        "lead_source": ["source", "lead source", "channel"],
        "deal_stage": ["stage", "deal stage", "status"],
        "amount_usd": ["amount", "revenue", "closed amount"],
        "potential_amount_usd": ["potential amount", "deal value", "amount (potential)"],
        "days_to_close": ["days to close", "sales cycle length"],
    })
    deals, issues = normalize_deals(deals)

    if monthly is not None:
        monthly = _rename_by_alias(monthly, {
            "month": ["period"],
            "deals_won": ["won", "closed won"],
            "deals_lost": ["lost", "closed lost"],
            "revenue_usd": ["revenue", "total revenue"],
            "avg_deal_size": ["average deal size", "avg deal size"],
            "win_rate": ["win rate", "win %"],
        })
    return deals, monthly, issues
