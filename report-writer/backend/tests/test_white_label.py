"""
Tests for W1 — full white-label. GOAL: logo, colors, fonts, cover page,
footer, signature, disclaimer all from one token source; zero ReportPilot
branding in any output format when white-label is on.

Real gaps this closes (found by an audit before this node started):
  - logo_data_uri was accepted by the form and used ONLY in the Power BI
    export -- never rendered in the PDF or the HTML dashboard at all.
  - font_family, footer_text, signature_name/title, disclaimer_text did not
    exist as branding fields anywhere.
  - The PDF's default footer hardcoded "...'s AI Report Writer" -- a
    product self-reference no agency could turn off.

Uses render_pdf_from_object / html_dashboard.build_dashboard directly
(hand-built ReportObject, no live LLM call) and asserts on the returned
HTML string -- fast, and this is presence/absence-of-branding-text testing,
not visual layout, so a real browser isn't needed the way html_dashboard's
existing interaction tests need one.
"""
from app import html_dashboard, report_builder, theme
from app.report_object import ChartRef, Period, ReportObject, SourceInfo

METRICS = {
    "analytics": {"totals": {"sessions": 1000, "revenue_usd": 5000.0, "conversion_rate": 3.0}},
    "period_label": "2026-01-01 to 2026-06-30",
}
NARRATIVE = {
    "report_title": "Aurora Home Goods — Performance Report",
    "period_label": "2026-01-01 to 2026-06-30",
    "_ai_generated": False,
    "executive_summary": "Sessions grew to 1,000, driving $5,000 in revenue.",
    "highlights": ["Solid growth."], "watchouts": [],
    "sections": [{"heading": "Web Analytics", "narrative": "Revenue reached $5,000.", "recommendations": []}],
    "next_steps": [],
}
QA_PASS = {
    "badge": "PASS", "failing_checks": [],
    "traceability": {"ok": True, "numbers_checked": 1, "fail": [], "warnings": []},
    "aggregation_sanity": {"ok": True, "mismatches": [], "inconclusive_sources": []},
    "unsupported_claims": {"ok": True, "claims_checked": 1, "unlinked": []},
}
_TINY_PNG_DATA_URI = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _obj(branding: dict) -> ReportObject:
    return ReportObject(
        report_id="wl-test", period=Period(label=METRICS["period_label"]),
        sources={"analytics": SourceInfo(row_count=10, sha256="a" * 64)},
        metrics=METRICS, series={}, charts=[], narrative=NARRATIVE, qa=QA_PASS,
        branding=branding, section_order=["analytics"],
    )


_MINIMAL_BRANDING = {"agency_name": "Northlight", "client_name": "Aurora",
                      "primary_color": "#2a78d6", "accent_color": "#eda100"}

_FULL_WHITE_LABEL_BRANDING = {
    **_MINIMAL_BRANDING,
    "logo_data_uri": _TINY_PNG_DATA_URI,
    "font_family": '"Georgia", serif',
    "footer_text": "Prepared exclusively for Aurora Home Goods by Northlight Growth Partners.",
    "signature_name": "Jamie Rivera",
    "signature_title": "Senior Analytics Consultant, Northlight",
    "disclaimer_text": "This report is confidential and intended solely for the named recipient.",
}


# ---------------------------------------------------------------------------
# PDF (report.html via render_pdf_from_object)
# ---------------------------------------------------------------------------

def test_logo_is_never_rendered_when_not_provided():
    """The .cover-logo CSS rule is always present in <style>; what must be
    absent is the actual <img> element it would style."""
    html, _pdf = report_builder.render_pdf_from_object(_obj(_MINIMAL_BRANDING))
    assert '<img class="cover-logo"' not in html


def test_logo_is_rendered_on_the_pdf_cover_when_provided():
    html, _pdf = report_builder.render_pdf_from_object(_obj(_FULL_WHITE_LABEL_BRANDING))
    assert 'class="cover-logo"' in html
    assert _TINY_PNG_DATA_URI in html


def test_font_family_override_reaches_the_pdf_css():
    html, _pdf = report_builder.render_pdf_from_object(_obj(_FULL_WHITE_LABEL_BRANDING))
    assert '"Georgia", serif' in html
    assert theme.FONT_STACK not in html


def test_font_defaults_to_the_product_stack_when_not_overridden():
    html, _pdf = report_builder.render_pdf_from_object(_obj(_MINIMAL_BRANDING))
    assert theme.FONT_STACK in html


def test_default_footer_no_longer_names_the_product_as_ai_report_writer():
    """The real bug this closes: the OLD default footer said "...'s AI
    Report Writer" -- a product self-reference with no way to turn it off."""
    html, _pdf = report_builder.render_pdf_from_object(_obj(_MINIMAL_BRANDING))
    assert "AI Report Writer" not in html
    assert "Northlight" in html  # the agency's own name is still there


def test_custom_footer_text_replaces_the_default():
    html, _pdf = report_builder.render_pdf_from_object(_obj(_FULL_WHITE_LABEL_BRANDING))
    assert "Prepared exclusively for Aurora Home Goods by Northlight Growth Partners." in html
    assert "Figures are computed directly from the uploaded source data." not in html


def test_signature_block_only_renders_when_a_name_is_given():
    html_without, _ = report_builder.render_pdf_from_object(_obj(_MINIMAL_BRANDING))
    assert '<div class="signature-block">' not in html_without

    html_with, _ = report_builder.render_pdf_from_object(_obj(_FULL_WHITE_LABEL_BRANDING))
    assert "Jamie Rivera" in html_with
    assert "Senior Analytics Consultant, Northlight" in html_with


def test_disclaimer_only_renders_when_provided():
    html_without, _ = report_builder.render_pdf_from_object(_obj(_MINIMAL_BRANDING))
    assert '<div class="disclaimer">' not in html_without

    html_with, _ = report_builder.render_pdf_from_object(_obj(_FULL_WHITE_LABEL_BRANDING))
    assert "This report is confidential" in html_with


def test_zero_reportpilot_branding_anywhere_in_the_pdf():
    html, _pdf = report_builder.render_pdf_from_object(_obj(_FULL_WHITE_LABEL_BRANDING))
    assert "ReportPilot" not in html


# ---------------------------------------------------------------------------
# Dashboard (dashboard.html via html_dashboard.build_dashboard)
# ---------------------------------------------------------------------------

def test_logo_is_rendered_in_the_dashboard_header_when_provided():
    html = html_dashboard.build_dashboard(_obj(_FULL_WHITE_LABEL_BRANDING))
    assert _TINY_PNG_DATA_URI in html


def test_logo_is_never_rendered_in_the_dashboard_when_not_provided():
    html = html_dashboard.build_dashboard(_obj(_MINIMAL_BRANDING))
    assert "logo" not in html.lower()


def test_dashboard_font_family_override_reaches_the_css():
    html = html_dashboard.build_dashboard(_obj(_FULL_WHITE_LABEL_BRANDING))
    assert '"Georgia", serif' in html


def test_dashboard_disclaimer_only_renders_when_provided():
    html_without = html_dashboard.build_dashboard(_obj(_MINIMAL_BRANDING))
    assert "confidential" not in html_without.lower()

    html_with = html_dashboard.build_dashboard(_obj(_FULL_WHITE_LABEL_BRANDING))
    assert "This report is confidential" in html_with


def test_zero_reportpilot_branding_anywhere_in_the_dashboard():
    html = html_dashboard.build_dashboard(_obj(_FULL_WHITE_LABEL_BRANDING))
    assert "ReportPilot" not in html


# ---------------------------------------------------------------------------
# theme.py: font override is additive/optional, doesn't touch the palette
# ---------------------------------------------------------------------------

def test_theme_font_override_leaves_every_other_token_untouched():
    default_ctx = theme.to_template_context()
    overridden_ctx = theme.to_template_context('"Georgia", serif')
    assert overridden_ctx["font_stack"] == '"Georgia", serif'
    for key in default_ctx:
        if key != "font_stack":
            assert overridden_ctx[key] == default_ctx[key]


def test_theme_context_defaults_when_no_override_given():
    assert theme.to_template_context()["font_stack"] == theme.FONT_STACK
    assert theme.to_template_context(None)["font_stack"] == theme.FONT_STACK
