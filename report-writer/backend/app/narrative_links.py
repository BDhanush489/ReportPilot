"""
Track A3 — narrative <-> chart link: the prose can cite a chart by id, and
QA reconciles the claim against that chart's own data.

Citation syntax is deliberately explicit and machine-parseable —
`[[chart:<chart_id>]]` inline in any narrative text field — rather than
inferring a citation from prose alone. This is the same design principle
as A2's annotations: a check that reconciles a claim against real data only
means something if the link between claim and chart is unambiguous, not
guessed at. Getting the LLM to actually emit these markers is prompt work
for a later pass (this project's local narrative model is a weak one, and
its output isn't reliably steerable enough to test against here); this
node builds and verifies the deterministic mechanism a citation feeds once
it exists — which is exactly what QA extends to check.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

CITATION_RE = re.compile(r"\[\[chart:([a-zA-Z0-9_\-]+)\]\]")

#: A citing sentence's claimed direction, from either an explicitly-signed
#: percentage ("+10%"/"-10%") or a direction word. Whichever is present.
_SIGNED_PCT_RE = re.compile(r"([+-])\s?\d+(?:\.\d+)?%")
_UP_WORDS = ("grew", "grow", "increased", "increase", "rose", "rise", "up", "gained", "climbed")
_DOWN_WORDS = ("declined", "decline", "decreased", "decrease", "dropped", "drop", "fell", "fall",
               "down", "lost", "shrank", "shrunk")

_NARRATIVE_TEXT_FIELDS = ("executive_summary",)
_NARRATIVE_LIST_FIELDS = ("highlights", "watchouts", "next_steps")


@dataclass
class Citation:
    chart_id: str
    field: str
    sentence: str


@dataclass
class CitationFinding:
    chart_id: str
    field: str
    sentence: str
    status: str  # "reconciled" | "mismatch" | "unknown_chart_id" | "no_directional_claim"
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CitationResult:
    findings: list[CitationFinding] = field(default_factory=list)

    @property
    def mismatches(self) -> list[CitationFinding]:
        return [f for f in self.findings if f.status in ("mismatch", "unknown_chart_id")]

    @property
    def ok(self) -> bool:
        return not self.mismatches

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "citations_checked": len(self.findings),
            "mismatches": [f.to_dict() for f in self.mismatches],
        }


def find_citations(narrative: dict) -> list[Citation]:
    """Scans every narrative text field for [[chart:ID]] markers. A
    "sentence" here is the whole field value the marker appeared in --
    fields are already short (a highlight, a summary sentence, a
    recommendation), so the field itself is the right citation unit rather
    than splitting on '.', which would break on "$5,000." mid-number."""
    citations: list[Citation] = []

    for field_name in _NARRATIVE_TEXT_FIELDS:
        text = narrative.get(field_name)
        if isinstance(text, str):
            for chart_id in CITATION_RE.findall(text):
                citations.append(Citation(chart_id=chart_id, field=field_name, sentence=text))

    for field_name in _NARRATIVE_LIST_FIELDS:
        for i, item in enumerate(narrative.get(field_name) or []):
            if isinstance(item, str):
                for chart_id in CITATION_RE.findall(item):
                    citations.append(Citation(chart_id=chart_id, field=f"{field_name}[{i}]", sentence=item))

    for i, section in enumerate(narrative.get("sections") or []):
        for sub_field in ("narrative",):
            text = section.get(sub_field)
            if isinstance(text, str):
                for chart_id in CITATION_RE.findall(text):
                    citations.append(Citation(chart_id=chart_id, field=f"sections[{i}].{sub_field}", sentence=text))
        for j, rec in enumerate(section.get("recommendations") or []):
            if isinstance(rec, str):
                for chart_id in CITATION_RE.findall(rec):
                    citations.append(Citation(chart_id=chart_id, field=f"sections[{i}].recommendations[{j}]", sentence=rec))

    return citations


def _claimed_direction(sentence: str) -> str | None:
    """+1 direction ("up"), -1 ("down"), or None if the sentence makes no
    directional claim at all (e.g. it just names a chart, no trend)."""
    signed = _SIGNED_PCT_RE.search(sentence)
    if signed:
        return "up" if signed.group(1) == "+" else "down"
    lower = sentence.lower()
    if any(w in lower for w in _UP_WORDS):
        return "up"
    if any(w in lower for w in _DOWN_WORDS):
        return "down"
    return None


def check_chart_citations(narrative: dict, charts: list) -> CitationResult:
    """charts: list[ChartRef] (or anything with .id/.annotation attributes).
    Every citation is checked two ways: does the chart_id actually exist
    (never invents one it can't verify), and — when the chart has a
    directional annotation (largest_delta) and the sentence makes a
    directional claim — does the claimed direction match the chart's own?
    A mismatch here is exactly the injected case the exit criterion names:
    narrative says +10%, chart shows -10%."""
    charts_by_id = {c.id: c for c in charts}
    findings: list[CitationFinding] = []

    for citation in find_citations(narrative):
        chart = charts_by_id.get(citation.chart_id)
        if chart is None:
            findings.append(CitationFinding(
                chart_id=citation.chart_id, field=citation.field, sentence=citation.sentence,
                status="unknown_chart_id", reason=f"no chart with id {citation.chart_id!r} exists in this report",
            ))
            continue

        annotation = chart.annotation
        chart_direction = annotation.get("direction") if annotation else None
        if chart_direction is None:
            findings.append(CitationFinding(
                chart_id=citation.chart_id, field=citation.field, sentence=citation.sentence,
                status="no_directional_claim",
                reason="chart has no directional annotation to reconcile against (not a largest_delta)",
            ))
            continue

        claimed = _claimed_direction(citation.sentence)
        if claimed is None:
            findings.append(CitationFinding(
                chart_id=citation.chart_id, field=citation.field, sentence=citation.sentence,
                status="no_directional_claim", reason="citing sentence makes no directional claim to check",
            ))
            continue

        if claimed == chart_direction:
            findings.append(CitationFinding(
                chart_id=citation.chart_id, field=citation.field, sentence=citation.sentence,
                status="reconciled", reason=f"claimed direction ({claimed}) matches the chart's ({chart_direction})",
            ))
        else:
            findings.append(CitationFinding(
                chart_id=citation.chart_id, field=citation.field, sentence=citation.sentence,
                status="mismatch",
                reason=f"narrative claims {claimed!r} but chart {citation.chart_id!r} shows {chart_direction!r}",
            ))

    return CitationResult(findings=findings)
