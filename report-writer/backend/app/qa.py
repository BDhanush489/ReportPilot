"""
Auto-QA layer: deterministic checks that a generated report's narrative is
fully backed by the metrics it was computed from, so a consultant can hand
a report to a client without personally re-verifying every figure.

Design mirrors the rest of the app: no check here uses an LLM or "looks
right" judgment. Every check is a pure function over the report dict, the
computed metrics_payload, and (for aggregation sanity, added in a later
slice) the source rows metrics_payload was computed from. Same trust model
as metrics.py — the QA layer is exactly as deterministic as the numbers it's
checking.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field

from . import metrics as metrics_mod
from .narrative_links import CitationResult, check_chart_citations
from .viz.aggregates import compute_aggregate

# ---------------------------------------------------------------------------
# Traceability: every number rendered in the narrative must trace back to a
# value actually present in metrics_payload.
# ---------------------------------------------------------------------------

#: Narrative fields agent.py actually writes prose into. Scoped deliberately
#: to these — insights.py/cleaning.py are separately deterministic and don't
#: carry the same "could the model have invented this" risk this check exists
#: to catch.
_NARRATIVE_LIST_FIELDS = ("highlights", "watchouts", "next_steps")
_NARRATIVE_TEXT_FIELDS = ("executive_summary",)

#: A number literal: optional $, digit groups (with optional comma
#: thousands), optional decimal part, optional K/M/B magnitude suffix,
#: optional %. Boundary lookarounds keep it from matching inside larger
#: tokens (ids, words) or spilling into a following word.
_NUMBER_RE = re.compile(
    r"(?<![\w.])"
    r"(?P<currency>\$)?"
    r"(?P<int>\d{1,3}(?:,\d{3})+|\d+)"
    r"(?P<dec>\.\d+)?"
    r"(?P<abbrev>[KMB])?"
    r"(?P<percent>%)?"
    r"(?![\w])"
)

#: Bare 4-digit numbers in this range are almost certainly a calendar year
#: mentioned in prose ("in 2026"), not a metric — years never appear in
#: metrics_payload, so without this they'd be flagged as untraceable on every
#: report. Narrow, explicit, documented exception rather than a broad guess.
_YEAR_RANGE = range(1900, 2100)

#: A rounded-for-readability number is still trustworthy; a number whose
#: implied rounding granularity exceeds 5% of the true value is not a
#: rounding of that value, it's a different (wrong or fabricated) number.
#: This is what lets "$45,000" pass against a true value of $45,231.50 while
#: still failing "$99,999" against that same true value.
_MAX_ROUNDING_RATIO = 0.05

_MAGNITUDE = {"K": 1e3, "M": 1e6, "B": 1e9}


@dataclass
class NumberFinding:
    literal: str
    value: float
    tier: str  # "exact" | "rounded" | "fail"
    matched_path: str | None
    matched_value: float | None
    field: str  # which report field this number was found in


@dataclass
class TraceabilityResult:
    findings: list[NumberFinding] = field(default_factory=list)

    @property
    def fail_findings(self) -> list[NumberFinding]:
        return [f for f in self.findings if f.tier == "fail"]

    @property
    def warning_findings(self) -> list[NumberFinding]:
        return [f for f in self.findings if f.tier == "rounded"]

    @property
    def ok(self) -> bool:
        return not self.fail_findings


def _flatten_numeric(payload, prefix: str = "") -> list[tuple[str, float]]:
    """Walk metrics_payload, yielding (json_path, value) for every numeric
    leaf. bool is excluded explicitly — it's a subclass of int in Python and
    would otherwise silently match narrative "0"/"1". tuple is walked
    exactly like list -- metrics.py's seo_metrics() returns top_issues as a
    list of (name, count) TUPLES (`[(k, int(v)) for k, v in ...]`), and
    before this handled tuples, every top_issues count was silently
    unreachable here, making every "'X' affects N pages" sentence
    permanently untraceable regardless of correctness. Caught by a real
    aurora-home-goods report's badge coming back FAIL for a genuinely
    correct number."""
    out: list[tuple[str, float]] = []
    if isinstance(payload, dict):
        for k, v in payload.items():
            if isinstance(k, str) and k.startswith("_"):
                continue
            out += _flatten_numeric(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(payload, (list, tuple)):
        for i, v in enumerate(payload):
            out += _flatten_numeric(v, f"{prefix}[{i}]")
    elif isinstance(payload, bool):
        pass
    elif isinstance(payload, (int, float)):
        out.append((prefix, float(payload)))
    return out


def _iter_narrative_fields(report: dict):
    """Yields (field_label, text) for every prose string the narrative-
    writing step (agent.py) is responsible for. Order/labels are for
    findings' provenance, not for matching logic."""
    for key in _NARRATIVE_TEXT_FIELDS:
        if report.get(key):
            yield key, report[key]
    for key in _NARRATIVE_LIST_FIELDS:
        for i, s in enumerate(report.get(key) or []):
            yield f"{key}[{i}]", s
    for si, section in enumerate(report.get("sections") or []):
        if section.get("narrative"):
            yield f"sections[{si}].narrative", section["narrative"]
        for ri, s in enumerate(section.get("recommendations") or []):
            yield f"sections[{si}].recommendations[{ri}]", s


def _parse_number(m: re.Match) -> dict | None:
    int_part = m.group("int")
    dec_part = m.group("dec")
    abbrev = m.group("abbrev")
    digits = int_part.replace(",", "")

    if not dec_part and not abbrev and not m.group("percent") and not m.group("currency") \
            and "," not in int_part and int(digits) in _YEAR_RANGE:
        return None  # looks like a bare calendar year, not a metric

    unit = _MAGNITUDE.get(abbrev, 1.0)
    value = float(digits + (dec_part or "")) * unit

    if dec_part:
        decimals_shown = len(dec_part) - 1  # exclude the leading '.'
        precision_abs = unit * (10 ** -decimals_shown)
    else:
        stripped = digits.rstrip("0")
        trailing_zeros = len(digits) - len(stripped) if stripped else 0
        precision_abs = unit * (10 ** trailing_zeros)

    return {"literal": m.group(0), "value": value, "precision_abs": precision_abs, "is_percent": bool(m.group("percent"))}


def _extract_numbers(text: str) -> list[dict]:
    out = []
    for m in _NUMBER_RE.finditer(text):
        parsed = _parse_number(m)
        if parsed:
            out.append(parsed)
    return out


def _match_number(
    value: float, precision_abs: float, candidates: list[tuple[str, float]], allow_sign_flip: bool = False,
) -> tuple[str, str | None, float | None]:
    """allow_sign_flip: narrative convention writes a signed change_pct as an
    unsigned magnitude plus a direction word ("a 20.8% decrease" for a metric
    stored as -20.8), never with a literal minus sign — so for percent
    literals we also match against the metric's absolute value. This makes
    check_traceability sign-agnostic for percentages by design; verifying
    the direction *word* actually agrees with the matched metric's real sign
    is check_unsupported_claims' job (it has the surrounding sentence to
    check that against), not this function's."""
    def close(c: float) -> bool:
        return abs(c - value) < 1e-6 or (allow_sign_flip and abs(-c - value) < 1e-6)

    for path, c in candidates:
        if close(c):
            return "exact", path, c

    for path, c in candidates:
        if c == 0:
            continue
        cap = abs(c) * _MAX_ROUNDING_RATIO
        eff_precision = min(precision_abs, cap)
        if eff_precision <= 0:
            continue
        if abs(c - value) <= eff_precision / 2 or (allow_sign_flip and abs(-c - value) <= eff_precision / 2):
            return "rounded", path, c

    return "fail", None, None


def check_traceability(report: dict, metrics_payload: dict) -> TraceabilityResult:
    """Every number in the narrative must trace back to metrics_payload,
    exactly or as a plausible rounding of a real value (see _MAX_ROUNDING_RATIO
    for what "plausible" means). Anything else is flagged fail — either
    fabricated or wrong."""
    candidates = _flatten_numeric(metrics_payload)
    result = TraceabilityResult()
    for field_label, text in _iter_narrative_fields(report):
        for num in _extract_numbers(text):
            tier, path, matched_value = _match_number(
                num["value"], num["precision_abs"], candidates, allow_sign_flip=num["is_percent"],
            )
            result.findings.append(NumberFinding(
                literal=num["literal"], value=num["value"], tier=tier,
                matched_path=path, matched_value=matched_value, field=field_label,
            ))
    return result


# ---------------------------------------------------------------------------
# Aggregation sanity: metrics_payload's sums/averages/counts must match what
# you get recomputing straight from source rows.
#
# Recomputation reuses metrics.py itself rather than reimplementing "how to
# aggregate" a second time — a second implementation would drift from the
# first over time and the check would quietly stop meaning anything. What
# this check actually verifies is narrower and still real: that the numbers
# *persisted* in metrics_payload are what metrics.py produces from the rows
# on hand right now, catching transcription bugs, tampering, or (via the
# fingerprint below) a report going stale against source data that moved.
# ---------------------------------------------------------------------------

def _strip_private(payload):
    if isinstance(payload, dict):
        return {k: _strip_private(v) for k, v in payload.items() if not (isinstance(k, str) and k.startswith("_"))}
    if isinstance(payload, list):
        return [_strip_private(v) for v in payload]
    return payload


_RECOMPUTE = {
    "analytics": lambda frames: metrics_mod.analytics_metrics(frames["analytics"]),
    "seo": lambda frames: metrics_mod.seo_metrics(frames["seo"]),
    "sales": lambda frames: metrics_mod.sales_metrics(*frames["sales"]),
}


def _numeric_diff(fresh, persisted, path: str = ""):
    """Yields (path, fresh_value, persisted_value) for numeric leaves that
    disagree beyond tolerance (0 for a pair of ints, 1e-6 for anything
    involving a float — matches the exit criterion's stated tolerances).
    Structural drift (a key recompute doesn't produce) is out of scope here —
    that's a shape-validation concern (_validate_report_shape in agent.py),
    not an aggregation-correctness one."""
    if isinstance(fresh, dict) and isinstance(persisted, dict):
        for k, fv in fresh.items():
            if isinstance(k, str) and k.startswith("_"):
                continue
            if k not in persisted:
                continue
            yield from _numeric_diff(fv, persisted[k], f"{path}.{k}" if path else k)
    elif isinstance(fresh, (list, tuple)) and isinstance(persisted, (list, tuple)):
        # tuple handled alongside list for the same reason _flatten_numeric
        # does: seo_metrics()'s top_issues is a list of (name, count) tuples.
        for i, (fv, pv) in enumerate(zip(fresh, persisted)):
            yield from _numeric_diff(fv, pv, f"{path}[{i}]")
    elif isinstance(fresh, bool) or isinstance(persisted, bool):
        return
    elif isinstance(fresh, (int, float)) and isinstance(persisted, (int, float)):
        tol = 0 if isinstance(fresh, int) and isinstance(persisted, int) else 1e-6
        if abs(fresh - persisted) > tol:
            yield path, fresh, persisted


@dataclass
class AggregationMismatch:
    source: str
    path: str
    reported: float
    recomputed: float


@dataclass
class AggregationResult:
    mismatches: list[AggregationMismatch] = field(default_factory=list)
    #: sources present in metrics_payload but with no source rows available
    #: to recompute against — distinct from a mismatch, and distinct from a
    #: pass: the check simply couldn't run, so it shouldn't count as either.
    inconclusive_sources: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches


def check_aggregation_sanity(metrics_payload: dict, source_frames: dict) -> AggregationResult:
    """source_frames: {"analytics": df, "seo": df, "sales": (deals_df, monthly_df_or_None)},
    only the keys you can actually supply rows for — a missing key marks that
    source inconclusive rather than failing it."""
    result = AggregationResult()
    for source, recompute in _RECOMPUTE.items():
        if source not in metrics_payload:
            continue
        if source not in source_frames:
            result.inconclusive_sources.append(source)
            continue
        fresh = _strip_private(recompute(source_frames))
        persisted = metrics_payload[source]
        for path, fv, pv in _numeric_diff(fresh, persisted):
            result.mismatches.append(AggregationMismatch(source=source, path=path, reported=pv, recomputed=fv))
    return result


# ---------------------------------------------------------------------------
# Insights sanity: insights.py's cards (health score, SEO opportunity,
# device gap, lead-source efficiency) are deterministic -- no LLM anywhere
# in that path -- but that trust was architectural, never independently
# re-checked the way a narrative claim is. Recompute-and-diff, the same
# pattern as aggregation sanity above, applied to insights.py instead of
# metrics.py: compare against a fresh compute_insights() call rather than
# regex-scanning the text for numbers. Regex-scanning would be the wrong
# test here and would cry wolf constantly -- a card's headline/detail often
# states a number insights.py *derived* (a health score, a dollar
# opportunity estimate) that never appears verbatim anywhere in
# metrics_payload by design, so "does this literal exist in metrics" would
# false-positive on exactly the figures that are working correctly. Exact
# string equality against a fresh recompute has no such false-positive
# risk: compute_insights is pure, so two calls with the same input produce
# byte-identical text unless something real changed in between.
# ---------------------------------------------------------------------------

_INSIGHT_TEXT_FIELDS = ("title", "headline", "sub", "detail")


@dataclass
class InsightMismatch:
    card_id: str
    field: str
    reported: str
    recomputed: str


@dataclass
class InsightsSanityResult:
    mismatches: list[InsightMismatch] = field(default_factory=list)
    #: a card compute_insights() produces on recompute but that isn't in
    #: the report (or vice versa) -- distinct from a text mismatch on a
    #: card both sides agree exists.
    missing_cards: list[str] = field(default_factory=list)
    unexpected_cards: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.mismatches or self.missing_cards or self.unexpected_cards)


def check_insights_sanity(reported_insights: list[dict] | None, metrics_payload: dict) -> InsightsSanityResult:
    """reported_insights: report["insights"] as persisted (report_builder.py
    attaches this from the exact same compute_insights() call -- a mismatch
    here means something mutated or diverged between that call and what got
    persisted, e.g. the historical _avg_deal in-place-mutation bug this
    project already hit once with html_dashboard.py's drill tables).

    reported_insights=None (the key is simply absent) means the caller
    isn't using this facility at all -- e.g. a hand-built test fixture that
    never called compute_insights, or a report predating insights entirely
    -- and is skipped, vacuously ok, the same accommodation source_frames/
    charts already get elsewhere in this module. A real empty list ([],
    insights.py legitimately finding nothing to say) is not the same thing
    and is still validated for real."""
    if reported_insights is None:
        return InsightsSanityResult()

    import copy

    from .insights import compute_insights

    fresh = compute_insights(copy.deepcopy(metrics_payload))
    fresh_by_id = {c["id"]: c for c in fresh}
    reported_by_id = {c["id"]: c for c in (reported_insights or [])}

    result = InsightsSanityResult(
        missing_cards=sorted(set(fresh_by_id) - set(reported_by_id)),
        unexpected_cards=sorted(set(reported_by_id) - set(fresh_by_id)),
    )
    for card_id in sorted(set(fresh_by_id) & set(reported_by_id)):
        fresh_card, reported_card = fresh_by_id[card_id], reported_by_id[card_id]
        for text_field in _INSIGHT_TEXT_FIELDS:
            fresh_val, reported_val = fresh_card.get(text_field), reported_card.get(text_field)
            if fresh_val != reported_val:
                result.mismatches.append(InsightMismatch(
                    card_id=card_id, field=text_field,
                    reported=str(reported_val), recomputed=str(fresh_val),
                ))
    return result


# ---------------------------------------------------------------------------
# Source fingerprint: lets a headless QA run, decoupled in time from report
# generation, tell "the source data moved since this report was built" apart
# from "this report's numbers are wrong." Computed at generation time
# (persisted alongside metrics_payload) and recomputed at QA time against
# freshly re-derived rows; a mismatch means aggregation sanity is
# inconclusive for that source, not failed.
# ---------------------------------------------------------------------------

def compute_source_fingerprint(df) -> dict:
    payload = df.to_csv(index=False).encode("utf-8")
    return {"row_count": int(len(df)), "sha256": hashlib.sha256(payload).hexdigest()}


# ---------------------------------------------------------------------------
# Unsupported-claim scan: every sentence that asserts something checkable —
# a number, or a comparative/trend word like "up"/"declined"/"highest" — must
# be backed by something in metrics_payload. A sentence with neither is
# scope-writing ("Let's review this together next quarter.") and isn't
# checked; deterministic verification has nothing to verify it against.
# ---------------------------------------------------------------------------

#: Doesn't special-case abbreviations ("e.g.", "Inc.") — the agent's system
#: prompt already forbids that register of prose, so mis-splitting on one is
#: a deliberate scope cut for this app's actual narrative style, not an
#: oversight.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")

#: "higher"/"lower" deliberately excluded — real narrative uses them at
#: least as often for a static ranking ("higher-performing lead sources") as
#: for a time trend, and that ambiguity means treating them as trend words
#: produces real false positives (caught against a live-generated report).
#: They're checked as comparative/ranking claims instead, alongside
#: superlatives — same "is there ranked data to draw this from" question as
#: "top"/"best".
_UP_WORDS = {"up", "grew", "grow", "growing", "increased", "increase", "rose",
             "rising", "surged", "improved", "gained", "climbed"}
_DOWN_WORDS = {"down", "declined", "decline", "declining", "decreased", "decrease",
               "dropped", "drop", "fell", "falling", "worsened", "slipped"}
_COMPARATIVE_WORDS = {"highest", "lowest", "led", "leading", "top", "best", "worst",
                       "outperformed", "underperformed", "higher", "lower"}
_WORD_RE = re.compile(r"[A-Za-z]+")

#: "Double down on X" is the agent's own stock recommendation phrasing
#: (see agent.py's fallback template) — idiomatic, not a trend claim about
#: X going down. Strip it before tokenizing rather than let a literal "down"
#: match trigger a false unlinked-claim finding on totally ordinary prose.
_IDIOM_GUARD_RE = re.compile(r"\bdouble\s+down\b", re.IGNORECASE)


def _split_sentences(text: str) -> list[str]:
    return [p for p in _SENTENCE_SPLIT_RE.split(text.strip()) if p]


def _keywords_in(sentence: str) -> tuple[set[str], set[str]]:
    sentence = _IDIOM_GUARD_RE.sub("", sentence)
    words = {w.lower() for w in _WORD_RE.findall(sentence)}
    return words & (_UP_WORDS | _DOWN_WORDS), words & _COMPARATIVE_WORDS


def _direction_signs_present(metrics_payload: dict) -> set[str]:
    """Which of {"up", "down"} at least one *_change_pct/*_momentum_pct
    field in metrics_payload actually shows — what a qualitative trend claim
    with no explicit number has to be consistent with."""
    signs = set()
    for path, value in _flatten_numeric(metrics_payload):
        leaf = path.rsplit(".", 1)[-1].split("[")[0]
        if leaf.endswith("change_pct") or leaf.endswith("momentum_pct"):
            if value > 0:
                signs.add("up")
            elif value < 0:
                signs.add("down")
    return signs


def _has_ranked_data(metrics_payload) -> bool:
    """Whether metrics_payload contains any grouped/ranked list (by_channel,
    by_rep, ...) — the generic, field-name-agnostic proxy for "there's data
    this superlative claim could legitimately be drawing from"."""
    if isinstance(metrics_payload, dict):
        return any(_has_ranked_data(v) for v in metrics_payload.values())
    if isinstance(metrics_payload, list):
        if metrics_payload and all(isinstance(x, dict) for x in metrics_payload):
            return True
        return any(_has_ranked_data(v) for v in metrics_payload)
    return False


@dataclass
class ClaimFinding:
    sentence: str
    field: str
    status: str  # "linked" | "unlinked"
    reason: str


@dataclass
class ClaimScanResult:
    findings: list[ClaimFinding] = field(default_factory=list)

    @property
    def unlinked(self) -> list[ClaimFinding]:
        return [f for f in self.findings if f.status == "unlinked"]

    @property
    def ok(self) -> bool:
        return not self.unlinked


def check_unsupported_claims(report: dict, metrics_payload: dict) -> ClaimScanResult:
    candidates = _flatten_numeric(metrics_payload)
    direction_signs = _direction_signs_present(metrics_payload)
    ranked_data_exists = _has_ranked_data(metrics_payload)
    result = ClaimScanResult()

    for field_label, text in _iter_narrative_fields(report):
        for sentence in _split_sentences(text):
            numbers = _extract_numbers(sentence)
            trend_words, superlative_words = _keywords_in(sentence)

            if numbers:
                # A concrete number is the strongest claim in the sentence,
                # so its own traceability governs — but a percent literal
                # matched via the sign-flip allowance ("20.8% decrease" for
                # a metric stored as -20.8) still has to have its direction
                # *word* actually agree with the metric's real sign; that's
                # the one thing check_traceability alone can't verify.
                matches = [
                    _match_number(n["value"], n["precision_abs"], candidates, allow_sign_flip=n["is_percent"])
                    for n in numbers
                ]
                if any(tier == "fail" for tier, _, _ in matches):
                    result.findings.append(ClaimFinding(sentence, field_label, "unlinked", "contains an untraceable number"))
                    continue

                claimed_dirs = {("up" if w in _UP_WORDS else "down") for w in trend_words}
                actual_signs = {("up" if mv > 0 else "down") for _, _, mv in matches if mv}
                if claimed_dirs and actual_signs and not (claimed_dirs & actual_signs):
                    result.findings.append(ClaimFinding(
                        sentence, field_label, "unlinked",
                        f"direction word claims {'/'.join(sorted(claimed_dirs))} but the matched metric is {'/'.join(sorted(actual_signs))}",
                    ))
                else:
                    result.findings.append(ClaimFinding(sentence, field_label, "linked", "number(s) trace to metrics_payload"))
                continue

            if trend_words:
                directions_claimed = {("up" if w in _UP_WORDS else "down") for w in trend_words}
                if directions_claimed <= direction_signs:
                    result.findings.append(ClaimFinding(sentence, field_label, "linked", "matching trend metric present"))
                else:
                    result.findings.append(ClaimFinding(
                        sentence, field_label, "unlinked",
                        f"claims a {'/'.join(sorted(directions_claimed))} trend with no matching metric in metrics_payload",
                    ))
                continue

            if superlative_words:
                if ranked_data_exists:
                    result.findings.append(ClaimFinding(sentence, field_label, "linked", "ranked/grouped data present"))
                else:
                    result.findings.append(ClaimFinding(sentence, field_label, "unlinked", "superlative claim with no ranked data in metrics_payload"))
                continue

            # No number, no trend/superlative word — nothing checkable here.

    return result


# ---------------------------------------------------------------------------
# Badge: combines the three checks into one verdict. FAIL means at least one
# check found something concretely wrong (a fabricated/wrong number, a
# reconciliation mismatch, an unbacked claim). PASS-WITH-WARNINGS means
# nothing is wrong, but something couldn't be fully confirmed — a
# legitimately-rounded display number, or a source that couldn't be
# recomputed against. PASS means every check found nothing to flag at all.
# ---------------------------------------------------------------------------

@dataclass
class QAReport:
    badge: str  # "PASS" | "PASS-WITH-WARNINGS" | "FAIL"
    failing_checks: list[str]
    traceability: TraceabilityResult
    aggregation: AggregationResult
    claims: ClaimScanResult
    #: A3 — narrative <-> chart citation reconciliation. Defaults to an
    #: empty result (nothing to check) when run_qa is called without charts,
    #: same accommodation source_frames already gets for aggregation sanity.
    citations: CitationResult = field(default_factory=lambda: CitationResult([]))
    #: insights.py's cards, independently recomputed and diffed -- see
    #: check_insights_sanity's docstring for why this is a recompute, not a
    #: traceability scan.
    insights: InsightsSanityResult = field(default_factory=InsightsSanityResult)

    def to_dict(self) -> dict:
        return {
            "badge": self.badge,
            "failing_checks": self.failing_checks,
            "traceability": {
                "ok": self.traceability.ok,
                "numbers_checked": len(self.traceability.findings),
                "fail": [asdict(f) for f in self.traceability.fail_findings],
                "warnings": [asdict(f) for f in self.traceability.warning_findings],
            },
            "aggregation_sanity": {
                "ok": self.aggregation.ok,
                "mismatches": [asdict(m) for m in self.aggregation.mismatches],
                "inconclusive_sources": self.aggregation.inconclusive_sources,
            },
            "unsupported_claims": {
                "ok": self.claims.ok,
                "claims_checked": len(self.claims.findings),
                "unlinked": [asdict(f) for f in self.claims.unlinked],
            },
            "chart_citations": self.citations.to_dict(),
            "insights_sanity": {
                "ok": self.insights.ok,
                "mismatches": [asdict(m) for m in self.insights.mismatches],
                "missing_cards": self.insights.missing_cards,
                "unexpected_cards": self.insights.unexpected_cards,
            },
        }


def run_qa(report: dict, metrics_payload: dict, source_frames: dict | None = None,
           charts: list | None = None) -> QAReport:
    """source_frames is optional — omit it (or pass {}) to run traceability
    and the claim scan without aggregation sanity; those sources are then
    marked inconclusive rather than failed, which is enough on its own to
    keep the badge from ever reading a bare PASS you can't actually back up.
    charts is optional too — omit it to skip citation reconciliation
    entirely (an empty, vacuously-ok CitationResult), for callers that
    don't have chart data (e.g. the ad-hoc viz engine's separate QA path).
    insights_sanity always runs — report["insights"] is either present
    (possibly empty list) or absent, both handled by check_insights_sanity
    without a separate opt-in flag."""
    traceability = check_traceability(report, metrics_payload)
    aggregation = check_aggregation_sanity(metrics_payload, source_frames or {})
    claims = check_unsupported_claims(report, metrics_payload)
    citations = check_chart_citations(report, charts or [])
    insights_result = check_insights_sanity(report.get("insights"), metrics_payload)

    failing_checks = []
    if not traceability.ok:
        failing_checks.append("traceability")
    if not aggregation.ok:
        failing_checks.append("aggregation_sanity")
    if not claims.ok:
        failing_checks.append("unsupported_claims")
    if not citations.ok:
        failing_checks.append("chart_citations")
    if not insights_result.ok:
        failing_checks.append("insights_sanity")

    if failing_checks:
        badge = "FAIL"
    elif traceability.warning_findings or aggregation.inconclusive_sources:
        badge = "PASS-WITH-WARNINGS"
    else:
        badge = "PASS"

    return QAReport(badge=badge, failing_checks=failing_checks, traceability=traceability,
                     aggregation=aggregation, claims=claims, citations=citations, insights=insights_result)


# ---------------------------------------------------------------------------
# Chart traceability (Lever 5 — extends aggregation sanity to the
# schema-agnostic viz engine's ad-hoc field-pair charts, app/viz/engine.py).
#
# Same reuse principle as check_aggregation_sanity: recompute via the exact
# function the chart was built with (app/viz/aggregates.py::compute_aggregate)
# rather than a second, possibly-diverging aggregation path. Reuses
# AggregationMismatch/AggregationResult directly rather than a parallel
# "chart mismatch" type — a mismatch is a mismatch regardless of which
# pipeline produced the number being checked.
# ---------------------------------------------------------------------------

def check_chart_traceability(chart, df) -> AggregationResult:
    """chart: a viz.engine.ChartResult. df: the same source rows the chart
    was built from (or freshly re-derived ones — the caller's choice, same
    as check_aggregation_sanity's source_frames contract)."""
    result = AggregationResult()
    label = f"{chart.x_col}_x_{chart.y_col}"
    fresh = compute_aggregate(
        df, chart.x_col, chart.y_col, chart.x_type, chart.y_type, chart.aggregate_with_outliers.agg_fn,
    )
    fresh_by_x = {p.x: p.y for p in fresh.points}

    for point in chart.aggregate_with_outliers.points:
        if point.x not in fresh_by_x:
            result.mismatches.append(AggregationMismatch(
                source=label, path=str(point.x), reported=point.y, recomputed=float("nan"),
            ))
            continue
        fresh_y = fresh_by_x[point.x]
        if abs(fresh_y - point.y) > 1e-6:
            result.mismatches.append(AggregationMismatch(source=label, path=str(point.x), reported=point.y, recomputed=fresh_y))

    return result


@dataclass
class ChartQAReport:
    badge: str  # "PASS" | "FAIL" -- no ad-hoc-chart WARNING tier yet (no
                # display-rounding step exists between compute and plot the
                # way there is for narrative prose), so this stays binary.
    aggregation: AggregationResult

    def to_dict(self) -> dict:
        return {
            "badge": self.badge,
            "aggregation_sanity": {
                "ok": self.aggregation.ok,
                "mismatches": [asdict(m) for m in self.aggregation.mismatches],
            },
        }


def run_chart_qa(chart, df) -> ChartQAReport:
    aggregation = check_chart_traceability(chart, df)
    return ChartQAReport(badge="PASS" if aggregation.ok else "FAIL", aggregation=aggregation)
