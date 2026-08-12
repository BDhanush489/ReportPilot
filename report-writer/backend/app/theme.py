"""
Single source of visual-language truth for every rendered deliverable
(PDF via charts.py + report.html, and the HTML dashboard via html_dashboard.py).

Before this module existed, charts.py and report.html each hardcoded their
own copy of the same hex values — a report and its dashboard could silently
drift apart the first time someone updated one file and not the other. Every
color and number-format rule lives here exactly once; nothing downstream
should ever write a literal hex code or its own rounding rule again.
"""
from __future__ import annotations

FONT_STACK = '"Helvetica", "Arial", sans-serif'

# --- validated palette (see dataviz skill references/palette.md) -----------
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"
SURFACE = "#fcfcfb"

CATEGORICAL = ["#2a78d6", "#008300", "#e87ba4", "#eda100", "#1baf7a", "#eb6834", "#4a3aa7", "#e34948"]
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95"]
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}

#: fixed identity -> color mapping so a channel/series is always the same
#: color across every chart/card in every deliverable (color follows entity,
#: not rank, and not which renderer happens to be drawing it).
CHANNEL_COLOR_ORDER = ["Organic Search", "Paid Search", "Paid Social", "Email", "Direct", "Referral"]
CHANNEL_COLORS = {name: CATEGORICAL[i % len(CATEGORICAL)] for i, name in enumerate(CHANNEL_COLOR_ORDER)}

#: Insight-card tag -> accent color, matching report.html's .insight-* rules
#: (score uses branding.primary_color instead, set per-report, not fixed here).
INSIGHT_TAG_COLORS = {"opportunity": STATUS["good"], "risk": STATUS["serious"], "efficiency": CATEGORICAL[6]}

#: Draft-narrative / data-quality callout colors (report.html's .draft-flag,
#: .dq-box) — not part of the core palette above, but still a color used in
#: more than one place and worth keeping out of literal template hex.
CALLOUT_WARNING_BG = "#fdf3e3"
CALLOUT_WARNING_BORDER = STATUS["warning"]
CALLOUT_GOOD_TEXT = "#006300"
CALLOUT_WATCH_TEXT = "#a6540f"
DQ_BG = "#f4f7fc"
DQ_BORDER = "#6b8fc9"
DQ_TEXT = "#3d4a5c"
DQ_TITLE = "#2a4a7a"

#: QA badge (PASS / PASS-WITH-WARNINGS / FAIL) tint + text, one pair per
#: tier. Solid pre-mixed colors, deliberately NOT an 8-digit #RRGGBBAA alpha
#: color on top of STATUS — xhtml2pdf (the PDF renderer) doesn't support
#: CSS4 8-digit hex and renders it fully opaque, which made a first attempt
#: at this badge render as same-color text on same-color background,
#: effectively invisible. A browser (the dashboard) would have tolerated
#: the alpha trick fine; the PDF didn't, so both use these solid tokens.
BADGE_PASS_BG = "#e8f5e9"
BADGE_PASS_BORDER = STATUS["good"]
BADGE_PASS_TEXT = CALLOUT_GOOD_TEXT
BADGE_WARNING_BG = CALLOUT_WARNING_BG
BADGE_WARNING_BORDER = CALLOUT_WARNING_BORDER
BADGE_WARNING_TEXT = CALLOUT_WATCH_TEXT
BADGE_FAIL_BG = "#fbe9e7"
BADGE_FAIL_BORDER = STATUS["critical"]
BADGE_FAIL_TEXT = "#a01f1f"


def channel_color(name: str, fallback_index: int = 0) -> str:
    return CHANNEL_COLORS.get(name, CATEGORICAL[fallback_index % len(CATEGORICAL)])


# --- number formatting -----------------------------------------------------
# The one place display-formatting rules live. Everything downstream (PDF
# narrative/insights, the HTML dashboard's KPI cards) that needs to show a
# dollar amount, a percent, or a plain count calls one of these instead of
# rolling its own f-string — so "$45,231.50" and "45,231.50" never both
# appear as the "same" number in different deliverables.

def format_currency(value: float, decimals: int = 0) -> str:
    return f"${value:,.{decimals}f}"


def format_percent(value: float, decimals: int = 1) -> str:
    return f"{value:.{decimals}f}%"


def format_count(value: float) -> str:
    return f"{value:,.0f}"


def to_template_context(font_family: str | None = None) -> dict:
    """Everything a Jinja template or an embedded JSON blob needs to render
    with these tokens, without importing this module's Python names directly.

    font_family: W1 white-label override -- a client-supplied CSS font stack
    (branding.font_family) replaces the default FONT_STACK when given. Kept
    as a parameter here (not resolved by each caller separately) so
    report_builder.py's PDF path and html_dashboard.py's dashboard path
    can't drift on how the override is applied."""
    return {
        "font_stack": font_family or FONT_STACK,
        "ink_primary": INK_PRIMARY,
        "ink_secondary": INK_SECONDARY,
        "ink_muted": INK_MUTED,
        "gridline": GRIDLINE,
        "baseline": BASELINE,
        "surface": SURFACE,
        "categorical": CATEGORICAL,
        "seq_blue": SEQ_BLUE,
        "status": STATUS,
        "channel_colors": CHANNEL_COLORS,
        "insight_tag_colors": INSIGHT_TAG_COLORS,
        "callout_warning_bg": CALLOUT_WARNING_BG,
        "callout_warning_border": CALLOUT_WARNING_BORDER,
        "callout_good_text": CALLOUT_GOOD_TEXT,
        "callout_watch_text": CALLOUT_WATCH_TEXT,
        "dq_bg": DQ_BG,
        "dq_border": DQ_BORDER,
        "dq_text": DQ_TEXT,
        #: keyed by the exact qa.badge string, so a template can do
        #: theme.badge[qa.badge].bg instead of an if/elif chain per renderer.
        "badge": {
            "PASS": {"bg": BADGE_PASS_BG, "border": BADGE_PASS_BORDER, "text": BADGE_PASS_TEXT},
            "PASS-WITH-WARNINGS": {"bg": BADGE_WARNING_BG, "border": BADGE_WARNING_BORDER, "text": BADGE_WARNING_TEXT},
            "FAIL": {"bg": BADGE_FAIL_BG, "border": BADGE_FAIL_BORDER, "text": BADGE_FAIL_TEXT},
        },
        "dq_title": DQ_TITLE,
    }
