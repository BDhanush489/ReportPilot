"""Orchestrates one report generation: parse -> compute metrics -> chart -> write -> render PDF.

Two front doors feed the same core pipeline:
  - build_report(): CSV/XLSX file uploads (parsers.py)
  - build_report_from_data_context(): a saved warehouse connection + column
    mapping (data_context.py / sql_source.py)
Both produce identical metrics_payload/section_charts shapes, so everything
downstream — narrative generation, insights, chart rendering, PDF — is a
single shared code path with no per-source-type branching.
"""
from __future__ import annotations

import base64
import copy
import json
import os
import uuid
from datetime import date
from io import BytesIO

from jinja2 import Environment, FileSystemLoader, select_autoescape
from xhtml2pdf import pisa

from . import ai_usage, charts, cleaning, metrics as metrics_mod, parsers, qa, template_specs, theme
from .agent import generate_report
from .chart_annotation import detect_notable_point
from .chart_intelligence import choose_chart_type
from .insights import compute_insights
from .report_object import ChartRef, Period, ReportObject, SourceInfo, resolve_path

#: T1 — templates are declarative JSON (app/template_specs/*.json), not
#: Python branches. `_CHART_SPECS`/`_CHART_METRIC_PATHS` below are kept as
#: names/shapes several existing tests already import directly, but they're
#: now DERIVED from the "default" template spec on disk, not hand-written
#: literals -- the single source of truth moved to the JSON file.
_DEFAULT_TEMPLATE = template_specs.load_template("default")

_ChartSpec = template_specs.ChartSpec  # backward-compatible alias

_CHART_SPECS: dict[tuple[str, str], template_specs.ChartSpec] = {
    (section.key, chart.caption): chart
    for section in _DEFAULT_TEMPLATE.sections
    for chart in section.charts
}

#: Backward-compatible view some call sites/tests still want: (chart_type, metric_paths).
_CHART_METRIC_PATHS = {key: (spec.chart_type, spec.metric_paths) for key, spec in _CHART_SPECS.items()}


def _slug(text: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in text).strip("-")


def _resolve_for_suitability(path: str, metrics_payload: dict, series_payload: dict):
    root_name, _, rest = path.partition(".")
    root = {"metrics": metrics_payload, "series": series_payload}.get(root_name)
    if root is None:
        return None
    return resolve_path(root, rest)


def _build_chart_refs(section_charts: dict, ordered_keys: list[str],
                       metrics_payload: dict, series_payload: dict,
                       chart_specs: dict | None = None) -> list[ChartRef]:
    """chart_specs defaults to the "default" template's chart table for
    backward compatibility with existing call sites/tests; _finish_report
    passes the ACTIVE template's own chart specs so a non-default template's
    captions resolve correctly."""
    if chart_specs is None:
        chart_specs = _CHART_SPECS
    refs = []
    for key in ordered_keys:
        for i, chart in enumerate(section_charts.get(key, [])):
            spec = chart_specs.get((key, chart["caption"]))
            if spec is None:
                refs.append(ChartRef(
                    id=f"{key}-{i}-{_slug(chart['caption'])}", section=key, caption=chart["caption"],
                    img=chart["img"], chart_type="unknown", metric_paths=[],
                    suitability_verdict="ambiguous_data", suitability_reason="no chart spec registered for this caption",
                ))
                continue

            resolved = _resolve_for_suitability(spec.metric_paths[0], metrics_payload, series_payload)
            choice = choose_chart_type(resolved, spec.shape, spec.x_field, spec.y_field, spec.chart_type)
            annotation = detect_notable_point(resolved, spec.shape, spec.x_field, spec.y_field)

            refs.append(ChartRef(
                id=f"{key}-{i}-{_slug(chart['caption'])}",
                section=key,
                caption=chart["caption"],
                img=chart["img"],
                chart_type=spec.chart_type,
                metric_paths=spec.metric_paths,
                suitability_verdict=choice.verdict,
                suitability_reason=choice.reason,
                suitability_alternatives=choice.alternatives,
                annotation=annotation.to_dict() if annotation else None,
            ))
    return refs


def _json_safe_records(df) -> list[dict]:
    """DataFrame -> list[dict] via pandas' own JSON serializer, not
    .to_dict("records") -- real source data routinely has Timestamp-typed
    week/month columns (confirmed against sample_data/sales_pipeline.xlsx's
    monthly sheet), which .to_dict() leaves as native, non-JSON-serializable
    objects. Routing through pandas' to_json also gets NaT/NaN handling for
    free instead of hand-rolling it."""
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _build_series_payload(metrics_payload: dict) -> dict:
    """Promotes the DataFrame-backed private keys charts are drawn from into
    a JSON-serializable sibling namespace -- deliberately NOT into
    clean_payload/metrics, so qa.run_qa's traceability scan doesn't grow the
    haystack a fabricated narrative number could coincidentally match."""
    series: dict = {}
    if "analytics" in metrics_payload:
        a = metrics_payload["analytics"]
        series["analytics"] = {
            "weekly_by_channel": _json_safe_records(a["_weekly"]),
            "weekly_totals": _json_safe_records(a["_weekly_totals"]),
        }
    if "sales" in metrics_payload:
        series["sales"] = {"monthly": _json_safe_records(metrics_payload["sales"]["_monthly"])}
    return series


def _resolve_report_id(report_id: str | None) -> str:
    """The object must always carry a real id, even when called from a path
    that doesn't have one yet to thread through (smoke_test.py, direct
    build_report() calls in tests) — main.py's job_id wins whenever it's
    actually passed."""
    return report_id or uuid.uuid4().hex[:12]


def render_pdf_from_object(obj: ReportObject) -> tuple[str, bytes]:
    """The PDF/HTML renderer's only input is the canonical object -- no
    re-querying, no recompute. Rebuilds the legacy `report`-shaped dict
    report.html expects (charts reattached per section) so the template
    itself needs zero changes for F0."""
    legacy_report = obj.to_legacy_report_dict()
    html = _env.get_template("report.html").render(
        branding=obj.branding,
        report=legacy_report,
        generated_date=date.today().isoformat(),
        theme=theme.to_template_context(obj.branding.get("font_family")),
        qa=obj.qa,
    )
    return html, _html_to_pdf(html)

_env = Environment(
    loader=FileSystemLoader(searchpath=str(__import__("pathlib").Path(__file__).parent / "templates")),
    autoescape=select_autoescape(["html"]),
)


def _strip_private(payload):
    """Drop internal (leading-underscore) keys before this leaves for the LLM prompt."""
    if isinstance(payload, dict):
        return {k: _strip_private(v) for k, v in payload.items() if not k.startswith("_")}
    if isinstance(payload, list):
        return [_strip_private(v) for v in payload]
    return payload


#: Real pipeline stages, in order — shared with main.py so the progress stream
#: reports the actual stage this run is in, not a simulated timer.
STAGES = ["Parsing, cleaning & computing metrics", "Writing narrative", "Computing insights", "Building PDF", "Done"]


#: T1 — these three now compute ONLY metrics + a period label. Chart
#: rendering used to be hardcoded inline here; it's now driven entirely by
#: the active template spec (see template_specs.render_section_charts,
#: called once per section from _finish_report) so which charts a section
#: gets is a template's decision, not this function's.
def _analytics_section(df) -> tuple[dict, str]:
    a = metrics_mod.analytics_metrics(df)
    period_label = f"{a['date_range']['start']} to {a['date_range']['end']}"
    return a, period_label


def _seo_section(df) -> dict:
    return metrics_mod.seo_metrics(df)


def _sales_section(deals, monthly) -> tuple[dict, str | None]:
    sl = metrics_mod.sales_metrics(deals, monthly)
    period_label = None
    if not sl["_monthly"].empty:
        months = sl["_monthly"]["month"].astype(str)
        period_label = f"{months.iloc[0]} to {months.iloc[-1]}"
    return sl, period_label


def build_report(uploads: dict, branding: dict, on_stage=None, report_id: str | None = None,
                  template_id: str = "default", template_version: int | None = None) -> dict:
    """
    uploads: dict with optional keys 'analytics', 'seo', 'sales' -> (filename, file-like) tuples
    branding: {client_name, agency_name, primary_color, accent_color, logo_data_uri}
    on_stage: optional callback(stage_label: str), invoked as the pipeline moves
      through STAGES — lets callers stream real progress instead of a spinner.
    report_id: optional caller-assigned id (main.py threads its job_id through
      so the persisted report_object.json's id matches the report directory
      name) — falls back to a freshly generated one when omitted.
    template_id: which declarative spec (app/template_specs/*.json) decides
      section order/labels/charts/tone for this report — see template_specs.py (T1).
    template_version: T3 — pin to this exact spec version instead of
      "latest" (None). scheduler.regenerate_run passes this, reading it from
      a past report's own ReportObject.template_version, so template drift
      never silently changes an already-generated period on regeneration.
    """
    def stage(label: str) -> None:
        if on_stage:
            on_stage(label)

    stage(STAGES[0])
    metrics_payload: dict = {}
    source_fingerprints: dict = {}
    source_frames: dict = {}
    period_label = None
    all_issues: list[dict] = []

    if "analytics" in uploads:
        name, fh = uploads["analytics"]
        fh.name = name
        df, issues = parsers.load_web_analytics(fh)
        all_issues += issues
        if df.empty:
            raise ValueError(
                "The analytics file had no usable rows left after cleaning — check that it has a "
                "valid 'date' column."
            )
        metrics_payload["analytics"], period_label = _analytics_section(df)
        source_fingerprints["analytics"] = qa.compute_source_fingerprint(df)
        source_frames["analytics"] = df

    if "seo" in uploads:
        name, fh = uploads["seo"]
        fh.name = name
        df, issues = parsers.load_seo_audit(fh)
        all_issues += issues
        if df.empty:
            raise ValueError(
                "The SEO audit file had no usable rows left after cleaning — check that it has a "
                "valid 'url' column."
            )
        metrics_payload["seo"] = _seo_section(df)
        source_fingerprints["seo"] = qa.compute_source_fingerprint(df)
        source_frames["seo"] = df

    if "sales" in uploads:
        name, fh = uploads["sales"]
        fh.name = name
        deals, monthly, issues = parsers.load_sales_pipeline(fh)
        all_issues += issues
        metrics_payload["sales"], sales_period = _sales_section(deals, monthly)
        period_label = period_label or sales_period
        source_fingerprints["sales"] = qa.compute_source_fingerprint(deals)
        source_frames["sales"] = (deals, monthly)

    if not metrics_payload:
        raise ValueError("No recognized data files were provided.")

    return _finish_report(metrics_payload, period_label, branding, stage, all_issues,
                           source_fingerprints, source_frames=source_frames, report_id=report_id,
                           template_id=template_id, template_version=template_version)


#: Two ways a data_context can be sourced without a file being handed to us
#: directly: a SQL warehouse table (the original shape, below), or a
#: mailbox/Slack channel to pull fresh attachments from on every call. Kept
#: as a set (not an if/else scattered across the function) so a third inbox
#: kind is one line to add, not a new branch shape.
_INBOX_CONNECTOR_KINDS = {"imap_inbox", "slack_inbox"}


def _build_uploads_from_inbox_context(kind: str, config: dict) -> tuple[dict, list]:
    """kind == "imap_inbox" -> Gmail/Outlook via IMAP; kind == "slack_inbox"
    -> a Slack channel via the Web API. Both connectors implement the same
    fetch_attachments() shape (see slack_source.py's docstring), so
    email_source.build_uploads_from_inbox() assembles the uploads dict
    identically either way — "where the files came from" is decided once,
    right here, not duplicated per call site."""
    from . import email_source, slack_source

    if kind == "imap_inbox":
        connector = email_source.create_inbox_connector(
            config["provider"], config["username"], config["password"],
        )
    elif kind == "slack_inbox":
        connector = slack_source.create_slack_connector(config["bot_token"], config["channel_id"])
    else:
        raise ValueError(f"unknown inbox connector kind {kind!r}")

    try:
        return email_source.build_uploads_from_inbox(
            connector, mailbox=config.get("mailbox", "INBOX"), search=config.get("search", "UNSEEN"),
            limit=config.get("limit", 20), mark_as_read=config.get("mark_as_read", False),
        )
    finally:
        connector.close()


def build_report_from_data_context(tenant_id: str, client_id: str, branding: dict, on_stage=None,
                                    report_id: str | None = None, template_id: str = "default",
                                    template_version: int | None = None) -> dict:
    """Same pipeline as build_report(), sourced from a saved connection
    instead of an upload handed to this call directly — either a SQL
    warehouse table (the original shape) or a mailbox/Slack channel to fetch
    fresh attachments from on every call ("hosted inbox polling": a schedule
    whose data_source_ref points at an inbox context regenerates from
    whatever's newly arrived each time the scheduler fires it, exactly like
    a warehouse context regenerates from whatever rows are newly in the
    table — same idempotency/cadence machinery in scheduler.py, no changes
    needed there). Requires data_context.py to have a saved context for this
    tenant_id/client_id (see main.py's /api/data-sources/onboard, onboard-inbox, or
    onboard-slack)."""
    from . import data_context, sql_source
    from .connectors import create_connector

    def stage(label: str) -> None:
        if on_stage:
            on_stage(label)

    stage(STAGES[0])
    context = data_context.load_data_context(tenant_id, client_id)
    if not context:
        raise ValueError(f"No data context saved for client_id={client_id!r}. Onboard it first.")

    connector_kind = context["connector"]["kind"]
    if connector_kind in _INBOX_CONNECTOR_KINDS:
        uploads, _unmatched = _build_uploads_from_inbox_context(connector_kind, context["connector"]["config"])
        if not uploads:
            raise ValueError(
                f"No matching attachments found for client_id={client_id!r} ({connector_kind}) "
                "— nothing to generate a report from this run."
            )
        return build_report(uploads, branding, on_stage=on_stage, report_id=report_id, template_id=template_id,
                             template_version=template_version)

    connector = create_connector(connector_kind, context["connector"]["config"])
    try:
        metrics_payload: dict = {}
        source_fingerprints: dict = {}
        source_frames: dict = {}
        period_label = None
        all_issues: list[dict] = []
        sources = context["sources"]

        if "analytics" in sources:
            df, issues = sql_source.load_analytics_from_sql(connector, sources["analytics"])
            all_issues += issues
            if df.empty:
                raise ValueError("The analytics table had no usable rows left after cleaning.")
            metrics_payload["analytics"], period_label = _analytics_section(df)
            source_fingerprints["analytics"] = qa.compute_source_fingerprint(df)
            source_frames["analytics"] = df

        if "seo" in sources:
            df, issues = sql_source.load_seo_from_sql(connector, sources["seo"])
            all_issues += issues
            if df.empty:
                raise ValueError("The SEO table had no usable rows left after cleaning.")
            metrics_payload["seo"] = _seo_section(df)
            source_fingerprints["seo"] = qa.compute_source_fingerprint(df)
            source_frames["seo"] = df

        if "sales" in sources:
            deals, monthly, issues = sql_source.load_sales_from_sql(connector, sources["sales"])
            all_issues += issues
            metrics_payload["sales"], sales_period = _sales_section(deals, monthly)
            period_label = period_label or sales_period
            source_fingerprints["sales"] = qa.compute_source_fingerprint(deals)
            source_frames["sales"] = (deals, monthly)

        if not metrics_payload:
            raise ValueError("This client's data context has no usable sources.")

        return _finish_report(metrics_payload, period_label, branding, stage, all_issues,
                               source_fingerprints, source_frames=source_frames, report_id=report_id,
                               template_id=template_id, template_version=template_version)
    finally:
        connector.close()


def _finish_report(metrics_payload: dict, period_label: str | None,
                    branding: dict, stage, cleaning_issues: list[dict] | None = None,
                    source_fingerprints: dict | None = None, source_frames: dict | None = None,
                    report_id: str | None = None, template_id: str = "default",
                    template_version: int | None = None) -> dict:
    """Shared tail of both front doors: narrative -> insights -> QA ->
    canonical object -> PDF render. Identical regardless of where the rows
    came from. The PDF is rendered from the canonical ReportObject (via
    render_pdf_from_object), not from a separately-threaded copy of the
    narrative — so "the object is what got rendered" is actually true, not
    just an artifact built alongside the real render path.

    T1: which sections render, in what order, with which charts and what
    narrative tone, is entirely decided by the loaded template spec — not by
    a hardcoded SECTION_ORDER. A section present in metrics_payload but not
    in the spec is simply not rendered (the template selected it out); a
    metrics_payload with zero overlap against the spec is a caller error,
    not a silently empty report."""
    spec = template_specs.load_template(template_id, version=template_version)
    ordered_keys = [s.key for s in spec.sections if s.key in metrics_payload]
    if not ordered_keys:
        raise ValueError(
            f"template_id={template_id!r} has no section that matches the available data "
            f"({sorted(metrics_payload)}) — nothing to report."
        )
    # Only the sections this template actually renders become part of the
    # canonical object -- metrics, series, and narrative all trace to what's
    # on the page, not to every section that happened to be uploaded.
    metrics_payload = {k: v for k, v in metrics_payload.items() if k in ordered_keys}

    # T2 — data-availability contract: a "column_missing" issue (see
    # cleaning.missing_column, emitted by parsers.py when a source column
    # was entirely absent, not just messy) means every row for that column
    # was a synthesized default -- rendering a chart built on it would look
    # like real data ("$0 revenue") instead of "we don't have this."
    cleaning_issues = list(cleaning_issues or [])
    missing_columns_by_source: dict[str, set[str]] = {}
    for issue in cleaning_issues:
        if issue.get("kind") == "column_missing":
            missing_columns_by_source.setdefault(issue["source"], set()).add(issue["column"])

    section_charts: dict[str, list[dict]] = {}
    omitted_charts: list[dict] = []
    for key in ordered_keys:
        section_spec = spec.section(key)
        renderable, omitted = template_specs.select_renderable_charts(
            section_spec, missing_columns_by_source.get(key, set()))
        section_charts[key] = template_specs.render_section_charts(renderable, metrics_payload[key])
        for note in omitted:
            cleaning_issues.append(cleaning.chart_omitted(note["section"], note["caption"], note["reason"]))
        omitted_charts.extend(omitted)

    sections_requested = [spec.section(key).label for key in ordered_keys]
    chart_specs = {(s.key, c.caption): c for s in spec.sections for c in s.charts}

    period_label = period_label or "Current Period"
    clean_payload = _strip_private(copy.deepcopy(metrics_payload))
    clean_payload["period_label"] = period_label
    series_payload = _build_series_payload(metrics_payload)

    stage(STAGES[1])
    # Only consult (and spend from) the global daily cap when a key is even
    # configured -- avoids an unnecessary DB write on every report in any
    # deployment that has no ANTHROPIC_API_KEY at all (agent.py's own `if
    # api_key` gate would make claude_allowed moot there anyway).
    claude_allowed = ai_usage.try_consume() if os.environ.get("ANTHROPIC_API_KEY") else True
    report = generate_report(clean_payload, branding, sections_requested, tone=spec.tone,
                              prompt_guidance=spec.prompt_guidance, claude_allowed=claude_allowed)

    stage(STAGES[2])
    # Computed independently of the LLM/template narrative path, so these
    # always show up — same trust guarantee as every other number in the app.
    report["insights"] = compute_insights(metrics_payload)
    # Same rule: what got cleaned in ingestion is arithmetic on the raw
    # values, never an LLM's word for it — see cleaning.py.
    report["data_quality"] = cleaning.summarize(cleaning_issues or [])

    # Snapshot the narrative BEFORE charts get attached below -- the
    # canonical object's charts live once, in the flat chart_refs list;
    # `narrative` never carries a duplicate copy of them.
    narrative_only = copy.deepcopy(report)

    chart_refs = _build_chart_refs(section_charts, ordered_keys, clean_payload, series_payload, chart_specs)

    # Legacy per-section attachment stays: `report` (not narrative_only) is
    # what the existing "report" return key / dashboard / /api/report still
    # read, unchanged, for backward compatibility.
    for i, section in enumerate(report.get("sections", [])):
        key = ordered_keys[i] if i < len(ordered_keys) else None
        section["charts"] = section_charts.get(key, [])

    stage(STAGES[3])

    qa_report = qa.run_qa(narrative_only, clean_payload, source_frames or {}, charts=chart_refs)
    qa_dict = qa_report.to_dict()
    # T2 — "the omission is visible ... in the QA JSON": an omitted chart is
    # a deliberate degrade, not a QA failure, so it's additive here and
    # never flips qa_dict["badge"].
    if omitted_charts:
        qa_dict["data_availability"] = {"omitted_charts": omitted_charts}

    obj = ReportObject(
        report_id=_resolve_report_id(report_id),
        period=Period(label=period_label),
        sources={k: SourceInfo(**v) for k, v in (source_fingerprints or {}).items()},
        metrics=clean_payload,
        series=series_payload,
        charts=chart_refs,
        narrative=narrative_only,
        qa=qa_dict,
        branding=branding,
        section_order=ordered_keys,
        # T3: the RESOLVED id/version, not the caller's (possibly None/"latest")
        # request -- so a regeneration later pins to exactly this, regardless
        # of how many newer versions exist by then.
        template_id=spec.id,
        template_version=spec.version,
    )

    html, pdf_bytes = render_pdf_from_object(obj)

    return {
        "html": html,
        "pdf_bytes": pdf_bytes,
        "report": report,
        "metrics": clean_payload,
        "source_fingerprints": source_fingerprints or {},
        "report_object": obj,
    }


def _html_to_pdf(html: str) -> bytes:
    buf = BytesIO()
    pisa.CreatePDF(src=html, dest=buf)
    return buf.getvalue()
