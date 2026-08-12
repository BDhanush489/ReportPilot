"""Tests for agent.py::_validate_report_shape's guardrails against known
local-model failure modes (weak models honor the JSON schema loosely)."""
import pytest

from app.agent import _validate_report_shape

VALID_SECTIONS = ["Web Analytics"]


def _report(**overrides) -> dict:
    base = {
        "report_title": "t", "period_label": "p",
        "executive_summary": "Clean summary with no markup.",
        "highlights": ["Sessions grew 12.3%."],
        "watchouts": [],
        "sections": [{"heading": "Web Analytics", "narrative": "Clean narrative.", "recommendations": ["Do X."]}],
        "next_steps": ["Review this with the team."],
    }
    base.update(overrides)
    return base


def test_clean_report_passes_validation():
    report = _report()
    _validate_report_shape(report, VALID_SECTIONS)  # must not raise


def test_literal_html_tag_in_narrative_is_rejected():
    # Real bug caught in a live generated report: llama3.2:3b wrapped its
    # narrative in literal "<p>...</p>" text, which report.html's Jinja
    # autoescaping then rendered as literal "<p>" characters on the page.
    report = _report(sections=[{
        "heading": "Web Analytics",
        "narrative": "<p>Sessions declined this period.</p>",
        "recommendations": ["Do X."],
    }])
    with pytest.raises(ValueError, match="literal markup"):
        _validate_report_shape(report, VALID_SECTIONS)


def test_literal_html_tag_in_highlights_is_rejected():
    report = _report(highlights=["<p>Sessions grew 12.3%.</p>"])
    with pytest.raises(ValueError, match="literal markup"):
        _validate_report_shape(report, VALID_SECTIONS)


def test_unfilled_placeholder_is_still_rejected():
    report = _report(next_steps=["[list specific issues]"])
    with pytest.raises(ValueError, match="unfilled template placeholder"):
        _validate_report_shape(report, VALID_SECTIONS)


def test_overprecise_number_is_still_rejected():
    report = _report(executive_summary="Revenue grew 12.34567%.")
    with pytest.raises(ValueError, match="suspicious precision"):
        _validate_report_shape(report, VALID_SECTIONS)
