"""Tests for app/narrative_links.py (Track A3 — narrative <-> chart link)
and its wiring into qa.py's run_qa/QAReport."""
from app.chart_annotation import ChartAnnotation
from app.narrative_links import CitationResult, check_chart_citations, find_citations
from app.qa import run_qa
from app.report_object import ChartRef


def _chart(chart_id: str, kind: str, direction: str | None) -> ChartRef:
    return ChartRef(
        id=chart_id, section="analytics", caption="Test chart", img="x", chart_type="line",
        metric_paths=["series.analytics.weekly_totals"],
        annotation=ChartAnnotation(kind=kind, x_label="2026-01-19", y_value=100.0,
                                    text="test", direction=direction).to_dict(),
    )


REVENUE_CHART = _chart("analytics-revenue", "largest_delta", "down")  # chart genuinely shows -X%
PEAK_CHART = _chart("analytics-peak", "peak", None)  # no direction to reconcile against


# ---------------------------------------------------------------------------
# find_citations: extraction across every narrative text field
# ---------------------------------------------------------------------------

def test_finds_citation_in_executive_summary():
    narrative = {"executive_summary": "Revenue fell sharply [[chart:analytics-revenue]] this quarter."}
    citations = find_citations(narrative)
    assert len(citations) == 1
    assert citations[0].chart_id == "analytics-revenue"
    assert citations[0].field == "executive_summary"


def test_finds_citations_in_highlights_and_watchouts_and_next_steps():
    narrative = {
        "highlights": ["Revenue grew [[chart:a]]"],
        "watchouts": ["Conversions dropped [[chart:b]]"],
        "next_steps": ["Review channel mix [[chart:c]]"],
    }
    ids = {c.chart_id for c in find_citations(narrative)}
    assert ids == {"a", "b", "c"}


def test_finds_citations_in_section_narrative_and_recommendations():
    narrative = {
        "sections": [{
            "heading": "Web Analytics",
            "narrative": "Sessions declined [[chart:analytics-revenue]].",
            "recommendations": ["Investigate the drop [[chart:analytics-peak]]"],
        }],
    }
    ids = {c.chart_id for c in find_citations(narrative)}
    assert ids == {"analytics-revenue", "analytics-peak"}


def test_no_citations_when_no_markers_present():
    narrative = {"executive_summary": "Revenue fell sharply this quarter.", "sections": []}
    assert find_citations(narrative) == []


def test_multiple_citations_of_the_same_chart_are_each_recorded():
    narrative = {"highlights": ["A [[chart:x]]", "B [[chart:x]]"]}
    assert len(find_citations(narrative)) == 2


# ---------------------------------------------------------------------------
# check_chart_citations: existence + direction reconciliation
# ---------------------------------------------------------------------------

def test_citation_to_a_chart_that_does_not_exist_is_flagged():
    narrative = {"executive_summary": "Revenue grew [[chart:nonexistent]]."}
    result = check_chart_citations(narrative, [REVENUE_CHART])
    assert not result.ok
    assert result.findings[0].status == "unknown_chart_id"


def test_matching_direction_reconciles():
    narrative = {"executive_summary": "Revenue declined sharply [[chart:analytics-revenue]] this month."}
    result = check_chart_citations(narrative, [REVENUE_CHART])
    assert result.ok
    assert result.findings[0].status == "reconciled"


def test_the_exact_injected_mismatch_from_the_exit_criterion_is_caught_as_fail():
    """narrative says +10%, chart shows -10% -- the literal example the
    exit criterion names."""
    narrative = {"executive_summary": "Revenue grew +10% [[chart:analytics-revenue]] this month."}
    result = check_chart_citations(narrative, [REVENUE_CHART])
    assert not result.ok
    finding = result.findings[0]
    assert finding.status == "mismatch"
    assert "up" in finding.reason and "down" in finding.reason


def test_direction_word_mismatch_is_also_caught_not_just_signed_percentages():
    narrative = {"executive_summary": "Revenue increased [[chart:analytics-revenue]] this month."}
    result = check_chart_citations(narrative, [REVENUE_CHART])
    assert result.findings[0].status == "mismatch"


def test_citation_to_a_chart_with_no_directional_annotation_is_not_a_false_mismatch():
    """A peak annotation has no up/down to reconcile against -- citing it
    must not be flagged as wrong just because there's nothing to check."""
    narrative = {"executive_summary": "See the breakdown [[chart:analytics-peak]] for detail."}
    result = check_chart_citations(narrative, [PEAK_CHART])
    assert result.ok
    assert result.findings[0].status == "no_directional_claim"


def test_citation_with_no_directional_claim_in_the_sentence_is_not_a_false_mismatch():
    narrative = {"executive_summary": "See the chart [[chart:analytics-revenue]] for detail."}
    result = check_chart_citations(narrative, [REVENUE_CHART])
    assert result.ok
    assert result.findings[0].status == "no_directional_claim"


def test_no_citations_produces_an_empty_vacuously_ok_result():
    result = check_chart_citations({"executive_summary": "No charts cited here."}, [REVENUE_CHART])
    assert result.ok
    assert result.findings == []


def test_citation_result_to_dict_is_json_serializable():
    import json
    narrative = {"executive_summary": "Revenue grew +10% [[chart:analytics-revenue]]."}
    result = check_chart_citations(narrative, [REVENUE_CHART])
    json.dumps(result.to_dict())  # raises if not serializable


# ---------------------------------------------------------------------------
# Wiring: run_qa/QAReport surfaces citation mismatches as a real FAIL
# ---------------------------------------------------------------------------

def test_run_qa_with_no_citations_and_no_charts_is_vacuously_ok():
    report = {"executive_summary": "Revenue grew this quarter.",  # no [[chart:...]] marker at all
              "highlights": [], "watchouts": [], "sections": [], "next_steps": []}
    result = run_qa(report, {})  # no charts kwarg at all
    assert result.citations.ok
    assert "chart_citations" not in result.failing_checks


def test_run_qa_without_charts_still_flags_a_citation_it_cannot_verify():
    """Omitting charts doesn't silently skip citation checking -- a marker
    in the text with nothing to verify it against is correctly unresolvable,
    not quietly ignored. Fail-safe, not fail-open."""
    report = {"executive_summary": "Revenue grew +10% [[chart:analytics-revenue]].",
              "highlights": [], "watchouts": [], "sections": [], "next_steps": []}
    result = run_qa(report, {})  # no charts kwarg -- nothing to reconcile against
    assert not result.citations.ok
    assert result.citations.findings[0].status == "unknown_chart_id"


def test_run_qa_with_charts_fails_the_badge_on_a_citation_mismatch():
    report = {
        "executive_summary": "",
        "highlights": ["Revenue grew +10% [[chart:analytics-revenue]] this quarter."],
        "watchouts": [], "sections": [], "next_steps": [],
    }
    result = run_qa(report, {}, charts=[REVENUE_CHART])
    assert result.badge == "FAIL"
    assert "chart_citations" in result.failing_checks
    assert not result.citations.ok


def test_run_qa_with_charts_and_a_reconciled_citation_does_not_fail_on_citations():
    report = {
        "executive_summary": "",
        "highlights": ["Revenue declined [[chart:analytics-revenue]] this quarter."],
        "watchouts": [], "sections": [], "next_steps": [],
    }
    result = run_qa(report, {}, charts=[REVENUE_CHART])
    assert "chart_citations" not in result.failing_checks


def test_qa_report_to_dict_includes_chart_citations_and_is_json_serializable():
    import json
    report = {"executive_summary": "Revenue grew +10% [[chart:analytics-revenue]].",
              "highlights": [], "watchouts": [], "sections": [], "next_steps": []}
    result = run_qa(report, {}, charts=[REVENUE_CHART])
    d = result.to_dict()
    assert "chart_citations" in d
    assert d["chart_citations"]["ok"] is False
    json.dumps(d)
