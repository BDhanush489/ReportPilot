"""
Tests for app/metrics.py::sales_metrics — specifically the synthesized-
monthly fallback (no monthly-summary sheet in the source file), which
previously summed amount_usd across every deal regardless of stage while
the headline Sales Revenue figure summed only Closed-Won deals. The two
numbers could legitimately disagree on the same report. Fixed to filter
the fallback's revenue the same way the headline does.
"""
import pandas as pd
import pytest

from app import metrics as metrics_mod

DEALS = pd.DataFrame([
    {"deal_id": "D1", "close_date": "2026-01-05", "sales_rep": "Alex", "lead_source": "Referral", "product": "Widget", "deal_stage": "Closed Won", "amount_usd": 1000.0},
    {"deal_id": "D2", "close_date": "2026-01-10", "sales_rep": "Sam", "lead_source": "Referral", "product": "Widget", "deal_stage": "Closed Won", "amount_usd": 2000.0},
    {"deal_id": "D3", "close_date": "2026-01-15", "sales_rep": "Alex", "lead_source": "Referral", "product": "Widget", "deal_stage": "Closed Lost", "amount_usd": 5000.0},
    {"deal_id": "D4", "close_date": "2026-02-01", "sales_rep": "Sam", "lead_source": "Referral", "product": "Widget", "deal_stage": "Open", "amount_usd": 9000.0},
    {"deal_id": "D5", "close_date": "2026-02-10", "sales_rep": "Alex", "lead_source": "Referral", "product": "Widget", "deal_stage": "Closed Won", "amount_usd": 3000.0},
])
DEALS["close_date"] = pd.to_datetime(DEALS["close_date"])


def test_headline_revenue_is_won_only():
    result = metrics_mod.sales_metrics(DEALS, None)
    assert result["totals"]["revenue_usd"] == pytest.approx(6000.0)  # 1000 + 2000 + 3000, excludes lost/open


def test_synthesized_monthly_revenue_matches_the_headline_definition():
    """The bug: this used to sum ALL deals (won + lost + open) per month,
    so it could show more "revenue" than the headline figure on the same
    report. Now both use the same won-only definition."""
    result = metrics_mod.sales_metrics(DEALS, None)
    monthly_records = result["_monthly"].to_dict("records")
    monthly_total = sum(r["revenue_usd"] for r in monthly_records)
    assert monthly_total == pytest.approx(result["totals"]["revenue_usd"])
    assert monthly_total == pytest.approx(6000.0)  # not 20000.0 (every deal, the old/buggy behavior)


def test_synthesized_monthly_revenue_per_month_is_won_only():
    result = metrics_mod.sales_metrics(DEALS, None)
    by_month = {r["month"]: r["revenue_usd"] for r in result["_monthly"].to_dict("records")}
    assert by_month["2026-01"] == pytest.approx(3000.0)  # D1 + D2 (won) -- NOT + D3's 5000 (lost)
    assert by_month["2026-02"] == pytest.approx(3000.0)  # D5 (won) -- NOT + D4's 9000 (open)


def test_synthesized_monthly_deal_counts_still_include_every_stage():
    """deals_won/deals_lost counts are a different question from revenue --
    they should still reflect every deal in that stage, not just won ones."""
    result = metrics_mod.sales_metrics(DEALS, None)
    by_month = {r["month"]: r for r in result["_monthly"].to_dict("records")}
    assert by_month["2026-01"]["deals_won"] == 2
    assert by_month["2026-01"]["deals_lost"] == 1


def test_a_month_with_only_lost_or_open_deals_gets_zero_revenue_not_dropped():
    deals_only_lost = pd.DataFrame([
        {"deal_id": "D1", "close_date": "2026-03-01", "sales_rep": "Alex", "lead_source": "Referral", "product": "Widget", "deal_stage": "Closed Lost", "amount_usd": 500.0},
    ])
    deals_only_lost["close_date"] = pd.to_datetime(deals_only_lost["close_date"])
    result = metrics_mod.sales_metrics(deals_only_lost, None)
    by_month = {r["month"]: r for r in result["_monthly"].to_dict("records")}
    assert "2026-03" in by_month  # month still present
    assert by_month["2026-03"]["revenue_usd"] == 0.0  # but with zero revenue, not the lost deal's amount


def test_avg_deal_size_in_synthesized_monthly_uses_the_corrected_revenue():
    result = metrics_mod.sales_metrics(DEALS, None)
    by_month = {r["month"]: r for r in result["_monthly"].to_dict("records")}
    # Jan: 2 won deals, $3000 won revenue -> $1500 avg, not (won+lost)/2
    assert by_month["2026-01"]["avg_deal_size"] == pytest.approx(1500.0)


def test_real_provided_monthly_sheet_is_left_untouched_by_this_fix():
    """This fix only touches the *synthesized* fallback -- a real monthly
    sheet passed in from the source file must pass through exactly as
    given, not be recomputed."""
    provided_monthly = pd.DataFrame([{"month": "2026-01", "deals_won": 1, "deals_lost": 0, "revenue_usd": 999.0}])
    result = metrics_mod.sales_metrics(DEALS, provided_monthly)
    assert result["_monthly"]["revenue_usd"].iloc[0] == 999.0  # untouched, not recomputed to 3000
