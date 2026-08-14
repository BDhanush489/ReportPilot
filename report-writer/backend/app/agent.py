"""
The report-writing agent.

Design choice: all numbers are computed deterministically by app/metrics.py
before Claude ever sees them. Claude's job is narrowly scoped to synthesis —
deciding what's worth saying about numbers it is handed, prioritizing what
matters to the client, and writing it in clean, client-ready prose. It is
never asked to compute or invent a figure. This is what makes it safe to hand
the output straight to an agency's client without a human fact-check pass.

Provider selection, in order, all automatic:
  1. Claude (claude-haiku-4-5) if ANTHROPIC_API_KEY is set AND today's
     global usage cap isn't exhausted (see app/ai_usage.py — a shared
     budget-protection cap across every tenant, not per-tenant).
  2. A local Ollama model (OLLAMA_MODEL, default llama3.2:3b) if an Ollama
     server is reachable at OLLAMA_BASE_URL — free, no API key, runs on this
     machine, and NOT subject to the cap above (nothing to protect, it's
     free). Weaker prose than Claude, but a real model, not a template.
  3. A deterministic template narrative — always available, zero setup.

Whichever tier actually produces the report, the numbers are identical:
every figure comes from app/metrics.py, never from the model.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

_OVERPRECISE_NUMBER = re.compile(r"\d+\.\d{3,}")
#: Weaker local models occasionally wrap narrative text in literal markup
#: ("<p>...</p>") instead of writing plain prose — report.html's Jinja
#: autoescaping then renders that as literal "<p>" characters on the page
#: rather than stripping it, since it's just text content as far as the
#: template is concerned. Caught live in a real generated report.
_HTML_TAG = re.compile(r"</?[a-zA-Z][^>]*>")

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "report_title": {"type": "string"},
        "period_label": {"type": "string"},
        "executive_summary": {"type": "string"},
        "highlights": {"type": "array", "items": {"type": "string"}},
        "watchouts": {"type": "array", "items": {"type": "string"}},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "narrative": {"type": "string"},
                    "recommendations": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["heading", "narrative", "recommendations"],
                "additionalProperties": False,
            },
        },
        "next_steps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["report_title", "period_label", "executive_summary", "highlights",
                 "watchouts", "sections", "next_steps"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are a senior analyst at a digital marketing/growth agency, writing a monthly \
client performance report. You will be given a JSON object of pre-computed metrics for one or more \
data sources (web analytics, technical SEO audit, sales pipeline).

Hard rules:
- Every number you write in the report (percentages, dollar amounts, counts) MUST come directly from \
the JSON you are given. Never calculate a new number, extrapolate a figure, or invent a statistic.
- If the JSON doesn't contain a number you'd want, don't invent it — describe the trend qualitatively instead.
- Write for the agency's client — a business stakeholder, not a technical/marketing peer. No jargon \
without a one-clause explanation.
- Be direct about bad news as well as good news. A trustworthy report names risks plainly.
- Produce exactly one entry in "sections" per data source given, in the order listed, using the \
provided section heading for each.
- Tone: confident, concise, consultative. Avoid filler phrases like "It's important to note that."
- When more than one data source is present, actively look for connections between them (e.g. a \
decline in organic sessions in the analytics data lining up with critical technical issues in the SEO \
audit, or a lead source with strong web traffic but weak sales close rates) and call the connection out \
explicitly — that cross-source synthesis is the most valuable thing you can add to this report.
- Any change/trend figure (e.g. "sessions_change_pct") compares the first half of the given period \
against the second half of that SAME period — there is no other period in the data. Never describe it \
as "year-over-year," "vs. last year/quarter," or any other comparison period; say "versus the first \
half of the period" or similar, matching what the data actually represents.
- Copy every number exactly as it already appears in the JSON (same rounding, same number of decimal \
places). Never add decimal precision that isn't already there.
- Write only the sections listed in "sections_requested_in_order" — nothing else. Do not add extra \
sections (e.g. "Recommendations," "Device Performance") beyond that list, and never leave a bracketed \
placeholder like "[list specific issues]" in the output — every sentence must be fully written out.
"""


OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")


#: T1 — tone is a template-spec field (see template_specs.py), threaded down
#: to whichever provider actually writes the prose. It changes REGISTER
#: ONLY: how much is said and how technically, never a number, since every
#: figure still comes from metrics_payload untouched. "manager" is an empty
#: override because SYSTEM_PROMPT above was already written in that
#: register — leaving it empty keeps live-LLM output byte-identical to
#: pre-T1 behavior for the default template (tone="manager").
_TONE_GUIDANCE = {
    "executive": (
        "\nTone override -- EXECUTIVE: the reader is a C-suite stakeholder with two minutes, not a "
        "channel manager. Lead the executive summary with the single most important number. Keep each "
        "section narrative to 2-3 sentences. Skip operational detail a channel owner would want but a "
        "C-suite reader wouldn't (e.g. don't enumerate every technical issue by name)."
    ),
    "manager": "",
    "specialist": (
        "\nTone override -- SPECIALIST: the reader runs this channel or practice day to day. Section "
        "narratives may go into more operational detail than usual (name the specific channel/rep/page a "
        "number refers to, not just the aggregate) and may use domain terminology without a lay "
        "explanation -- still never a number that isn't already in the JSON."
    ),
}


def _system_prompt_for(tone: str, prompt_guidance: str = "") -> str:
    """prompt_guidance: P — an industry pack's extra instruction (e.g. "this
    client is a local service business; frame sessions/conversions in terms
    of calls and booked jobs, not raw traffic"), appended verbatim. Same
    hard rule as everything else in SYSTEM_PROMPT still applies -- guidance
    can change what's emphasized or how a number is framed, never invent one."""
    prompt = SYSTEM_PROMPT + _TONE_GUIDANCE.get(tone, "")
    if prompt_guidance:
        prompt += f"\nIndustry guidance for this client: {prompt_guidance}"
    return prompt


def _client():
    import anthropic
    return anthropic.Anthropic()


def _build_user_prompt(metrics_payload: dict, branding: dict, sections_requested: list[str], tone: str) -> str:
    return json.dumps({
        "client_name": branding.get("client_name") or "the client",
        "agency_name": branding.get("agency_name") or "the agency",
        "period_label": metrics_payload.get("period_label", ""),
        "sections_requested_in_order": sections_requested,
        "tone": tone,
        "metrics": metrics_payload,
    }, default=str)


def _iter_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for v in value:
            yield from _iter_strings(v)
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_strings(v)


def _validate_report_shape(report: dict, sections_requested: list[str]) -> None:
    """Structural + sanity check. Local models honor 'format' and instructions
    loosely — verify the required keys made it, that sections match exactly
    what was requested (smaller models like to bolt on extra sections), and
    that no unfilled template placeholder ("[list specific issues]") leaked
    into client-facing prose. Anything that fails here falls back to the safe
    deterministic template rather than reaching a client."""
    required = ["report_title", "period_label", "executive_summary", "highlights",
                "watchouts", "sections", "next_steps"]
    for key in required:
        if key not in report:
            raise ValueError(f"model output missing required key: {key}")
    if not isinstance(report["sections"], list) or len(report["sections"]) < len(sections_requested):
        raise ValueError("model output has fewer sections than requested")
    # watchouts is legitimately empty when there's genuinely nothing to flag —
    # highlights and next_steps never should be.
    for key in ("highlights", "next_steps"):
        if not isinstance(report[key], list) or not report[key]:
            raise ValueError(f"model output has an empty {key} list")

    # Some local models append extra sections beyond what was asked for —
    # keep only the first len(sections_requested), and force headings to the
    # exact requested labels so they can't drift or get invented.
    report["sections"] = report["sections"][:len(sections_requested)]
    for section, label in zip(report["sections"], sections_requested):
        for key in ("heading", "narrative", "recommendations"):
            if key not in section:
                raise ValueError(f"section missing required key: {key}")
        section["heading"] = label

    for s in _iter_strings(report):
        if "[" in s:
            raise ValueError("model output contains an unfilled template placeholder")
        if _HTML_TAG.search(s):
            raise ValueError(f"model wrote literal markup instead of plain prose: {s!r}")
        if _OVERPRECISE_NUMBER.search(s):
            # every number in metrics.py is pre-rounded to 1-2 decimals; 3+ decimal
            # digits means the model re-derived a figure instead of copying it —
            # exactly the "never invent a number" rule this product exists to enforce.
            raise ValueError(f"model wrote a number with suspicious precision: {s!r}")


def _ollama_available() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen(f"{OLLAMA_BASE_URL}/api/tags", timeout=1.5)
        return True
    except Exception:  # noqa: BLE001
        return False


def _generate_via_anthropic(metrics_payload: dict, branding: dict, sections_requested: list[str],
                             tone: str, prompt_guidance: str = "") -> dict:
    client = _client()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8000,
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": REPORT_SCHEMA}},
        system=_system_prompt_for(tone, prompt_guidance),
        messages=[{"role": "user",
                   "content": _build_user_prompt(metrics_payload, branding, sections_requested, tone)}],
    )
    text_block = next(b.text for b in response.content if b.type == "text")
    report = json.loads(text_block)
    _validate_report_shape(report, sections_requested)
    report["_ai_generated"] = True
    report["_ai_provider"] = "Claude Haiku 4.5"
    return report


def _generate_via_ollama(metrics_payload: dict, branding: dict, sections_requested: list[str], tone: str,
                          prompt_guidance: str = "") -> dict:
    import urllib.request

    body = json.dumps({
        "model": OLLAMA_MODEL,
        "stream": False,
        "options": {"temperature": 0.3},
        "messages": [
            {"role": "system", "content": _system_prompt_for(tone, prompt_guidance)},
            {"role": "user", "content": _build_user_prompt(metrics_payload, branding, sections_requested, tone)},
        ],
        "format": REPORT_SCHEMA,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/chat", data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    # local CPU inference on a multi-section prompt can take minutes — give it room.
    with urllib.request.urlopen(req, timeout=300) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    report = json.loads(payload["message"]["content"])
    _validate_report_shape(report, sections_requested)
    report["_ai_generated"] = True
    report["_ai_provider"] = f"local Ollama ({OLLAMA_MODEL})"
    return report


def generate_report(metrics_payload: dict, branding: dict, sections_requested: list[str],
                     tone: str = "manager", prompt_guidance: str = "", claude_allowed: bool = True) -> dict:
    """tone: "executive" | "manager" | "specialist" (see template_specs.py, T1)
    -- register only. Every figure still comes from metrics_payload untouched
    regardless of tone; see test_template_specs.py's identical-figures test.
    prompt_guidance: P — an industry pack's extra system-prompt instruction,
    empty for general-purpose templates. Only reaches a live model (Claude/
    Ollama); the deterministic fallback has no prompt to guide.
    claude_allowed: resolved by the caller (report_builder.py) via
    app/ai_usage.py's global daily cap BEFORE this function is ever called —
    this module stays DB-free/pure on purpose (see its own module docstring
    and existing test suite, which calls this directly with no DB fixture).
    Defaults True so every existing call site/test is unaffected unless it
    explicitly opts in to the cap."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    errors: list[str] = []
    limit_reached = False

    if api_key:
        if claude_allowed:
            try:
                return _generate_via_anthropic(metrics_payload, branding, sections_requested, tone, prompt_guidance)
            except Exception as exc:  # noqa: BLE001 - demo-grade: never hard-fail report generation
                errors.append(f"Claude: {exc}")
        else:
            limit_reached = True

    if _ollama_available():
        # small local models don't reliably pass _validate_report_shape on the
        # first try — one retry meaningfully raises the odds of a clean pass
        # without user-visible cost (worst case, ~2x latency before falling
        # back to the template anyway).
        for attempt in range(2):
            try:
                return _generate_via_ollama(metrics_payload, branding, sections_requested, tone, prompt_guidance)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Ollama attempt {attempt + 1}: {exc}")

    fallback = _fallback_report(metrics_payload, branding, sections_requested, tone)
    if errors:
        fallback["_ai_error"] = "; ".join(errors)
    if limit_reached:
        fallback["_ai_limit_reached"] = True
    return fallback


# ---------------------------------------------------------------------------
# Deterministic fallback (no API key configured / call failed)
# ---------------------------------------------------------------------------

def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "flat"
    arrow = "up" if v >= 0 else "down"
    return f"{arrow} {abs(v):.1f}%"


#: How many highlight bullets lead the executive summary, by tone. The
#: deterministic fallback template is otherwise tone-invariant by
#: construction (it's a fixed template, not a model) -- this is the one real
#: lever tone has on this path. manager=2 reproduces the exact pre-T1
#: behavior (which hardcoded [:2]) so the default template's fallback output
#: is unchanged.
_TONE_HIGHLIGHT_COUNT = {"executive": 1, "manager": 2, "specialist": 3}


def _fallback_report(m: dict, branding: dict, sections_requested: list[str], tone: str = "manager") -> dict:
    client_name = branding.get("client_name") or "the client"
    highlights, watchouts, sections = [], [], []

    if "analytics" in m:
        a = m["analytics"]
        highlights.append(
            f"Sessions {_fmt_pct(a['sessions_change_pct'])} and revenue {_fmt_pct(a['revenue_change_pct'])} "
            f"comparing the two halves of the period."
        )
        top = a["by_channel"][0] if a["by_channel"] else None
        if top:
            highlights.append(f"{top['channel']} led revenue at ${top['revenue_usd']:,.0f} "
                               f"({top['share_of_sessions_pct']}% of sessions).")
        if a.get("top_declining_channel") and (a["top_declining_channel"]["session_change_pct"] or 0) < 0:
            watchouts.append(f"{a['top_declining_channel']['channel']} sessions are "
                              f"{_fmt_pct(a['top_declining_channel']['session_change_pct'])}.")
        sections.append({
            "heading": "Web Analytics",
            "narrative": (
                f"Over the {a['date_range']['days']}-day period, {client_name}'s site drove "
                f"{a['totals']['sessions']:,} sessions and ${a['totals']['revenue_usd']:,.0f} in revenue "
                f"at a {a['totals']['conversion_rate']}% conversion rate. Session volume is "
                f"{_fmt_pct(a['sessions_change_pct'])} and revenue is {_fmt_pct(a['revenue_change_pct'])} "
                f"versus the first half of the period."
            ),
            "recommendations": [
                f"Investigate {a['top_declining_channel']['channel']} performance"
                if a.get("top_declining_channel") else "Maintain current channel mix.",
                f"Double down on {a['by_channel'][0]['channel']}, the top revenue channel."
                if a["by_channel"] else "Review channel attribution.",
            ],
        })

    if "seo" in m:
        s = m["seo"]
        highlights.append(f"{s['indexable_pct']}% of {s['total_urls_crawled']} crawled pages are indexable.")
        if s["severity_counts"].get("critical", 0):
            watchouts.append(f"{s['severity_counts']['critical']} pages have critical technical issues "
                              f"(broken pages or server errors).")
        top_issue = s["top_issues"][0] if s["top_issues"] else None
        sections.append({
            "heading": "SEO & Site Health",
            "narrative": (
                f"The audit crawled {s['total_urls_crawled']} URLs. {s['severity_counts'].get('critical', 0)} "
                f"are critical, {s['severity_counts'].get('warning', 0)} need attention. Organic search "
                f"performance over the search console's trailing reporting window: "
                f"{s['search_performance']['impressions_28d']:,} impressions, "
                f"{s['search_performance']['clicks_28d']:,} clicks ({s['search_performance']['ctr_pct']}% CTR), "
                f"average position {s['search_performance']['avg_position']}."
            ),
            "recommendations": [
                f"Prioritize fixing '{top_issue[0].replace('_', ' ')}' — it affects {top_issue[1]} pages."
                if top_issue else "Continue routine crawl monitoring.",
                "Fix critical (broken/500) pages first — they block both users and crawlers.",
            ],
        })

    if "sales" in m:
        sl = m["sales"]
        highlights.append(f"${sl['totals']['revenue_usd']:,.0f} in closed-won revenue "
                           f"at a {sl['totals']['win_rate_pct']}% win rate.")
        if sl.get("revenue_momentum_pct") is not None and sl["revenue_momentum_pct"] < 0:
            watchouts.append(f"Revenue is {_fmt_pct(sl['revenue_momentum_pct'])} month-over-month.")
        top_rep = sl["by_rep"][0] if sl["by_rep"] else None
        sections.append({
            "heading": "Sales Performance",
            "narrative": (
                f"The pipeline closed {sl['totals']['deals_won']} deals won against {sl['totals']['deals_lost']} lost "
                f"({sl['totals']['win_rate_pct']}% win rate), for ${sl['totals']['revenue_usd']:,.0f} in revenue "
                f"at an average deal size of ${sl['totals']['avg_deal_size_usd']:,.0f}."
            ),
            "recommendations": [
                f"{top_rep['sales_rep']} is the top performer — review their approach for coaching opportunities."
                if top_rep else "Review rep performance distribution.",
                "Investigate the lowest win-rate lead source for qualification issues.",
            ],
        })

    highlight_count = _TONE_HIGHLIGHT_COUNT.get(tone, 2)
    exec_summary = (
        " ".join(highlights[:highlight_count]) if highlights else "No data sources were provided for this period."
    )

    return {
        "report_title": f"{client_name} — Performance Report",
        "period_label": m.get("period_label", ""),
        "executive_summary": exec_summary,
        "highlights": highlights,
        "watchouts": watchouts or ["No significant risks identified this period."],
        "sections": sections,
        "next_steps": [
            "Review this report with the account team.",
            "Confirm priorities for next period based on the recommendations above.",
        ],
        "_ai_generated": False,
    }
