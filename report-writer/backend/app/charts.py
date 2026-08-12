"""
Chart rendering for the PDF report.

Static print charts (matplotlib -> base64 PNG), styled with the validated
categorical / sequential / status palette from the dataviz skill so every
report a customer generates is colorblind-safe and legible by construction —
never hand-picked per request. Palette itself lives in theme.py — the single
source shared with report.html and the HTML dashboard.
"""
from __future__ import annotations

import base64
from io import BytesIO

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd

from .theme import (
    BASELINE,
    CATEGORICAL,
    GRIDLINE,
    INK_MUTED,
    INK_PRIMARY,
    INK_SECONDARY,
    SEQ_BLUE,
    STATUS,
    SURFACE,
    channel_color,
)


def _style_axes(ax, show_x_grid=False):
    ax.set_facecolor(SURFACE)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.spines["bottom"].set_linewidth(1)
    ax.tick_params(colors=INK_MUTED, labelsize=9)
    ax.yaxis.grid(True, color=GRIDLINE, linewidth=1)
    ax.set_axisbelow(True)
    if not show_x_grid:
        ax.xaxis.grid(False)
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_color(INK_MUTED)


def _fig_to_base64(fig) -> str:
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=170, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def weekly_sessions_by_channel_chart(weekly_df: pd.DataFrame, channels: list[str]) -> str:
    """Multi-line chart, one line per channel, fixed identity colors + a legend (never color alone)."""
    fig, ax = plt.subplots(figsize=(7.6, 3.6))
    for ch in channels:
        series = weekly_df[weekly_df["channel_group"] == ch].sort_values("week")
        if series.empty:
            continue
        ax.plot(series["week"], series["sessions"], color=channel_color(ch), linewidth=2,
                solid_capstyle="round", label=ch)
    ax.set_title("Weekly sessions by channel", fontsize=11, color=INK_PRIMARY, loc="left", pad=12)
    ax.set_ylabel("Sessions", fontsize=9, color=INK_MUTED)
    _style_axes(ax)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
    fig.autofmt_xdate(rotation=30, ha="right")
    legend = ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.32), ncol=min(len(channels), 3),
                        frameon=False, fontsize=8.5, handlelength=1.4, columnspacing=1.2)
    for text in legend.get_texts():
        text.set_color(INK_SECONDARY)
    fig.tight_layout()
    return _fig_to_base64(fig)


def revenue_trend_chart(weekly_totals: pd.DataFrame) -> str:
    """Single-series area chart (sequential blue) — one measure, no legend needed."""
    fig, ax = plt.subplots(figsize=(7.6, 2.8))
    ax.fill_between(weekly_totals["week"], weekly_totals["revenue_usd"], color=SEQ_BLUE[1], alpha=0.55, linewidth=0)
    ax.plot(weekly_totals["week"], weekly_totals["revenue_usd"], color=SEQ_BLUE[4], linewidth=2)
    ax.set_title("Weekly revenue", fontsize=11, color=INK_PRIMARY, loc="left", pad=12)
    _style_axes(ax)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${int(v):,}"))
    fig.autofmt_xdate(rotation=30, ha="right")
    return _fig_to_base64(fig)


def channel_revenue_bar_chart(by_channel: list[dict]) -> str:
    """Horizontal bars, fixed identity color per channel, direct value labels."""
    rows = sorted(by_channel, key=lambda r: r["revenue_usd"], reverse=True)
    names = [r["channel"] for r in rows]
    values = [r["revenue_usd"] for r in rows]
    colors = [channel_color(n, i) for i, n in enumerate(names)]

    fig, ax = plt.subplots(figsize=(7.6, 0.5 * len(names) + 1))
    bars = ax.barh(names, values, color=colors, height=0.55)
    ax.invert_yaxis()
    for bar, v in zip(bars, values):
        ax.annotate(f"${v:,.0f}", xy=(bar.get_width(), bar.get_y() + bar.get_height() / 2),
                    xytext=(6, 0), textcoords="offset points", va="center", fontsize=9, color=INK_SECONDARY)
    ax.set_title("Revenue by channel", fontsize=11, color=INK_PRIMARY, loc="left", pad=12)
    ax.xaxis.set_visible(False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_facecolor(SURFACE)
    for label in ax.get_yticklabels():
        label.set_color(INK_PRIMARY)
        label.set_fontsize(9.5)
    return _fig_to_base64(fig)


def conversion_rate_by_channel_chart(by_channel: list[dict]) -> str:
    """Efficiency view alongside channel_revenue_bar_chart's volume view —
    a channel can lead on revenue and still convert worse than a smaller one."""
    rows = sorted(by_channel, key=lambda r: r["conversion_rate"], reverse=True)
    names = [r["channel"] for r in rows]
    values = [r["conversion_rate"] for r in rows]
    colors = [channel_color(n, i) for i, n in enumerate(names)]

    fig, ax = plt.subplots(figsize=(7.6, 0.5 * len(names) + 1))
    bars = ax.barh(names, values, color=colors, height=0.55)
    ax.invert_yaxis()
    for bar, v in zip(bars, values):
        ax.annotate(f"{v:.1f}%", xy=(bar.get_width(), bar.get_y() + bar.get_height() / 2),
                    xytext=(6, 0), textcoords="offset points", va="center", fontsize=9, color=INK_SECONDARY)
    ax.set_title("Conversion rate by channel", fontsize=11, color=INK_PRIMARY, loc="left", pad=12)
    ax.xaxis.set_visible(False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_facecolor(SURFACE)
    for label in ax.get_yticklabels():
        label.set_color(INK_PRIMARY)
        label.set_fontsize(9.5)
    return _fig_to_base64(fig)


def _luminance_text_color(hex_color: str) -> str:
    """WCAG relative luminance -> readable text color for that specific
    wedge, rather than assuming white always contrasts. It doesn't: white
    text on the palette's amber ("#eda100") wedge is marginal at best."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)
    return INK_PRIMARY if luminance > 0.45 else "#ffffff"


def _donut_chart(labels: list[str], values: list[float], title: str, center_label: str) -> str:
    """Shared rendering for every donut in this app. Previously
    device_split_pie_chart and revenue_by_lead_source_pie_chart were two
    byte-identical copies of this same function — consolidated so a fix
    (like the per-wedge contrast below) only has to happen once."""
    colors = [CATEGORICAL[i % len(CATEGORICAL)] for i in range(len(labels))]

    fig, ax = plt.subplots(figsize=(7.6, 4.0))
    wedges, _texts, autotexts = ax.pie(
        values, colors=colors, autopct=lambda p: f"{p:.0f}%" if p >= 4 else "",
        startangle=90, pctdistance=0.78, radius=1.2,
        wedgeprops={"width": 0.42, "edgecolor": SURFACE, "linewidth": 2.5},
        textprops={"fontsize": 9.5, "fontweight": "bold"},
    )
    for autotext, color in zip(autotexts, colors):
        autotext.set_color(_luminance_text_color(color))

    # Center total -- a donut's hole is otherwise wasted space, and the
    # total is exactly the number a reader looks for right after the split.
    ax.text(0, 0, center_label, ha="center", va="center", fontsize=12.5,
            fontweight="bold", color=INK_PRIMARY, linespacing=1.4)

    ax.legend(wedges, labels, loc="center left", bbox_to_anchor=(1.05, 0.5), frameon=False,
              labelcolor=INK_SECONDARY, fontsize=9.5)
    ax.set_title(title, fontsize=11, color=INK_PRIMARY, loc="left", pad=12)
    fig.tight_layout()
    return _fig_to_base64(fig)


def device_split_pie_chart(by_device: list[dict]) -> str:
    """Session share by device category. Device categories are always a
    handful (desktop/mobile/tablet), exactly the low-cardinality case a
    donut reads well for."""
    rows = sorted(by_device, key=lambda r: r["sessions"], reverse=True)
    labels = [str(r["device_category"]).title() for r in rows]
    values = [r["sessions"] for r in rows]
    total = sum(values)
    return _donut_chart(labels, values, "Sessions by device", f"{total:,.0f}\nsessions")


def seo_health_bar_chart(severity_counts: dict) -> str:
    """Single stacked bar showing ok/warning/critical share, using fixed status colors."""
    order = ["good", "warning", "critical"]
    label_map = {"good": "Healthy", "warning": "Needs attention", "critical": "Critical"}
    total = sum(severity_counts.get(k, 0) for k in order) or 1

    fig, ax = plt.subplots(figsize=(7.6, 1.3))
    left = 0
    for key in order:
        val = severity_counts.get(key, 0)
        pct = val / total * 100
        ax.barh([0], [pct], left=left, color=STATUS[key if key != "good" else "good"], height=0.5,
                edgecolor=SURFACE, linewidth=2)
        if pct > 6:
            ax.annotate(f"{label_map[key]}\n{val} pages", xy=(left + pct / 2, 0), ha="center", va="center",
                        fontsize=8.5, color="#ffffff" if key != "warning" else INK_PRIMARY)
        left += pct
    ax.set_xlim(0, 100)
    ax.set_ylim(-0.5, 0.5)
    ax.axis("off")
    ax.set_title("Site health — pages crawled", fontsize=11, color=INK_PRIMARY, loc="left", pad=4)
    return _fig_to_base64(fig)


def top_issues_bar_chart(issue_counts: list[tuple]) -> str:
    """Single-measure ranked bar — one flat hue, no legend needed."""
    issue_counts = issue_counts[:8]
    labels = [k.replace("_", " ").title() for k, _ in issue_counts]
    values = [v for _, v in issue_counts]

    fig, ax = plt.subplots(figsize=(7.6, 0.42 * len(labels) + 1))
    bars = ax.barh(labels, values, color=CATEGORICAL[0], height=0.55)
    ax.invert_yaxis()
    for bar, v in zip(bars, values):
        ax.annotate(f"{v}", xy=(bar.get_width(), bar.get_y() + bar.get_height() / 2),
                    xytext=(6, 0), textcoords="offset points", va="center", fontsize=9, color=INK_SECONDARY)
    ax.set_title("Most common technical SEO issues", fontsize=11, color=INK_PRIMARY, loc="left", pad=12)
    ax.xaxis.set_visible(False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_facecolor(SURFACE)
    for label in ax.get_yticklabels():
        label.set_color(INK_PRIMARY)
        label.set_fontsize(9.5)
    return _fig_to_base64(fig)


def monthly_revenue_and_winrate_chart(monthly_df: pd.DataFrame) -> str:
    """Two stacked small-multiple panels sharing an x-axis (never a dual-axis chart)."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(7.6, 4.4), sharex=True, height_ratios=[2, 1])

    ax1.bar(monthly_df["month"], monthly_df["revenue_usd"], color=CATEGORICAL[0], width=0.55)
    ax1.set_title("Monthly revenue (closed-won)", fontsize=11, color=INK_PRIMARY, loc="left", pad=10)
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${int(v):,}"))
    _style_axes(ax1)

    ax2.plot(monthly_df["month"], monthly_df["win_rate"] * 100, color=CATEGORICAL[5], linewidth=2, marker="o",
             markersize=5, markerfacecolor=CATEGORICAL[5], markeredgecolor=SURFACE)
    ax2.set_title("Win rate", fontsize=11, color=INK_PRIMARY, loc="left", pad=10)
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    _style_axes(ax2)
    fig.autofmt_xdate(rotation=30, ha="right")
    fig.tight_layout()
    return _fig_to_base64(fig)


def revenue_by_rep_chart(by_rep: list[dict]) -> str:
    rows = sorted(by_rep, key=lambda r: r["revenue_usd"], reverse=True)
    names = [r["sales_rep"] for r in rows]
    values = [r["revenue_usd"] for r in rows]

    fig, ax = plt.subplots(figsize=(7.6, 0.5 * len(names) + 1))
    bars = ax.barh(names, values, color=CATEGORICAL[0], height=0.55)
    ax.invert_yaxis()
    for bar, v in zip(bars, values):
        ax.annotate(f"${v:,.0f}", xy=(bar.get_width(), bar.get_y() + bar.get_height() / 2),
                    xytext=(6, 0), textcoords="offset points", va="center", fontsize=9, color=INK_SECONDARY)
    ax.set_title("Revenue by sales rep", fontsize=11, color=INK_PRIMARY, loc="left", pad=12)
    ax.xaxis.set_visible(False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_facecolor(SURFACE)
    for label in ax.get_yticklabels():
        label.set_color(INK_PRIMARY)
        label.set_fontsize(9.5)
    return _fig_to_base64(fig)


def revenue_by_lead_source_pie_chart(by_lead_source: list[dict]) -> str:
    """Revenue share by lead source. Lead sources are a small, fixed set
    (typically <=8), the same low-cardinality case device_split_pie_chart is for."""
    rows = sorted(by_lead_source, key=lambda r: r["revenue_usd"], reverse=True)
    labels = [str(r["lead_source"]) for r in rows]
    values = [r["revenue_usd"] for r in rows]
    total = sum(values)
    return _donut_chart(labels, values, "Revenue by lead source", f"${total:,.0f}\ntotal")


def revenue_by_product_bar_chart(by_product: list[dict]) -> str:
    rows = sorted(by_product, key=lambda r: r["revenue_usd"], reverse=True)
    names = [r["product"] for r in rows]
    values = [r["revenue_usd"] for r in rows]

    fig, ax = plt.subplots(figsize=(7.6, 0.5 * len(names) + 1))
    bars = ax.barh(names, values, color=CATEGORICAL[1], height=0.55)
    ax.invert_yaxis()
    for bar, v in zip(bars, values):
        ax.annotate(f"${v:,.0f}", xy=(bar.get_width(), bar.get_y() + bar.get_height() / 2),
                    xytext=(6, 0), textcoords="offset points", va="center", fontsize=9, color=INK_SECONDARY)
    ax.set_title("Revenue by product", fontsize=11, color=INK_PRIMARY, loc="left", pad=12)
    ax.xaxis.set_visible(False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_facecolor(SURFACE)
    for label in ax.get_yticklabels():
        label.set_color(INK_PRIMARY)
        label.set_fontsize(9.5)
    return _fig_to_base64(fig)
