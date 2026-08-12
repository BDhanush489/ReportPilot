"""
Fixtures:
  CLEAN_METRICS / CLEAN_REPORT  — narrative numbers are either exact or a
    plausible rounding of the real metric. Must produce zero FAIL findings.
  BROKEN_METRICS / BROKEN_REPORT — same underlying metrics, but the narrative
    has: a wrong total, a fabricated number with no backing metric at all,
    and (still) one correctly-rounded number — so the checker must isolate
    the two real errors without flagging the legitimate rounding.
"""
import copy
import json

import pandas as pd

from app import metrics as metrics_mod
from app.qa import (
    check_aggregation_sanity,
    check_traceability,
    check_unsupported_claims,
    compute_source_fingerprint,
    run_qa,
)

CLEAN_METRICS = {
    "analytics": {
        "totals": {"sessions": 12345, "revenue_usd": 45231.50, "conversion_rate": 3.42},
        "sessions_change_pct": 12.3,
        "by_channel": [
            {"channel": "Organic Search", "revenue_usd": 20000.0, "share_of_sessions_pct": 40.0},
        ],
    }
}

CLEAN_REPORT = {
    "report_title": "Aurora Home Goods — Performance Report",
    "period_label": "2026-01-01 to 2026-06-30",
    "executive_summary": (
        "Sessions grew 12.3% to 12,345 in the period, driving $45,000 in revenue "
        "at a 3.42% conversion rate."
    ),
    "highlights": ["Organic Search led revenue at $20,000 (40% of sessions)."],
    "watchouts": ["No significant risks identified this period."],
    "sections": [{
        "heading": "Web Analytics",
        "narrative": "Revenue reached $45,000 across 12,345 sessions.",
        "recommendations": ["Double down on Organic Search, the top revenue channel."],
    }],
    "next_steps": ["Review this report with the account team."],
}

BROKEN_REPORT = {
    "report_title": "Aurora Home Goods — Performance Report",
    "period_label": "2026-01-01 to 2026-06-30",
    "executive_summary": (
        "Sessions grew 12.3% to 12,345 in the period, driving $99,999 in revenue "  # wrong total
        "at a 3.42% conversion rate, with an engagement score of 87.5."  # fabricated number
    ),
    "highlights": ["Organic Search led revenue at $20,000 (40% of sessions)."],  # correctly rounded
    "watchouts": ["No significant risks identified this period."],
    "sections": [{
        "heading": "Web Analytics",
        "narrative": "Revenue reached $45,000 across 12,345 sessions.",  # correctly rounded
        "recommendations": ["Double down on Organic Search, the top revenue channel."],
    }],
    "next_steps": ["Review this report with the account team."],
}


def test_clean_report_has_no_fail_findings():
    result = check_traceability(CLEAN_REPORT, CLEAN_METRICS)
    assert result.ok, [f"{f.literal} in {f.field}" for f in result.fail_findings]


def test_clean_report_flags_rounded_number_as_warning_not_exact():
    result = check_traceability(CLEAN_REPORT, CLEAN_METRICS)
    warning_literals = {f.literal for f in result.warning_findings}
    assert "$45,000" in warning_literals


def test_broken_report_catches_wrong_total():
    result = check_traceability(BROKEN_REPORT, CLEAN_METRICS)
    fail_literals = {f.literal for f in result.fail_findings}
    assert "$99,999" in fail_literals


def test_broken_report_catches_fabricated_number():
    result = check_traceability(BROKEN_REPORT, CLEAN_METRICS)
    fail_literals = {f.literal for f in result.fail_findings}
    assert "87.5" in fail_literals


def test_broken_report_still_passes_the_correctly_rounded_number():
    result = check_traceability(BROKEN_REPORT, CLEAN_METRICS)
    fail_literals = {f.literal for f in result.fail_findings}
    warning_literals = {f.literal for f in result.warning_findings}
    assert "$45,000" not in fail_literals
    assert "$45,000" in warning_literals


def test_broken_report_has_exactly_two_fail_findings():
    result = check_traceability(BROKEN_REPORT, CLEAN_METRICS)
    assert len(result.fail_findings) == 2, result.fail_findings


def test_bare_year_is_not_flagged_as_untraceable():
    report = {**CLEAN_REPORT, "executive_summary": "Prepared for the 2026 fiscal year."}
    result = check_traceability(report, CLEAN_METRICS)
    assert result.ok


def test_count_requires_near_exact_match_not_loose_rounding():
    # sessions is really 12345; a report claiming 12000 should not be
    # laundered through the rounding tolerance as "close enough".
    metrics = {"analytics": {"totals": {"sessions": 12345}}}
    report = {**CLEAN_REPORT, "executive_summary": "The site drove 12,000 sessions."}
    result = check_traceability(report, metrics)
    assert not result.ok
    assert any(f.literal == "12,000" for f in result.fail_findings)


def test_a_number_inside_a_tuple_in_metrics_traces_correctly():
    """Regression: metrics.seo_metrics()'s top_issues is a list of (name,
    count) TUPLES, not lists (`[(k, int(v)) for k, v in ...]`). Before
    _flatten_numeric handled tuple the same as list, every top_issues count
    was silently unreachable here -- a genuinely correct number ("'title
    length' affects 68 pages") came back FAIL against a real aurora-home-
    goods report. Caught via T4's QA-badge test on real data, not a
    synthetic fixture."""
    metrics = {"seo": {"top_issues": [("title_length", 68), ("broken_internal_links", 41)]}}
    report = {
        "report_title": "T", "period_label": "P",
        "executive_summary": "Prioritize 'title length' — it affects 68 pages.",
        "highlights": [], "watchouts": [], "sections": [], "next_steps": [],
    }
    result = check_traceability(report, metrics)
    assert result.ok
    assert not result.fail_findings


# ---------------------------------------------------------------------------
# Aggregation sanity
# ---------------------------------------------------------------------------

ANALYTICS_DF = pd.DataFrame([
    # 20-day period, two channels, so the recent/prior-half split in
    # analytics_metrics has real rows on both sides.
    {"date": "2026-01-01", "sessions": 100, "new_users": 80, "conversions": 5, "revenue_usd": 500.0,
     "channel_group": "Organic Search", "device_category": "desktop"},
    {"date": "2026-01-03", "sessions": 150, "new_users": 90, "conversions": 8, "revenue_usd": 800.0,
     "channel_group": "Organic Search", "device_category": "mobile"},
    {"date": "2026-01-05", "sessions": 90, "new_users": 60, "conversions": 4, "revenue_usd": 400.0,
     "channel_group": "Paid Search", "device_category": "desktop"},
    {"date": "2026-01-08", "sessions": 120, "new_users": 70, "conversions": 6, "revenue_usd": 600.0,
     "channel_group": "Paid Search", "device_category": "mobile"},
    {"date": "2026-01-12", "sessions": 200, "new_users": 130, "conversions": 12, "revenue_usd": 1200.0,
     "channel_group": "Organic Search", "device_category": "desktop"},
    {"date": "2026-01-15", "sessions": 175, "new_users": 100, "conversions": 9, "revenue_usd": 900.0,
     "channel_group": "Paid Search", "device_category": "mobile"},
    {"date": "2026-01-18", "sessions": 220, "new_users": 140, "conversions": 15, "revenue_usd": 1500.0,
     "channel_group": "Organic Search", "device_category": "mobile"},
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


def test_aggregation_sanity_passes_when_metrics_match_recomputed_source():
    metrics_payload = {"analytics": _strip_private(metrics_mod.analytics_metrics(ANALYTICS_DF))}
    result = check_aggregation_sanity(metrics_payload, {"analytics": ANALYTICS_DF})
    assert result.ok, result.mismatches
    assert result.inconclusive_sources == []


def test_aggregation_sanity_catches_a_tampered_total():
    metrics_payload = {"analytics": _strip_private(metrics_mod.analytics_metrics(ANALYTICS_DF))}
    tampered = copy.deepcopy(metrics_payload)
    tampered["analytics"]["totals"]["revenue_usd"] += 500.0  # doesn't match any recomputation from ANALYTICS_DF

    result = check_aggregation_sanity(tampered, {"analytics": ANALYTICS_DF})
    assert not result.ok
    assert any(m.path == "totals.revenue_usd" for m in result.mismatches)


def test_aggregation_sanity_marks_missing_source_rows_inconclusive_not_failed():
    metrics_payload = {"analytics": _strip_private(metrics_mod.analytics_metrics(ANALYTICS_DF))}
    result = check_aggregation_sanity(metrics_payload, {})
    assert result.ok  # inconclusive is not the same as failed
    assert result.inconclusive_sources == ["analytics"]


def test_source_fingerprint_is_stable_for_identical_rows():
    fp1 = compute_source_fingerprint(ANALYTICS_DF)
    fp2 = compute_source_fingerprint(ANALYTICS_DF.copy())
    assert fp1 == fp2
    assert fp1["row_count"] == len(ANALYTICS_DF)


def test_source_fingerprint_changes_when_rows_change():
    fp1 = compute_source_fingerprint(ANALYTICS_DF)
    drifted = pd.concat([ANALYTICS_DF, ANALYTICS_DF.iloc[[0]]], ignore_index=True)
    fp2 = compute_source_fingerprint(drifted)
    assert fp1 != fp2
    assert fp2["row_count"] == len(ANALYTICS_DF) + 1


# ---------------------------------------------------------------------------
# Unsupported-claim scan
# ---------------------------------------------------------------------------

CLAIM_METRICS = {
    "analytics": {
        "totals": {"sessions": 12345, "revenue_usd": 45231.50},
        "sessions_change_pct": 12.3,  # positive only — no negative trend anywhere
        "by_channel": [{"channel": "Organic Search", "revenue_usd": 20000.0}],
    }
}

CLAIM_METRICS_NO_RANKED_DATA = {
    "analytics": {"totals": {"sessions": 12345, "revenue_usd": 45231.50}, "sessions_change_pct": 12.3}
}


def _claim_report(sentence: str) -> dict:
    # Deliberately minimal — not spread from CLEAN_REPORT, whose other
    # fields (highlights, sections, recommendations) carry their own claims
    # that would contaminate a test meant to isolate one sentence.
    return {
        "report_title": "Test Report", "period_label": "period",
        "executive_summary": sentence,
        "highlights": [], "watchouts": [], "sections": [], "next_steps": [],
    }


def test_number_backed_claim_is_linked():
    result = check_unsupported_claims(_claim_report("Revenue reached $45,000 across 12,345 sessions."), CLAIM_METRICS)
    assert result.ok
    assert result.findings[0].status == "linked"


def test_fabricated_number_claim_is_unlinked():
    result = check_unsupported_claims(
        _claim_report("Revenue reached an incredible $999,999,999 this period."), CLAIM_METRICS,
    )
    assert not result.ok
    assert result.unlinked[0].reason == "contains an untraceable number"


def test_trend_claim_backed_by_matching_metric_is_linked():
    result = check_unsupported_claims(_claim_report("Sessions grew steadily this period."), CLAIM_METRICS)
    assert result.ok
    assert result.findings[0].status == "linked"


def test_trend_claim_with_no_matching_metric_is_unlinked():
    # sessions_change_pct is +12.3 — nothing in CLAIM_METRICS is negative,
    # so a claimed decline has no metric backing it.
    result = check_unsupported_claims(_claim_report("Paid Search sessions declined sharply this period."), CLAIM_METRICS)
    assert not result.ok
    assert "down" in result.unlinked[0].reason


def test_superlative_claim_backed_by_ranked_data_is_linked():
    result = check_unsupported_claims(_claim_report("Organic Search led the way this period."), CLAIM_METRICS)
    assert result.ok
    assert result.findings[0].status == "linked"


def test_superlative_claim_with_no_ranked_data_is_unlinked():
    result = check_unsupported_claims(
        _claim_report("Organic Search led the way this period."), CLAIM_METRICS_NO_RANKED_DATA,
    )
    assert not result.ok
    assert "superlative" in result.unlinked[0].reason


def test_plain_sentence_with_no_claim_is_not_flagged():
    result = check_unsupported_claims(_claim_report("Thanks for your continued partnership."), CLAIM_METRICS)
    assert result.ok
    assert result.findings == []


def test_unsigned_percent_decrease_matches_a_negative_metric():
    # Real bug caught via a live Ollama-generated report: metrics.py stores
    # sessions_change_pct as -20.8, but correct narrative prose writes that
    # as "a 20.8% decrease" — an unsigned magnitude plus a direction word,
    # never a literal minus sign. This must trace, not fail.
    metrics = {"analytics": {"sessions_change_pct": -20.8}}
    result = check_unsupported_claims(
        _claim_report("Sessions saw a 20.8% decrease this period."), metrics,
    )
    assert result.ok, result.unlinked
    assert result.findings[0].status == "linked"


def test_percent_sign_word_contradicting_actual_metric_sign_is_unlinked():
    # The flip side of the fix above: matching magnitude alone isn't enough
    # to call a percent claim linked — "increase" describing a metric that's
    # actually -20.8 is a real, catchable error.
    metrics = {"analytics": {"sessions_change_pct": -20.8}}
    result = check_unsupported_claims(
        _claim_report("Sessions saw a 20.8% increase this period."), metrics,
    )
    assert not result.ok
    assert "direction word" in result.unlinked[0].reason


def test_literal_minus_sign_percent_still_traces():
    # Also seen from a live model run: "-28.7%" written with an actual minus
    # sign. _NUMBER_RE doesn't capture the sign as part of the match (it
    # matches "28.7%"), so this only traces because the sign-flip allowance
    # for percents catches it against the metric's real negative value.
    metrics = {"analytics": {"by_channel": [{"channel": "Organic Search", "session_change_pct": -28.7}]}}
    result = check_traceability(
        _claim_report("Organic Search sessions changed by -28.7% this period."), metrics,
    )
    assert result.ok, result.fail_findings


def test_higher_performing_is_a_ranking_claim_not_a_trend_claim():
    # Caught via a live model run: "higher-performing lead sources" is a
    # static comparison among sources, not a claim that something increased
    # over time — it must not require a positive change_pct to back it.
    metrics = {"sales": {"by_lead_source": [{"lead_source": "Email", "revenue_usd": 1000.0}]}}
    result = check_unsupported_claims(
        _claim_report("Focus on higher-performing lead sources such as Email."), metrics,
    )
    assert result.ok, result.unlinked


def test_double_down_idiom_is_not_mistaken_for_a_decline_claim():
    # agent.py's own fallback template writes exactly this phrase as a
    # recommendation — it must never be flagged as an unsupported "down" claim.
    result = check_unsupported_claims(
        _claim_report("Double down on Organic Search, the top revenue channel."), CLAIM_METRICS,
    )
    assert result.ok
    assert result.findings[0].status == "linked"


# ---------------------------------------------------------------------------
# Badge output
# ---------------------------------------------------------------------------

def test_badge_is_pass_when_nothing_is_flagged():
    metrics_payload = {"analytics": _strip_private(metrics_mod.analytics_metrics(ANALYTICS_DF))}
    report = _claim_report("The site drove real, exactly-traceable sessions this period.")
    qa_report = run_qa(report, metrics_payload, {"analytics": ANALYTICS_DF})
    assert qa_report.badge == "PASS"
    assert qa_report.failing_checks == []


def test_badge_is_fail_when_traceability_fails():
    qa_report = run_qa(BROKEN_REPORT, CLEAN_METRICS)
    assert qa_report.badge == "FAIL"
    assert "traceability" in qa_report.failing_checks


def test_badge_is_pass_with_warnings_for_a_legitimately_rounded_report():
    # CLEAN_REPORT's "$45,000" is a correct rounding, not an error — that
    # alone should be enough to keep the badge off a bare PASS.
    qa_report = run_qa(CLEAN_REPORT, CLEAN_METRICS)
    assert qa_report.badge == "PASS-WITH-WARNINGS"
    assert qa_report.failing_checks == []


def test_badge_is_pass_with_warnings_when_a_source_is_inconclusive():
    metrics_payload = {"analytics": _strip_private(metrics_mod.analytics_metrics(ANALYTICS_DF))}
    report = _claim_report("Sessions and revenue were both up this period compared to the first half.")
    qa_report = run_qa(report, metrics_payload, source_frames={})  # no rows supplied -> inconclusive
    assert qa_report.badge == "PASS-WITH-WARNINGS"
    assert qa_report.failing_checks == []
    assert qa_report.aggregation.inconclusive_sources == ["analytics"]


def test_badge_reports_all_failing_checks_not_just_the_first():
    metrics_payload = {"analytics": _strip_private(metrics_mod.analytics_metrics(ANALYTICS_DF))}
    tampered = copy.deepcopy(metrics_payload)
    tampered["analytics"]["totals"]["revenue_usd"] += 999.0
    report = _claim_report("Revenue reached an entirely fabricated $12,345,678 this period.")
    qa_report = run_qa(report, tampered, {"analytics": ANALYTICS_DF})
    assert qa_report.badge == "FAIL"
    assert set(qa_report.failing_checks) == {"traceability", "aggregation_sanity", "unsupported_claims"}


def test_qa_report_to_dict_is_json_serializable():
    qa_report = run_qa(BROKEN_REPORT, CLEAN_METRICS)
    payload = qa_report.to_dict()
    serialized = json.dumps(payload)  # must not raise
    reloaded = json.loads(serialized)
    assert reloaded["badge"] == "FAIL"
    assert "traceability" in reloaded["failing_checks"]
    assert reloaded["traceability"]["fail"]  # at least one fail finding present, not just a count
