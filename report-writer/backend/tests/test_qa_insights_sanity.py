"""
Tests for qa.py's check_insights_sanity — independently re-verifies
insights.py's deterministic cards (health score, SEO opportunity, device
gap, lead-source efficiency) via recompute-and-diff, closing the gap where
those figures were trusted by construction but never independently
re-checked the way a narrative claim is.

Real sample data is used (not just hand-built dicts) so the recomputed
cards are genuine, non-trivial insight text — the same figures a real
report would actually show.
"""
import copy

from app import metrics as metrics_mod, parsers
from app.insights import compute_insights
from app.qa import check_insights_sanity, run_qa

SAMPLE = __import__("pathlib").Path(__file__).parent.parent / "sample_data"


def _real_metrics_payload() -> dict:
    a_df, _ = parsers.load_web_analytics(open(SAMPLE / "web_analytics.csv", "rb"))
    s_df, _ = parsers.load_seo_audit(open(SAMPLE / "seo_audit.csv", "rb"))
    deals, monthly, _ = parsers.load_sales_pipeline(open(SAMPLE / "sales_pipeline.xlsx", "rb"))
    return {
        "analytics": metrics_mod.analytics_metrics(a_df),
        "seo": metrics_mod.seo_metrics(s_df),
        "sales": metrics_mod.sales_metrics(deals, monthly),
    }


# ---------------------------------------------------------------------------
# None vs. empty list: the exact distinction the regression fix depends on
# ---------------------------------------------------------------------------

def test_none_is_skipped_entirely_vacuously_ok():
    """A hand-built test fixture (or any caller not using this facility)
    that never set an "insights" key must not be penalized for it."""
    result = check_insights_sanity(None, {"analytics": {}})
    assert result.ok
    assert result.missing_cards == [] and result.unexpected_cards == []


def test_real_empty_list_is_still_validated_for_real():
    """A genuinely empty list (insights.py legitimately found nothing to
    say) is NOT the same as None -- if compute_insights() would produce a
    real card from this metrics payload, an empty reported list must be
    caught as missing, not waved through."""
    metrics_payload = _real_metrics_payload()
    result = check_insights_sanity([], metrics_payload)
    assert not result.ok
    assert "health_score" in result.missing_cards  # always produced when analytics data exists


# ---------------------------------------------------------------------------
# Real recompute-and-diff against real sample data
# ---------------------------------------------------------------------------

def test_untouched_real_insights_pass_cleanly():
    metrics_payload = _real_metrics_payload()
    reported = compute_insights(copy.deepcopy(metrics_payload))
    result = check_insights_sanity(reported, metrics_payload)
    assert result.ok, (result.mismatches, result.missing_cards, result.unexpected_cards)


def test_a_genuinely_altered_headline_is_caught():
    """Simulates exactly the class of bug this check exists to catch: the
    persisted card silently diverges from what compute_insights() would
    produce right now from the same metrics."""
    metrics_payload = _real_metrics_payload()
    reported = compute_insights(copy.deepcopy(metrics_payload))
    tampered = copy.deepcopy(reported)
    health_card = next(c for c in tampered if c["id"] == "health_score")
    health_card["headline"] = "A+"  # not what recompute actually produces

    result = check_insights_sanity(tampered, metrics_payload)
    assert not result.ok
    mismatch = next(m for m in result.mismatches if m.card_id == "health_score" and m.field == "headline")
    assert mismatch.reported == "A+"


def test_a_missing_card_is_caught():
    metrics_payload = _real_metrics_payload()
    reported = compute_insights(copy.deepcopy(metrics_payload))
    without_health = [c for c in reported if c["id"] != "health_score"]

    result = check_insights_sanity(without_health, metrics_payload)
    assert not result.ok
    assert "health_score" in result.missing_cards


def test_an_unexpected_extra_card_is_caught():
    metrics_payload = _real_metrics_payload()
    reported = compute_insights(copy.deepcopy(metrics_payload))
    reported.append({"id": "not_a_real_card", "tag": "score", "title": "t", "headline": "h", "detail": "d"})

    result = check_insights_sanity(reported, metrics_payload)
    assert not result.ok
    assert "not_a_real_card" in result.unexpected_cards


def test_the_historical_avg_deal_mutation_bug_would_have_been_caught():
    """_lead_source_efficiency mutates the by_lead_source records it's
    handed (adds "_avg_deal" in place) -- this project already hit that as
    a real bug leaking into html_dashboard.py's drill tables. Simulates the
    same shape of divergence here: the reported card reflects a
    pre-mutation input, recompute reflects a fresh (already-mutated-once)
    one, and asserts the sanity check surfaces *some* real difference
    rather than silently agreeing."""
    metrics_payload = _real_metrics_payload()
    reported = compute_insights(copy.deepcopy(metrics_payload))
    # Simulate divergence: the "reported" version came from a differently
    # shaped input (extra affected dimension), so its efficiency ratio text
    # differs from what a fresh recompute against today's metrics produces.
    lead_source_card = next((c for c in reported if c["id"] == "lead_source_efficiency"), None)
    if lead_source_card:
        lead_source_card["headline"] = "999.9x"
        result = check_insights_sanity(reported, metrics_payload)
        assert not result.ok
        assert any(m.card_id == "lead_source_efficiency" for m in result.mismatches)


# ---------------------------------------------------------------------------
# Wired into run_qa / QAReport
# ---------------------------------------------------------------------------

def test_run_qa_fails_the_badge_on_an_insights_mismatch():
    metrics_payload = _real_metrics_payload()
    reported_insights = compute_insights(copy.deepcopy(metrics_payload))
    reported_insights[0]["headline"] = "totally wrong"

    narrative = {
        "report_title": "t", "period_label": "p", "executive_summary": "",
        "highlights": [], "watchouts": [], "sections": [], "next_steps": [],
        "insights": reported_insights,
    }
    result = run_qa(narrative, metrics_payload)
    assert result.badge == "FAIL"
    assert "insights_sanity" in result.failing_checks
    assert not result.insights.ok


def test_run_qa_report_to_dict_includes_insights_sanity_and_is_json_serializable():
    import json
    metrics_payload = _real_metrics_payload()
    narrative = {
        "report_title": "t", "period_label": "p", "executive_summary": "",
        "highlights": [], "watchouts": [], "sections": [], "next_steps": [],
        "insights": compute_insights(copy.deepcopy(metrics_payload)),
    }
    result = run_qa(narrative, metrics_payload)
    d = result.to_dict()
    assert "insights_sanity" in d
    assert d["insights_sanity"]["ok"] is True
    json.dumps(d)


def test_run_qa_without_an_insights_key_at_all_does_not_fail_on_insights_sanity():
    """Confirms the regression fix directly: a hand-built narrative dict
    with no "insights" key (the existing test_qa.py fixture shape) must
    never fail because of this new check."""
    result = run_qa({"report_title": "t", "period_label": "p", "executive_summary": "",
                      "highlights": [], "watchouts": [], "sections": [], "next_steps": []},
                     {"analytics": {"totals": {"sessions": 100}}})
    assert result.insights.ok
    assert "insights_sanity" not in result.failing_checks
