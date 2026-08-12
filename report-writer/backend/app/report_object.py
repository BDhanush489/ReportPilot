"""
Canonical report object (F0): one serializable structure that every
renderer -- the PDF today, the dashboard/PPTX/email exports later -- reads
from. Assembled once per report generation in report_builder.py; nothing
downstream re-queries source data or recomputes a number.

Namespace split that matters, and is easy to collapse by accident:
`metrics` is the tight, curated aggregate set qa.run_qa's traceability scan
matches narrative numbers against -- anything added here enlarges the
haystack a fabricated number could coincidentally match, weakening
fabrication detection. `series` holds the larger data (weekly/monthly rows)
that charts are drawn from but the narrative never cites a cell of directly
-- kept out of `metrics` on purpose, so making it recoverable for future
chart-annotation work doesn't quietly widen what "traceable" means.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Period:
    label: str
    start: str | None = None
    end: str | None = None


@dataclass
class SourceInfo:
    row_count: int
    sha256: str


@dataclass
class ChartRef:
    id: str
    section: str
    caption: str
    img: str
    chart_type: str
    #: Namespace-qualified dotted paths, e.g. "metrics.analytics.by_channel"
    #: or "series.sales.monthly" -- resolvable via ReportObject.resolve().
    metric_paths: list[str] = field(default_factory=list)
    #: A1 — deterministic verdict on whether chart_type actually fits the
    #: data this specific report has, computed from resolve()'d values, not
    #: a static per-caption assumption. "good" | "discouraged" |
    #: "ambiguous_data" (see app/chart_intelligence.py).
    suitability_verdict: str = "unknown"
    suitability_reason: str = ""
    suitability_alternatives: list[str] = field(default_factory=list)
    #: A2 — the single most notable point on this chart (outlier / largest
    #: delta / peak), or None when nothing qualifies (see
    #: app/chart_annotation.py). Serialized form of ChartAnnotation, kept as
    #: a plain dict here (not the dataclass) so ChartRef itself stays a
    #: simple, uniformly-JSON-serializable structure.
    annotation: dict | None = None


def resolve_path(root: Any, path: str) -> Any:
    """Dotted-path lookup into a plain dict/list tree. "" returns root
    unchanged. Returns None the moment a segment can't be resolved, rather
    than raising -- a dangling path is a data question, not a crash."""
    if not path:
        return root
    current = root
    for segment in path.split("."):
        if isinstance(current, dict):
            if segment not in current:
                return None
            current = current[segment]
        elif isinstance(current, list):
            try:
                idx = int(segment)
            except ValueError:
                return None
            if not (0 <= idx < len(current)):
                return None
            current = current[idx]
        else:
            return None
    return current


@dataclass
class ReportObject:
    report_id: str
    period: Period
    sources: dict[str, SourceInfo]
    metrics: dict[str, Any]
    series: dict[str, Any]
    charts: list[ChartRef]
    narrative: dict[str, Any]
    qa: dict[str, Any]
    branding: dict[str, Any]
    #: Which section keys ("analytics"/"seo"/"sales") are present, in the
    #: order `narrative["sections"]` and this report's charts follow --
    #: needed to reattach charts to sections in to_legacy_report_dict().
    section_order: list[str] = field(default_factory=list)
    #: B1 -> B2: current-vs-prior-report deltas (see scheduler.py's
    #: _attach_period_comparison), computed via period_diff.py against the
    #: same schedule's previous run. None for a one-off upload/first-ever
    #: scheduled run -- there's no prior report to diff against yet. This
    #: is a genuinely different question from metrics.py's within-upload
    #: "change_pct" fields (first half vs. second half of one file) -- see
    #: the architecture doc's Known Limitations for that distinction.
    period_comparison: dict[str, Any] | None = None
    #: T3 -- exactly which declarative spec (app/template_specs/{id}.v{N}.json)
    #: actually produced this report, resolved at generation time even when
    #: the caller asked for "latest" -- so a stored report always knows
    #: precisely what to pin to on a later regeneration, regardless of how
    #: many newer template versions exist by then. Defaulted (not required)
    #: so report_object.json files persisted before T3 still deserialize.
    template_id: str = "default"
    template_version: int = 1

    def resolve(self, path: str) -> Any:
        """path is namespace-qualified: the first segment selects which
        top-level field of this object to resolve into (almost always
        "metrics" or "series"), the rest is a dotted path within it."""
        root_name, _, rest = path.partition(".")
        root = getattr(self, root_name, None)
        if root is None:
            return None
        return resolve_path(root, rest)

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "period": asdict(self.period),
            "sources": {k: asdict(v) for k, v in self.sources.items()},
            "metrics": self.metrics,
            "series": self.series,
            "charts": [asdict(c) for c in self.charts],
            "narrative": self.narrative,
            "qa": self.qa,
            "branding": self.branding,
            "section_order": self.section_order,
            "period_comparison": self.period_comparison,
            "template_id": self.template_id,
            "template_version": self.template_version,
        }

    @staticmethod
    def from_dict(d: dict) -> "ReportObject":
        return ReportObject(
            report_id=d["report_id"],
            period=Period(**d["period"]),
            sources={k: SourceInfo(**v) for k, v in d["sources"].items()},
            metrics=d["metrics"],
            series=d["series"],
            charts=[ChartRef(**c) for c in d["charts"]],
            narrative=d["narrative"],
            qa=d["qa"],
            branding=d["branding"],
            section_order=d.get("section_order", []),
            period_comparison=d.get("period_comparison"),
            template_id=d.get("template_id", "default"),
            template_version=d.get("template_version", 1),
        )

    def to_legacy_report_dict(self) -> dict:
        """Reconstructs the exact `report`-shaped dict report.html expects
        today -- each section carrying its own `charts` list inline -- so
        the template needs zero changes for F0. The canonical object itself
        never duplicates charts into `narrative`; this is the one place they
        get rejoined, on demand, for rendering."""
        legacy = copy.deepcopy(self.narrative)
        charts_by_section: dict[str, list[dict]] = {}
        for c in self.charts:
            charts_by_section.setdefault(c.section, []).append(
                {"caption": c.caption, "img": c.img, "annotation": c.annotation}
            )
        for i, section in enumerate(legacy.get("sections", [])):
            key = self.section_order[i] if i < len(self.section_order) else None
            section["charts"] = charts_by_section.get(key, [])
        return legacy
