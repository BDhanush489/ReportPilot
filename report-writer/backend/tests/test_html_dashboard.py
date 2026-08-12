"""
Tests for app/html_dashboard.py (Lever 4 — the third deliverable).

Uses a real headless Chromium (Playwright) rather than parsing the embedded
JSON directly — the exit criteria for this feature explicitly call for
"loading the file headless," and a real browser is the only way to also
verify the filter/drill-down *interaction* actually works, not just that the
underlying data is correct.
"""
import re
import time
from pathlib import Path

import pandas as pd
import pytest
from playwright.sync_api import sync_playwright

from app import html_dashboard, metrics as metrics_mod
from app.qa import check_traceability
from app.report_object import Period, ReportObject

ANALYTICS_DF = pd.DataFrame([
    {"date": "2026-01-01", "sessions": 100, "new_users": 80, "conversions": 5, "revenue_usd": 500.0,
     "channel_group": "Organic Search", "device_category": "desktop"},
    {"date": "2026-01-05", "sessions": 150, "new_users": 90, "conversions": 8, "revenue_usd": 800.0,
     "channel_group": "Paid Search", "device_category": "mobile"},
    {"date": "2026-01-12", "sessions": 200, "new_users": 130, "conversions": 12, "revenue_usd": 1200.0,
     "channel_group": "Organic Search", "device_category": "desktop"},
    {"date": "2026-01-20", "sessions": 160, "new_users": 95, "conversions": 7, "revenue_usd": 700.0,
     "channel_group": "Paid Search", "device_category": "desktop"},
])
ANALYTICS_DF["date"] = pd.to_datetime(ANALYTICS_DF["date"])


def _strip_private(payload):
    if isinstance(payload, dict):
        return {k: _strip_private(v) for k, v in payload.items() if not str(k).startswith("_")}
    if isinstance(payload, list):
        return [_strip_private(v) for v in payload]
    return payload


_DEFAULT_QA = {
    "badge": "PASS", "failing_checks": [],
    "traceability": {"ok": True, "numbers_checked": 0, "fail": [], "warnings": []},
    "aggregation_sanity": {"ok": True, "mismatches": [], "inconclusive_sources": []},
    "unsupported_claims": {"ok": True, "claims_checked": 0, "unlinked": []},
}


def _obj(metrics_payload: dict, branding: dict, qa: dict | None = None, period_comparison: dict | None = None) -> ReportObject:
    """F0: build_dashboard() takes the canonical object, not a raw metrics
    dict + branding threaded separately -- this wraps a hand-built
    metrics_payload (same convention these tests already used) the same way
    report_builder.py's _finish_report would."""
    return ReportObject(
        report_id="test-dashboard",
        period=Period(label=metrics_payload.get("period_label", "")),
        sources={},
        metrics=metrics_payload,
        series={},
        charts=[],
        narrative={},
        qa=qa if qa is not None else _DEFAULT_QA,
        branding=branding,
        section_order=[],
        period_comparison=period_comparison,
    )


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _load(browser, html: str, block_network: bool = False):
    page = browser.new_page()
    failed_requests = []
    if block_network:
        page.route("**/*", lambda route: (failed_requests.append(route.request.url), route.abort()))
    console_errors = []
    page.on("pageerror", lambda exc: console_errors.append(str(exc)))
    # data: URL avoids depending on a running file server for "opens offline"
    from urllib.parse import quote
    page.goto("data:text/html," + quote(html))
    return page, console_errors, failed_requests


def test_dashboard_is_self_contained_no_network_calls(browser):
    metrics_payload = {"analytics": _strip_private(metrics_mod.analytics_metrics(ANALYTICS_DF))}
    html = html_dashboard.build_dashboard(_obj(metrics_payload, {"client_name": "Test Co"}))

    # No external <script src=...>/<link href=...> at all -- everything must
    # be inline for a single self-contained file.
    assert not re.search(r'<(script|link)[^>]+(src|href)=["\']https?://', html)

    page, console_errors, failed = _load(browser, html, block_network=True)
    assert console_errors == []
    assert failed == []  # nothing even attempted a network request
    assert page.locator(".kpi-card").count() > 0
    page.close()


def test_dashboard_uses_the_same_metrics_payload_as_the_pdf_path(browser):
    # This *is* metrics_payload as report_builder.py would compute it for
    # the PDF path (analytics_metrics + strip_private) -- no separate query,
    # no separate copy, same function call.
    metrics_payload = {"analytics": _strip_private(metrics_mod.analytics_metrics(ANALYTICS_DF))}
    html = html_dashboard.build_dashboard(_obj(metrics_payload, {}))
    page, errors, _ = _load(browser, html)

    expected_sessions = metrics_payload["analytics"]["totals"]["sessions"]
    shown = page.locator('[data-testid="kpi-value-web_sessions"]').inner_text()
    assert shown.replace(",", "") == str(expected_sessions)
    assert errors == []
    page.close()


def test_kpi_cards_filter_and_drilldown_interaction(browser):
    metrics_payload = {"analytics": _strip_private(metrics_mod.analytics_metrics(ANALYTICS_DF))}
    html = html_dashboard.build_dashboard(_obj(metrics_payload, {}))
    page, errors, _ = _load(browser, html)

    assert page.locator(".kpi-card").count() >= 3

    page.locator('.kpi-card[data-card-id="web_revenue"]').click()
    page.wait_for_selector("#drill-panel.active")
    all_rows = page.locator("#drill-table tbody tr").count()
    assert all_rows == len(metrics_payload["analytics"]["by_channel"])

    page.select_option("#channel-filter", "Organic Search")
    filtered_rows = page.locator("#drill-table tbody tr").count()
    organic_count = sum(1 for c in metrics_payload["analytics"]["by_channel"] if c["channel"] == "Organic Search")
    assert filtered_rows == organic_count
    assert filtered_rows < all_rows  # the filter actually did something

    assert errors == []
    page.close()


def test_dashboard_visual_language_matches_theme_tokens(browser):
    from app import theme
    metrics_payload = {"analytics": _strip_private(metrics_mod.analytics_metrics(ANALYTICS_DF))}
    branding = {"primary_color": "#2a78d6"}
    html = html_dashboard.build_dashboard(_obj(metrics_payload, branding))
    page, errors, _ = _load(browser, html)

    color = page.locator(".kpi-card .value").first.evaluate("el => getComputedStyle(el).color")
    # rgb(42, 120, 214) == #2a78d6, the same branding.primary_color the PDF's
    # .exec-summary border-left and .insight-score headline both use.
    assert color == "rgb(42, 120, 214)"
    bg = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
    assert bg == "rgb(252, 252, 251)"  # theme.SURFACE == #fcfcfb
    assert theme.SURFACE == "#fcfcfb"
    assert errors == []
    page.close()


def test_numbers_pass_goal_2_traceability_check_against_the_same_source():
    metrics_payload = {"analytics": _strip_private(metrics_mod.analytics_metrics(ANALYTICS_DF))}
    html_dashboard.build_dashboard(_obj(metrics_payload, {}))  # exercise the real code path
    kpi_cards = html_dashboard._kpi_cards(metrics_payload)

    # Treat every KPI card's displayed string as a narrative claim and run
    # it through the exact same check the PDF's narrative is held to.
    synthetic_report = {
        "report_title": "t", "period_label": "p", "executive_summary": "",
        "highlights": [c["formatted"] for c in kpi_cards],
        "watchouts": [], "sections": [], "next_steps": [],
    }
    result = check_traceability(synthetic_report, metrics_payload)
    assert result.ok, result.fail_findings


def test_qa_badge_is_visible_and_reflects_the_objects_qa_result(browser):
    """The dashboard is a renderer like the PDF -- the same in-band QA badge
    F0 computes once must show up here too, not just in report.html."""
    metrics_payload = {"analytics": _strip_private(metrics_mod.analytics_metrics(ANALYTICS_DF))}
    fail_qa = {**_DEFAULT_QA, "badge": "FAIL", "failing_checks": ["traceability"]}
    html = html_dashboard.build_dashboard(_obj(metrics_payload, {}, qa=fail_qa))
    page, errors, _ = _load(browser, html)

    badge = page.locator('[data-testid="qa-badge"]')
    assert badge.count() == 1
    assert "FAIL" in badge.inner_text()
    assert errors == []
    page.close()


def test_qa_badge_absent_when_object_carries_no_qa_result():
    metrics_payload = {"analytics": _strip_private(metrics_mod.analytics_metrics(ANALYTICS_DF))}
    html = html_dashboard.build_dashboard(_obj(metrics_payload, {}, qa={}))
    assert 'data-testid="qa-badge"' not in html


def test_period_comparison_section_absent_when_object_carries_none():
    """None is the ordinary case (a one-off upload, or a schedule's first
    run) -- not an error, and the section must simply not render."""
    metrics_payload = {"analytics": _strip_private(metrics_mod.analytics_metrics(ANALYTICS_DF))}
    html = html_dashboard.build_dashboard(_obj(metrics_payload, {}))
    assert 'id="period-comparison"' not in html


def test_period_comparison_section_renders_real_deltas(browser):
    metrics_payload = {"analytics": _strip_private(metrics_mod.analytics_metrics(ANALYTICS_DF))}
    comparison = {
        "prior_report_id": "prior-123",
        "prior_period_label": "2025-12-01 to 2025-12-31",
        "analytics": {
            "revenue_usd": {"field": "revenue_usd", "current": 1500.0, "prior": 1000.0, "abs_delta": 500.0, "pct_delta": 50.0},
        },
    }
    html = html_dashboard.build_dashboard(_obj(metrics_payload, {}, period_comparison=comparison))
    page, errors, _ = _load(browser, html)

    rows = page.locator('[data-testid="period-comparison-row"]')
    assert rows.count() == 1
    assert rows.first.get_attribute("data-direction") == "up"
    assert "+50.0%" in rows.first.inner_text()
    assert "2025-12-01 to 2025-12-31" in page.locator("#period-comparison h2").inner_text()
    assert errors == []
    page.close()


def test_dashboard_renders_empty_dataset_without_error(browser):
    html = html_dashboard.build_dashboard(_obj({}, {"client_name": "Empty Co"}))
    page, errors, _ = _load(browser, html)
    assert page.locator(".kpi-card").count() == 0
    assert page.locator(".empty-state").inner_text() == "No data available for this report."
    assert errors == []
    page.close()


def test_dashboard_stays_small_and_fast_on_a_large_source_dataset(browser):
    # ~50k rows of source data -- but the dashboard only ever embeds
    # metrics.py's pre-computed aggregates, never raw rows, so file size and
    # render time shouldn't scale with source size at all. That's the actual
    # perf property this criterion is checking, not "can it handle 50k DOM rows."
    n = 50_000
    dates = pd.date_range("2025-01-01", periods=60, freq="D")
    channels = ["Organic Search", "Paid Search", "Paid Social", "Email", "Direct", "Referral"]
    devices = ["desktop", "mobile", "tablet"]
    big_df = pd.DataFrame({
        "date": [dates[i % len(dates)] for i in range(n)],
        "sessions": [1 + (i % 50) for i in range(n)],
        "new_users": [1 + (i % 30) for i in range(n)],
        "conversions": [i % 5 for i in range(n)],
        "revenue_usd": [float((i % 500)) for i in range(n)],
        "channel_group": [channels[i % len(channels)] for i in range(n)],
        "device_category": [devices[i % len(devices)] for i in range(n)],
    })

    metrics_payload = {"analytics": _strip_private(metrics_mod.analytics_metrics(big_df))}

    start = time.perf_counter()
    html = html_dashboard.build_dashboard(_obj(metrics_payload, {"client_name": "Big Co"}))
    build_seconds = time.perf_counter() - start

    assert build_seconds < 5.0, f"dashboard build took {build_seconds:.2f}s for a {n}-row source"
    assert len(html.encode("utf-8")) < 500_000, f"dashboard HTML was {len(html)} bytes for a {n}-row source"

    page, errors, _ = _load(browser, html)
    assert page.locator(".kpi-card").count() > 0
    shown = page.locator('[data-testid="kpi-value-web_sessions"]').inner_text()
    assert shown.replace(",", "") == str(metrics_payload["analytics"]["totals"]["sessions"])
    assert errors == []
    page.close()
