"""
T1 — templates are DATA, not code. A template is a declarative JSON spec
(app/template_specs/*.json): which sections it covers, what order, what
label, what charts, and what tone. This module is the *only* place that
maps the stable string ids a spec file uses to the real Python callables
that compute metrics / render chart images. Adding a template means adding
a JSON file here — report_builder.py's rendering loop needs zero changes.

Chart builders in charts.py take 1-2 positional args, each of which is
always `section_metrics[some_key]` (confirmed against every call site in
the pre-T1 report_builder.py — e.g. weekly_sessions_by_channel_chart(a["_weekly"],
a["_top_channels"])). That's what makes `builder_args: [key, ...]` a fully
generic way to describe "how to call this chart builder" from data alone.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import charts, metrics as metrics_mod

_SPEC_DIR = Path(__file__).parent / "template_specs"

#: metrics.py functions a spec's `metrics_fn` id can name. Not currently
#: dispatched through by report_builder.py (ingestion — which uploaded file
#: produced which section's raw metrics — is a data-availability concern,
#: not a templating one; see T2), but kept here as the one place the ids
#: are grounded to real callables so a future consumer isn't guessing.
METRIC_FNS = {
    "analytics_metrics": metrics_mod.analytics_metrics,
    "seo_metrics": metrics_mod.seo_metrics,
    "sales_metrics": metrics_mod.sales_metrics,
}

CHART_BUILDERS = {
    "weekly_sessions_by_channel_chart": charts.weekly_sessions_by_channel_chart,
    "revenue_trend_chart": charts.revenue_trend_chart,
    "channel_revenue_bar_chart": charts.channel_revenue_bar_chart,
    "conversion_rate_by_channel_chart": charts.conversion_rate_by_channel_chart,
    "device_split_pie_chart": charts.device_split_pie_chart,
    "seo_health_bar_chart": charts.seo_health_bar_chart,
    "top_issues_bar_chart": charts.top_issues_bar_chart,
    "monthly_revenue_and_winrate_chart": charts.monthly_revenue_and_winrate_chart,
    "revenue_by_rep_chart": charts.revenue_by_rep_chart,
    "revenue_by_lead_source_pie_chart": charts.revenue_by_lead_source_pie_chart,
    "revenue_by_product_bar_chart": charts.revenue_by_product_bar_chart,
}

#: Fallback label when a spec's section doesn't override one — kept small
#: and separate from the spec files so the three known section keys don't
#: need to repeat "Web Analytics" / etc. in every template that uses them.
DEFAULT_SECTION_LABELS = {
    "analytics": "Web Analytics",
    "seo": "SEO & Site Health",
    "sales": "Sales Performance",
}


@dataclass
class ChartSpec:
    caption: str
    builder: str
    builder_args: list[str]
    #: A1 — deterministic chart-type suitability needs to know how to read
    #: (x, y) series out of the resolved metric value (see chart_intelligence.py).
    chart_type: str
    metric_paths: list[str]
    shape: str
    x_field: str | None = None
    y_field: str | None = None
    #: T2 — raw SOURCE FILE column names (parsers.py's canonical names, not
    #: metric dict keys) this chart's whole point depends on, e.g. a revenue
    #: chart requires "revenue_usd". Every column parsers.py can't find gets
    #: silently defaulted (0 / "Unknown" / etc.) so the pipeline never
    #: crashes on a slightly different export -- but a defaulted business
    #: column would make a chart LOOK like real data ("$0 revenue") when
    #: it's actually "we don't have this." select_renderable_charts() is
    #: where that distinction gets acted on.
    requires_columns: list[str] = field(default_factory=list)


@dataclass
class SectionSpec:
    key: str
    label: str
    charts: list[ChartSpec] = field(default_factory=list)


@dataclass
class TemplateSpec:
    id: str
    #: T3 — template drift must not break scheduled-report reproducibility.
    #: Every spec file is pinned to one version number; "bumping" a template
    #: means adding a NEW {id}.v{N}.json file, never editing an existing one
    #: in place (that would silently change what already-generated reports
    #: claim they were built from).
    version: int
    tone: str
    sections: list[SectionSpec]
    #: T4 — human-facing name/blurb for a template picker. label defaults to
    #: the id (still functional, just not pretty) so a spec file added
    #: without them doesn't break loading.
    label: str = ""
    description: str = ""
    #: T4/P — a template not meant to appear in the product's picker (a
    #: proof-of-concept fixture, or an industry pack still being drafted).
    #: Still fully loadable by id -- existing tests/direct callers are
    #: unaffected -- just excluded from list_templates().
    hidden: bool = False
    #: P — industry-pack guidance appended to agent.py's system prompt
    #: verbatim (see agent._system_prompt_for). Empty for general-purpose
    #: templates; this is the one field an industry pack actually adds.
    prompt_guidance: str = ""

    def section(self, key: str) -> SectionSpec | None:
        return next((s for s in self.sections if s.key == key), None)


def _parse(raw: dict) -> TemplateSpec:
    return TemplateSpec(
        id=raw["id"],
        version=raw["version"],
        tone=raw.get("tone", "manager"),
        label=raw.get("label") or raw["id"],
        description=raw.get("description", ""),
        hidden=raw.get("hidden", False),
        prompt_guidance=raw.get("prompt_guidance", ""),
        sections=[
            SectionSpec(
                key=s["key"],
                label=s.get("label") or DEFAULT_SECTION_LABELS.get(s["key"], s["key"]),
                charts=[ChartSpec(**c) for c in s.get("charts", [])],
            )
            for s in raw["sections"]
        ],
    )


_CACHE: dict[tuple[str, int | None], TemplateSpec] = {}


def _available_versions(template_id: str) -> list[int]:
    versions = []
    for p in _SPEC_DIR.glob(f"{template_id}.v*.json"):
        suffix = p.stem.rsplit(".v", 1)[-1]
        if suffix.isdigit():
            versions.append(int(suffix))
    return sorted(versions)


def _available_template_ids() -> list[str]:
    ids = set()
    for p in _SPEC_DIR.glob("*.v*.json"):
        template_id = p.stem.rsplit(".v", 1)[0]
        ids.add(template_id)
    return sorted(ids)


def list_templates() -> list[dict]:
    """T4 — every non-hidden template's LATEST version, for a picker (see
    main.py's GET /api/templates). Adding a template is exactly "drop a new
    {id}.v1.json file" -- it shows up here with zero code changes, the same
    guarantee T1 proved for rendering."""
    out = []
    for template_id in _available_template_ids():
        spec = load_template(template_id)
        if spec.hidden:
            continue
        out.append({
            "id": spec.id, "label": spec.label, "description": spec.description,
            "version": spec.version, "tone": spec.tone,
            "sections": [s.key for s in spec.sections],
        })
    return out


def load_template(template_id: str, version: int | None = None) -> TemplateSpec:
    """version=None (default) resolves to the HIGHEST version currently on
    disk for this id -- "latest." Pass an explicit version to pin to it
    exactly (see report_builder.py's template_version param / scheduler.py's
    regenerate_run) -- raises ValueError rather than silently falling back
    to latest if that exact version no longer exists on disk, since a caller
    that asked to pin got to make that choice on purpose.

    Raises ValueError with the searched path on an unknown id/version — a
    missing template is a caller mistake, not a silently-empty report."""
    cache_key = (template_id, version)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    resolved_version = version
    if resolved_version is None:
        versions = _available_versions(template_id)
        if not versions:
            raise ValueError(f"unknown template_id={template_id!r} (no {template_id}.v*.json in {_SPEC_DIR})")
        resolved_version = versions[-1]

    path = _SPEC_DIR / f"{template_id}.v{resolved_version}.json"
    if not path.exists():
        raise ValueError(f"unknown template_id={template_id!r} version={resolved_version} (no {path} on disk)")
    spec = _parse(json.loads(path.read_text(encoding="utf-8")))
    _CACHE[cache_key] = spec
    _CACHE[(template_id, spec.version)] = spec  # so an explicit-version lookup hits this same entry
    return spec


def clear_cache() -> None:
    """Template files never change at runtime in production (a "bump" is a
    new file, not an edit) -- this exists for tests that simulate a bump by
    writing a new version file mid-test and need load_template("id") to
    re-resolve "latest" against it."""
    _CACHE.clear()


def render_section_charts(charts: list[ChartSpec], section_metrics: dict) -> list[dict]:
    """The generic replacement for the old hand-written per-section chart
    lists: every chart a section draws comes from the spec, not a hardcoded
    call site, so a template that wants fewer/different charts is a JSON
    edit, not a report_builder.py edit. Takes an explicit chart list (not a
    whole SectionSpec) so a caller can pass select_renderable_charts()'s
    filtered subset instead of every chart the spec declares."""
    out = []
    for cs in charts:
        builder = CHART_BUILDERS[cs.builder]
        args = [section_metrics[k] for k in cs.builder_args]
        out.append({"caption": cs.caption, "img": builder(*args)})
    return out


def select_renderable_charts(section_spec: SectionSpec, missing_columns: set[str]) -> tuple[list[ChartSpec], list[dict]]:
    """T2 — data-availability contract. Splits a section's declared charts
    into (renderable, omitted), where a chart is omitted the moment ANY
    source column it requires was missing from the upload (not just
    malformed -- entirely absent, i.e. parsers.py had to synthesize a
    default for every row). Never a silent blank/zero chart: every omission
    comes back with a stated reason, attached to the report's Data Quality
    section and the QA JSON by report_builder._finish_report."""
    renderable, omitted = [], []
    for cs in section_spec.charts:
        blocked = sorted(set(cs.requires_columns) & missing_columns)
        if blocked:
            omitted.append({
                "section": section_spec.key,
                "caption": cs.caption,
                "missing_columns": blocked,
                "reason": f"'{cs.caption}' was omitted: requires column(s) {', '.join(blocked)}, "
                          f"which weren't present in the uploaded {section_spec.key} data.",
            })
        else:
            renderable.append(cs)
    return renderable, omitted
