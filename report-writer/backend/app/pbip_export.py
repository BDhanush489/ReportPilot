"""
Track D2 — Power BI export: a Power BI Project (PBIP/TMDL) generated from a
canonical ReportObject, not from a client's raw files. Covers D2.0
(parameterized semantic model), D2.1 (chart -> visual mapping), D2.2
(measure correctness), and D2.3 (live warehouse connection) — D2.4 (publish
to workspace) stays GATED on real Power BI/Fabric OAuth, and D2.5 (human-
verified open test) needs real Power BI Desktop, neither available in this
environment. Parameterizes the hand-built generator at d:\\IMDollars\\powerbi\\
build_pbip.py (kept there, untouched, as the design reference for D2.1's
visual layout) into a real function of any ReportObject.

FRAMING: Power BI does not import graphs. It imports a MODEL — this module
never produces a PNG; it produces TMDL tables a Power BI report can bind
visuals to (that binding is D2.1's job, not this one).

The one fact that shapes this whole module: ReportObject never carries raw
rows, only `metrics` (curated aggregates) and `series` (weekly/monthly
rollups) — see report_object.py's own docstring for why, and F0's
CHANGELOG entry for the QA-traceability reasoning behind that split. Raw
uploaded/fetched files aren't persisted anywhere in this pipeline either.
So this generator cannot build a row-level star schema the way the old
hand-built demo does; it builds a small set of already-aggregated tables
(one row per channel, one row per month, ...) instead. Every table below is
extracted straight off ReportObject.resolve() — the exact same dotted-path
mechanism report_builder.py's chart specs already use — never a client-
specific literal, so a different ReportObject (different client, different
sections present) produces a different but equally valid model, per D2.0's
own exit criteria.

Data is embedded directly into each table's TMDL partition as a Power Query
`#table(...)` M literal, not read from an external CSV via a machine-path
parameter (the old demo's `SampleDataFolder` parameter is exactly that kind
of non-portable, non-deterministic dependency — it bakes an absolute local
path into expressions.tmdl). Embedding means: no external file dependency
at export time, no path to go stale when the project moves, and the same
ReportObject in always produces byte-identical TMDL out — the last of
D2.0's exit criteria, and the thing the old demo does not actually satisfy.

GLOBAL INVARIANT: since Power BI only ever sees numbers ReportPilot already
computed (never raw rows), it cannot compute a different answer — D2.2's
"does the DAX measure reconcile with ReportObject" is a stronger guarantee
here by construction than in a row-level model, not a weaker one.
"""
from __future__ import annotations

import base64
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import theme
from .report_object import ReportObject

SCHEMA_BASE = "https://developer.microsoft.com/json-schemas/fabric"

#: Fixed, arbitrary namespace for uuid5 -- what matters is that it never
#: changes between runs, not what it is. Using uuid4 here (like the old
#: hand-built demo does) would make every regeneration non-deterministic,
#: failing D2.0's "byte-identical PBIP out across two runs" criterion.
_UUID_NAMESPACE = uuid.UUID("c9c918fa-9b3b-5a11-8f0a-7c4b1e6a2d10")


def _w(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\r\n")


def _wj(path: Path, obj: dict) -> None:
    _w(path, json.dumps(obj, indent=2, ensure_ascii=False))


def _sanitize_project_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", name or "")
    return cleaned or "Report"


def _logical_id(project_name: str, item_type: str) -> str:
    return str(uuid.uuid5(_UUID_NAMESPACE, f"{project_name}:{item_type}"))


# ---------------------------------------------------------------------------
# Power Query M literal formatting -- how a Python value becomes an embedded
# table cell. Order matters: bool before int (isinstance(True, int) is True
# in Python), None before everything else.
# ---------------------------------------------------------------------------

def _m_literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return '"' + str(value).replace('"', '""') + '"'


def _tmdl_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "int64"
    if isinstance(value, float):
        return "double"
    return "string"


def _default_summarize(dtype: str, name: str) -> str:
    """Naming-convention heuristic, not a per-column hardcoded table --
    matches the exact `_pct`/"rate"/`_usd`/"revenue"/"amount" convention
    already established (and tested) in html_dashboard.py's dashboard.html
    JS formatCell(), so a column's default aggregation behavior is
    consistent across every ReportPilot surface, not invented fresh here."""
    if dtype in ("string", "boolean"):
        return "none"
    lower = name.lower()
    if lower.endswith("_pct") or "rate" in lower or lower.startswith("avg_") or lower.endswith("_position"):
        return "average"
    if dtype in ("int64", "double"):
        return "sum"
    return "none"


def _format_string(dtype: str, name: str) -> str | None:
    """Same naming heuristic as _default_summarize, one format string per
    column so numbers render like every other ReportPilot surface instead
    of a raw float. CRITICAL: metrics.py's own *_pct fields are already
    multiplied by 100 (e.g. win_rate_pct = round(won/(won+lost)*100, 1) --
    see this session's read of metrics.py) -- the same convention
    theme.format_percent() assumes (f"{value:.1f}%", no *100 inside the
    f-string). A real DAX percent format token ("0.0%") ALSO multiplies by
    100, which would double it (38.2 -> "3820.0%"). The quoted literal
    "%" suffix below sidesteps that: it's plain 0.0 formatting with a
    literal percent character appended, not the percent operator."""
    if dtype not in ("int64", "double"):
        return None
    lower = name.lower()
    if lower.endswith("_pct") or "rate" in lower:
        return '0.0"%"'
    if lower.endswith("_usd") or "revenue" in lower or "amount" in lower:
        return "$#,##0" if dtype == "int64" else "$#,##0.00"
    if dtype == "int64":
        return "#,##0"
    return "#,##0.00"


# ---------------------------------------------------------------------------
# Shape extraction -- turns a resolved ReportObject value into (columns,
# rows) for one TMDL table. Mirrors chart_intelligence.py's own "records" /
# "dict_counts" / "pairs" shape vocabulary on purpose (same three shapes,
# same meaning) plus "scalar_dict" for the KPI/summary tables charts never
# need but a semantic model does.
# ---------------------------------------------------------------------------

_Columns = list[tuple[str, str]]
_Rows = list[tuple]


def _extract_records(resolved: Any) -> tuple[_Columns, _Rows] | None:
    if not isinstance(resolved, list) or not resolved:
        return None
    keys: list[str] = []
    seen: set[str] = set()
    for row in resolved:
        if not isinstance(row, dict):
            return None
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                keys.append(k)
    types: dict[str, str] = {}
    for k in keys:
        dtype = "string"
        for row in resolved:
            v = row.get(k)
            if v is not None:
                dtype = _tmdl_type(v)
                break
        types[k] = dtype
    columns = [(k, types[k]) for k in keys]
    rows = [tuple(row.get(k) for k in keys) for row in resolved]
    return columns, rows


def _extract_dict_counts(resolved: Any, column_names: tuple[str, str]) -> tuple[_Columns, _Rows] | None:
    if not isinstance(resolved, dict) or not resolved:
        return None
    cat_col, count_col = column_names
    count_type = _tmdl_type(next(iter(resolved.values())))
    columns = [(cat_col, "string"), (count_col, count_type)]
    rows = [(k, v) for k, v in resolved.items()]
    return columns, rows


def _extract_pairs(resolved: Any, column_names: tuple[str, str]) -> tuple[_Columns, _Rows] | None:
    if not isinstance(resolved, list) or not resolved:
        return None
    name_col, count_col = column_names
    try:
        rows = [(p[0], p[1]) for p in resolved]
    except (IndexError, TypeError, KeyError):
        return None
    columns = [(name_col, "string"), (count_col, _tmdl_type(rows[0][1]))]
    return columns, rows


def _extract_scalar_dict(report_object: ReportObject, field_paths: list[tuple[str | None, str]]) -> tuple[_Columns, _Rows] | None:
    """field_paths: (field_name, path) to take one scalar under that name, or
    (None, path) to merge every key of the dict at that path in directly --
    lets a table like seo's "Totals" assemble from several sibling metrics
    keys (some bare scalars, one nested dict) without a section-specific
    special case anywhere else in this module."""
    columns: _Columns = []
    values: list[Any] = []
    for field_name, path in field_paths:
        resolved = report_object.resolve(path)
        if resolved is None:
            return None  # any missing piece means this summary table isn't available this run
        if field_name is not None:
            columns.append((field_name, _tmdl_type(resolved)))
            values.append(resolved)
        else:
            if not isinstance(resolved, dict):
                return None
            for k, v in resolved.items():
                columns.append((k, _tmdl_type(v)))
                values.append(v)
    return columns, [tuple(values)]


# ---------------------------------------------------------------------------
# Table spec -- what tables this model has, derived from the real shapes
# metrics.py / report_builder.py actually produce (see this session's read
# of both). Not a fixed *schema* (the exit criterion's actual concern): add
# a client with only "analytics", and every seo/sales entry below simply
# resolves to None and is skipped-with-reason, not a crash.
# ---------------------------------------------------------------------------

@dataclass
class _TableSpec:
    section: str
    name: str
    shape: str  # "records" | "dict_counts" | "pairs" | "scalar_dict"
    source: Any  # str path for records/dict_counts/pairs; list[(name|None, path)] for scalar_dict
    column_names: tuple[str, str] | None = None  # dict_counts / pairs only


#: Table names are section-prefixed (AnalyticsTotals, not Totals) so every
#: table name is unique across the whole model -- three sections each
#: naturally want a "Totals" summary table, and without the prefix the
#: second and third would silently overwrite the first's .tmdl file on disk
#: (caught by generating against a real 3-section report before this fix).
_TABLE_SPECS: list[_TableSpec] = [
    _TableSpec("analytics", "AnalyticsTotals", "scalar_dict", [(None, "metrics.analytics.totals")]),
    _TableSpec("analytics", "AnalyticsByChannel", "records", "metrics.analytics.by_channel"),
    _TableSpec("analytics", "AnalyticsByDevice", "records", "metrics.analytics.by_device"),
    _TableSpec("analytics", "AnalyticsWeeklyByChannel", "records", "series.analytics.weekly_by_channel"),
    _TableSpec("analytics", "AnalyticsWeeklyTotals", "records", "series.analytics.weekly_totals"),

    _TableSpec("seo", "SeoTotals", "scalar_dict", [
        ("total_urls_crawled", "metrics.seo.total_urls_crawled"),
        ("indexable_pct", "metrics.seo.indexable_pct"),
        ("avg_load_time_ms", "metrics.seo.avg_load_time_ms"),
        (None, "metrics.seo.search_performance"),
    ]),
    _TableSpec("seo", "SeoSeverityCounts", "dict_counts", "metrics.seo.severity_counts", ("severity", "count")),
    _TableSpec("seo", "SeoTopIssues", "pairs", "metrics.seo.top_issues", ("issue", "count")),

    _TableSpec("sales", "SalesTotals", "scalar_dict", [(None, "metrics.sales.totals")]),
    _TableSpec("sales", "SalesByRep", "records", "metrics.sales.by_rep"),
    _TableSpec("sales", "SalesByLeadSource", "records", "metrics.sales.by_lead_source"),
    _TableSpec("sales", "SalesByProduct", "records", "metrics.sales.by_product"),
    _TableSpec("sales", "SalesMonthly", "records", "series.sales.monthly"),

    #: Content the PDF path never charts (no _CHART_SPECS entry references
    #: these) but metrics.py already computes -- real additional content for
    #: the dashboard, not synthesized. Shown as tableEx visuals, see
    #: _EXTRA_TABLE_SPECS below.
    _TableSpec("seo", "SeoWorstPages", "records", "metrics.seo.worst_pages"),
    _TableSpec("seo", "SeoOpportunityPages", "records", "metrics.seo.opportunity_pages"),
]


# ---------------------------------------------------------------------------
# D2.2 — measure correctness. GLOBAL INVARIANT restated for this specific
# surface: Power BI only ever sees numbers ReportPilot already computed
# (pre-aggregated tables, never raw rows -- see this module's own header),
# so a DAX measure here can only be SUM/DIVIDE over an already-correct
# column. There's no live Power BI engine in this pipeline to actually
# EXECUTE the DAX and check its answer (that's D2.5 -- a human, in real
# Desktop) -- what CAN be verified deterministically, right now, is that
# summing the exact rows about to be embedded (the same rows a live SUM()
# measure would operate over) reconciles with the canonical ReportObject
# value the measure claims to represent. A mismatch here means the table
# and the canonical metric have silently diverged -- exactly the class of
# bug qa.check_aggregation_sanity exists to catch for the PDF/dashboard,
# extended to this export surface instead of leaving it uncovered.
# ---------------------------------------------------------------------------

@dataclass
class _MeasureSpec:
    table: str
    name: str
    dax: str
    format_string: str
    #: Which column of `table`'s rows to sum for the pandas-style recompute
    #: -- must be the same column `dax` aggregates.
    recompute_column: str
    #: Dotted ReportObject path this measure's recompute must reconcile
    #: with, within tolerance.
    reconciles_with: str


#: One canonical "Total X" measure per KPI already shown elsewhere in this
#: report (PDF/dashboard) -- hosted on whichever table's rows sum to that
#: total. Deliberately NOT one per table per column: AnalyticsByDevice's
#: sessions column sums to the exact same total as AnalyticsByChannel's, and
#: a second "Total Sessions" measure there would just be a duplicate DAX
#: name collision waiting to happen, not a second real fact.
#: SeoTopIssues has no entry here on purpose: it's a top-8 subset (see
#: metrics.seo_metrics's issue_counts.head(8)), not exhaustive, so summing
#: it does NOT equal any canonical total -- reconciling it against one
#: would be asserting something structurally false.
_MEASURE_SPECS: list[_MeasureSpec] = [
    _MeasureSpec("AnalyticsByChannel", "Total Revenue", "SUM(AnalyticsByChannel[revenue_usd])",
                 "$#,##0.00", "revenue_usd", "metrics.analytics.totals.revenue_usd"),
    _MeasureSpec("AnalyticsByChannel", "Total Sessions", "SUM(AnalyticsByChannel[sessions])",
                 "#,##0", "sessions", "metrics.analytics.totals.sessions"),
    _MeasureSpec("AnalyticsByChannel", "Total Conversions", "SUM(AnalyticsByChannel[conversions])",
                 "#,##0", "conversions", "metrics.analytics.totals.conversions"),
    _MeasureSpec("SalesByRep", "Sales Total Revenue", "SUM(SalesByRep[revenue_usd])",
                 "$#,##0.00", "revenue_usd", "metrics.sales.totals.revenue_usd"),
    _MeasureSpec("SeoSeverityCounts", "Total Pages Crawled", "SUM(SeoSeverityCounts[count])",
                 "#,##0", "count", "metrics.seo.total_urls_crawled"),
]


def _verify_measures(report_object: ReportObject, rows_by_table: dict[str, tuple[_Columns, _Rows]]) -> list[dict]:
    """Returns every measure whose pandas-style recompute disagrees with the
    canonical value beyond tolerance -- empty means every measure reconciles.
    A table that wasn't written this run (spec.reconciles_with unavailable,
    or the host table itself skipped) is simply not checked, not a failure —
    same "skip with a reason, never silently pretend" policy as everywhere
    else in this module, just inverted: nothing to verify isn't a mismatch."""
    mismatches = []
    for spec in _MEASURE_SPECS:
        table_data = rows_by_table.get(spec.table)
        if table_data is None:
            continue
        columns, rows = table_data
        col_names = [c[0] for c in columns]
        if spec.recompute_column not in col_names:
            continue
        idx = col_names.index(spec.recompute_column)
        recomputed = sum(row[idx] for row in rows)
        canonical = report_object.resolve(spec.reconciles_with)
        if canonical is None or not isinstance(canonical, (int, float)) or isinstance(canonical, bool):
            continue
        tolerance = max(1e-6, abs(canonical) * 1e-6)
        if abs(recomputed - canonical) > tolerance:
            mismatches.append({
                "measure": spec.name, "table": spec.table, "recomputed": recomputed,
                "canonical": canonical, "path": spec.reconciles_with,
            })
    return mismatches


class MeasureReconciliationError(ValueError):
    """D2.2: FAIL blocks export. Raised by build_pbip() instead of writing a
    workbook whose DAX measures would show a number that disagrees with the
    canonical report — never shipped, not even with a warning label."""


# ---------------------------------------------------------------------------
# D2.3 — live warehouse connection. Snapshot mode (default, unchanged above)
# embeds a frozen M-literal; live mode instead emits a DirectQuery table
# that connects straight to the client's own warehouse table on refresh.
#
# ARCHITECTURAL BOUNDARY, stated not hidden: D2.0-D2.2's tables are
# pre-aggregated (AnalyticsByChannel is a groupby over the raw analytics
# rows, computed by metrics.py) -- there is no 1:1 warehouse table matching
# that shape. Replicating metrics.py's groupby/derived-column logic as a
# SECOND implementation in Power Query M would violate the mission's own
# "one canonical source, many renderers" invariant (a DAX/M recompute that
# could silently drift from metrics.py's answer). So live mode DirectQueries
# the RAW source table instead (same table data_context.py's `sources`
# config already names) with a straight column rename, no aggregation, no
# derived columns -- genuinely live, genuinely zero second metrics path,
# at the honest cost of not being the pre-aggregated shape snapshot mode
# ships. Full DAX-measure parity with metrics.py on top of these raw live
# tables is real future work, not silently claimed here.
#
# SECURITY: no plaintext credentials are ever embedded. Every branch below
# extracts only non-secret connection topology (host/port/database/account/
# warehouse/catalog/schema) from the stored connector config and discards
# user/password/token/dsn-with-credentials entirely -- Power BI Desktop
# prompts for credentials on first refresh and stores them in its own
# credential manager, never in the .pbix/.pbip text on disk.
# ---------------------------------------------------------------------------

class UnsupportedLiveConnection(ValueError):
    """Raised when connection_mode="live" is requested for a connector kind
    with no native Power Query connector (sqlite has none) — snapshot mode
    stays selectable and is the honest answer for that kind, never a silent
    substitution the caller didn't ask for."""


_LIVE_SECTION_TABLES = {"analytics": "AnalyticsLive", "seo": "SeoLive", "sales": "SalesLive"}


def _live_nav_m(connector_kind: str, config: dict, table_name: str) -> str:
    """Returns the M expression that navigates a connector's native Power
    Query source function down to one table, using only non-secret config."""
    if connector_kind == "postgres":
        import urllib.parse
        parsed = urllib.parse.urlparse(config["dsn"])
        host = parsed.hostname or "localhost"
        port = parsed.port or 5432
        database = (parsed.path or "/").lstrip("/")
        return f'PostgreSQL.Database("{host}:{port}", "{database}"){{[Schema="public",Item="{table_name}"]}}[Data]'
    if connector_kind == "snowflake":
        return (
            f'Snowflake.Databases("{config["account"]}.snowflakecomputing.com", "{config["warehouse"]}")'
            f'{{[Name="{config["database"]}"]}}[Data]'
            f'{{[Name="{config.get("schema", "PUBLIC")}"]}}[Data]'
            f'{{[Name="{table_name}",Kind="Table"]}}[Data]'
        )
    if connector_kind == "bigquery":
        return (
            f'GoogleBigQuery.Database(){{[Name="{config["project_id"]}"]}}[Data]'
            f'{{[Name="{config["dataset"]}"]}}[Data]{{[Name="{table_name}",Kind="Table"]}}[Data]'
        )
    if connector_kind == "databricks":
        return (
            f'Databricks.Catalogs("{config["server_hostname"]}", "{config["http_path"]}", '
            f'[Catalog="{config["catalog"]}"])'
            f'{{[Name="{config["schema"]}"]}}[Data]{{[Name="{table_name}",Kind="Table"]}}[Data]'
        )
    raise UnsupportedLiveConnection(
        f"connector kind {connector_kind!r} has no native Power Query connector, so live mode isn't "
        f"available for it -- use connection_mode='snapshot' (the default) instead."
    )


def _build_live_table_tmdl(pbip_table_name: str, connector_kind: str, config: dict,
                            source_table: str, column_map: dict[str, str]) -> str:
    """column_map: {canonical_name: warehouse_column_name}, the exact dict
    data_context.py's `sources[section]["column_map"]` already carries --
    reused verbatim, not a second mapping UI. Only a rename step (1:1,
    no computation) so the live table's column names match every other
    ReportPilot surface's naming without a second schema-mapping decision."""
    nav = _live_nav_m(connector_kind, config, source_table)
    rename_pairs = ", ".join(f'{{"{warehouse_col}", "{canonical}"}}' for canonical, warehouse_col in column_map.items())
    lines = [
        f"table {pbip_table_name}",
        "",
        f"\tpartition {pbip_table_name}-Partition = m",
        "\t\tmode: directQuery",
        "\t\tsource =",
        "\t\t\tlet",
        f"\t\t\t\tSource = {nav},",
        f"\t\t\t\tRenamed = Table.RenameColumns(Source, {{{rename_pairs}}}, MissingField.Ignore)",
        "\t\t\tin",
        "\t\t\t\tRenamed",
        "",
    ]
    for canonical in column_map:
        cname_q = f"'{canonical}'" if not canonical.isidentifier() else canonical
        lines.append(f"\tcolumn {cname_q}")
        lines.append(f"\t\tsourceColumn: {canonical}")
        lines.append("\t\tsummarizeBy: none")  # raw/live rows -- no aggregation claim baked in here
        lines.append("")
    return "\n".join(lines)


def _resolve_table(report_object: ReportObject, spec: _TableSpec) -> tuple[_Columns, _Rows] | None:
    if spec.shape == "scalar_dict":
        return _extract_scalar_dict(report_object, spec.source)
    resolved = report_object.resolve(spec.source)
    if spec.shape == "records":
        return _extract_records(resolved)
    if spec.shape == "dict_counts":
        return _extract_dict_counts(resolved, spec.column_names)
    if spec.shape == "pairs":
        return _extract_pairs(resolved, spec.column_names)
    raise ValueError(f"unknown table shape {spec.shape!r}")


# ---------------------------------------------------------------------------
# TMDL emission
# ---------------------------------------------------------------------------

def _build_partition_literal_m(table_name: str, columns: _Columns, rows: _Rows) -> str:
    col_names_m = ", ".join(f'"{c[0]}"' for c in columns)
    row_strs = ["{" + ", ".join(_m_literal(v) for v in row) + "}" for row in rows]
    rows_m = ", ".join(row_strs)
    return (
        f"\tpartition {table_name}-Partition = m\n"
        f"\t\tmode: import\n"
        f"\t\tsource =\n"
        f"\t\t\tlet\n"
        f"\t\t\t\tSource = #table({{{col_names_m}}}, {{{rows_m}}})\n"
        f"\t\t\tin\n"
        f"\t\t\t\tSource\n"
    )


def _build_table_tmdl(name: str, columns: _Columns, rows: _Rows,
                       measures: list[_MeasureSpec] | None = None) -> str:
    lines = [f"table {name}", "", _build_partition_literal_m(name, columns, rows)]
    for measure in measures or []:
        name_q = f"'{measure.name}'" if not measure.name.isidentifier() else measure.name
        lines.append(f"\tmeasure {name_q} = {measure.dax}")
        lines.append(f"\t\tformatString: {measure.format_string}")
        lines.append("")
    for cname, dtype in columns:
        cname_q = f"'{cname}'" if not cname.isidentifier() else cname
        lines.append(f"\tcolumn {cname_q}")
        lines.append(f"\t\tdataType: {dtype}")
        lines.append(f"\t\tsourceColumn: {cname}")
        lines.append(f"\t\tsummarizeBy: {_default_summarize(dtype, cname)}")
        fmt = _format_string(dtype, cname)
        if fmt:
            lines.append(f"\t\tformatString: {fmt}")
        lines.append("")
    return "\n".join(lines)


def _build_platform(folder: Path, project_name: str, item_type: str, display_name: str) -> None:
    _wj(folder / ".platform", {
        "version": "2.0",
        "$schema": f"{SCHEMA_BASE}/platform/platformProperties.json",
        "config": {"logicalId": _logical_id(project_name, item_type)},
        "metadata": {"type": item_type, "displayName": display_name},
    })


def build_pbip(report_object: ReportObject, out_dir: Path, connection_mode: str = "snapshot",
               data_context: dict | None = None) -> dict:
    """Writes {project_name}.SemanticModel/ (D2.0's tables) and, when the
    object has at least one chart, {project_name}.Report/ + the .pbip root
    file too (D2.1's visuals, one page per section that has charts). A
    report with zero charts still gets its semantic model -- a page with no
    visuals isn't meaningful, so no empty Report/.pbip is written for that
    case, not a partial/broken one. Returns a summary dict: which tables and
    visuals were written, and which were skipped with why -- never a silent
    omission.

    connection_mode: "snapshot" (default) — every table is a frozen M-literal,
      exactly D2.0/D2.1/D2.2's existing behavior, unchanged. "live" (D2.3)
      additionally DirectQueries the client's actual warehouse table for
      every section data_context declares a source for — see this module's
      D2.3 section docstring for why these are separate, raw-shaped tables
      rather than a live version of the pre-aggregated ones.
    data_context: the dict data_context.load_data_context(client_id) returns
      (kind/config/sources) — required when connection_mode="live", unused
      otherwise. Not looked up internally so this module carries no
      import-time dependency on data_context.py, same DI pattern
      delivery.py's channel_impl uses."""
    if connection_mode not in ("snapshot", "live"):
        raise ValueError(f"connection_mode must be 'snapshot' or 'live', got {connection_mode!r}")
    if connection_mode == "live" and not data_context:
        raise ValueError("connection_mode='live' requires a data_context (kind/config/sources)")

    project_name = _sanitize_project_name(report_object.branding.get("client_name") or report_object.report_id)
    out_dir = Path(out_dir)
    model_dir = out_dir / f"{project_name}.SemanticModel"
    defn = model_dir / "definition"

    written: list[str] = []
    skipped: list[dict] = []
    rows_by_table: dict[str, tuple[_Columns, _Rows]] = {}
    for spec in _TABLE_SPECS:
        result = _resolve_table(report_object, spec)
        if result is None:
            skipped.append({
                "table": spec.name, "section": spec.section,
                "reason": f"no usable data resolved for {spec.name!r} (source: {spec.source!r})",
            })
            continue
        columns, rows = result
        rows_by_table[spec.name] = result
        written.append(spec.name)

    # D2.2 — verify BEFORE writing a single measure-bearing table: a
    # mismatch blocks the whole export, not just the one table.
    mismatches = _verify_measures(report_object, rows_by_table)
    if mismatches:
        detail = "; ".join(
            f"{m['measure']} on {m['table']}: recomputed {m['recomputed']!r} != "
            f"canonical {m['canonical']!r} ({m['path']})" for m in mismatches
        )
        raise MeasureReconciliationError(f"Power BI measure reconciliation failed, export blocked: {detail}")

    measures_by_table: dict[str, list[_MeasureSpec]] = {}
    for spec in _MEASURE_SPECS:
        measures_by_table.setdefault(spec.table, []).append(spec)

    for table_name, (columns, rows) in rows_by_table.items():
        _w(defn / "tables" / f"{table_name}.tmdl",
           _build_table_tmdl(table_name, columns, rows, measures_by_table.get(table_name)))

    # D2.2 / global invariant "the badge travels": the same qa.run_qa()
    # verdict every other surface (PDF/dashboard) carries, plus a fixed
    # methodology note explaining WHY every measure above is trustworthy --
    # a real table, bindable to a card/text visual, not just a code comment
    # only a developer would ever read.
    qa_badge = (report_object.qa or {}).get("badge")
    if qa_badge is not None:
        methodology = (
            "Every figure in this workbook is generated deterministically by ReportPilot's metrics "
            "engine before this export exists -- Power BI never computes, estimates, or recomputes a "
            "number. Every DAX measure here is a plain SUM over a pre-aggregated column, verified "
            "against the same canonical report object the PDF and dashboard read from at export time."
        )
        qa_columns: _Columns = [("badge", "string"), ("methodology", "string")]
        qa_rows: _Rows = [(qa_badge, methodology)]
        _w(defn / "tables" / "QaSummary.tmdl", _build_table_tmdl("QaSummary", qa_columns, qa_rows))
        written.append("QaSummary")

    # D2.3 — live warehouse connection, additive to (never a replacement
    # for) the snapshot tables above.
    live_tables_written: list[str] = []
    live_tables_skipped: list[dict] = []
    if connection_mode == "live":
        connector_kind = data_context["connector"]["kind"]
        connector_config = data_context["connector"]["config"]
        for section, source_cfg in (data_context.get("sources") or {}).items():
            pbip_table_name = _LIVE_SECTION_TABLES.get(section)
            if pbip_table_name is None or section not in report_object.section_order:
                continue  # not a section this report covers, or an unrecognized section key
            try:
                tmdl = _build_live_table_tmdl(
                    pbip_table_name, connector_kind, connector_config,
                    source_cfg["table"], source_cfg["column_map"],
                )
            except UnsupportedLiveConnection as exc:
                live_tables_skipped.append({"table": pbip_table_name, "section": section, "reason": str(exc)})
                continue
            _w(defn / "tables" / f"{pbip_table_name}.tmdl", tmdl)
            live_tables_written.append(pbip_table_name)
        written.extend(live_tables_written)

    _build_platform(model_dir, project_name, "SemanticModel", project_name)
    _wj(model_dir / "definition.pbism", {
        "$schema": f"{SCHEMA_BASE}/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.0",
    })
    _w(defn / "database.tmdl", f"database {project_name}\n\tcompatibilityLevel: 1567\n")
    _w(defn / "model.tmdl",
       "model Model\n\tculture: en-US\n\n" + "".join(f"ref table {t}\n" for t in written))

    report_summary = _build_report(out_dir, project_name, report_object, written)

    return {
        "project_name": project_name,
        "model_dir": str(model_dir),
        "report_dir": report_summary["report_dir"],
        "tables_written": written,
        "tables_skipped": skipped,
        "visuals_written": report_summary["visuals_written"],
        "visuals_skipped": report_summary["visuals_skipped"],
        "annotations_written": report_summary["annotations_written"],
        "annotations_dropped": report_summary["annotations_dropped"],
        "extra_content_written": report_summary["extra_content_written"],
        "theme_written": report_summary["theme_written"],
        "logo_written": report_summary["logo_written"],
        "connection_mode": connection_mode,
        "live_tables_written": live_tables_written,
        "live_tables_skipped": live_tables_skipped,
    }


# ---------------------------------------------------------------------------
# D2.1 — chart -> visual mapping. FRAMING: Power BI does not import graphs,
# it imports a model; every visual below binds to a real D2.0 table column,
# never a PNG. Reuses the OLD hand-built build_pbip.py's exact visual JSON
# shapes for chart/query/projection (already schema-validated -- see
# d:\IMDollars\powerbi\validate_pbip.py's own "26/26 passed" result) --
# ported, not reinvented. The textbox shape is new (the old demo never had
# one) and is sourced from a real, documented PBIR example (data-goblin/
# power-bi-agentic-development's pbir-format skill reference), not guessed
# -- guessing this risks a file Desktop refuses to open, the exact failure
# mode the old demo's own README warns about.
# ---------------------------------------------------------------------------

VISUAL_SCHEMA = f"{SCHEMA_BASE}/item/report/definition/visualContainer/2.9.0/schema.json"
PAGE_SCHEMA = f"{SCHEMA_BASE}/item/report/definition/page/2.1.0/schema.json"

#: Single mapping table, chart_type -> Power BI visualType. Never a second/
#: parallel map -- chart_type itself always comes from the ChartRef (A1's
#: decision, or the PDF path's hand-authored default when A1 has nothing to
#: evaluate), never re-decided here. Confirmed against every _CHART_SPECS
#: entry in report_builder.py plus every chart-rendering function in
#: charts.py: chart_type is always "line"/"bar"/"pie", and every "bar" chart
#: in this app renders horizontal (matplotlib barh) -- so "bar" always maps
#: to the horizontal Power BI visual, never the column/vertical one.
_CHART_TYPE_TO_VISUAL = {"line": "lineChart", "bar": "clusteredBarChart", "pie": "pieChart"}


@dataclass
class _VisualBindingSpec:
    #: Which D2.0 table this visual reads from.
    table: str
    x_field: str
    #: More than one entry is exactly the "multi-series charts bind ALL
    #: series, not just the first" exit criterion -- "Monthly revenue & win
    #: rate" binds both revenue_usd and win_rate, not only the first.
    y_fields: list[str]
    #: Grouping/legend dimension, e.g. channel_group for the weekly-by-
    #: channel line chart. None for charts with no such breakdown.
    series_field: str | None = None


#: Keyed by (section, caption) -- the same stable key report_builder.py's
#: own _CHART_SPECS uses, and covers exactly the captions that appear
#: there. table/x_field/y_fields intentionally repeat what a chart's own
#: metric_paths already imply (see D2.0's _TABLE_SPECS) rather than
#: re-deriving them from metric_paths at runtime -- report_object.charts
#: carries `metric_paths`, not column names, and D2.0's shape-extraction
#: (_extract_records etc.) is where those columns actually get named; this
#: spec is the one place that reconnects a caption to the table/columns
#: D2.0 produced for it.
_VISUAL_SPECS: dict[tuple[str, str], _VisualBindingSpec] = {
    ("analytics", "Weekly sessions by channel"): _VisualBindingSpec(
        "AnalyticsWeeklyByChannel", "week", ["sessions"], series_field="channel_group"),
    ("analytics", "Weekly revenue"): _VisualBindingSpec("AnalyticsWeeklyTotals", "week", ["revenue_usd"]),
    ("analytics", "Revenue by channel"): _VisualBindingSpec("AnalyticsByChannel", "channel", ["revenue_usd"]),
    ("analytics", "Conversion rate by channel"): _VisualBindingSpec("AnalyticsByChannel", "channel", ["conversion_rate"]),
    ("analytics", "Sessions by device"): _VisualBindingSpec("AnalyticsByDevice", "device_category", ["sessions"]),

    ("seo", "Site health"): _VisualBindingSpec("SeoSeverityCounts", "severity", ["count"]),
    ("seo", "Top technical issues"): _VisualBindingSpec("SeoTopIssues", "issue", ["count"]),

    ("sales", "Monthly revenue & win rate"): _VisualBindingSpec("SalesMonthly", "month", ["revenue_usd", "win_rate"]),
    ("sales", "Revenue by sales rep"): _VisualBindingSpec("SalesByRep", "sales_rep", ["revenue_usd"]),
    ("sales", "Revenue by lead source"): _VisualBindingSpec("SalesByLeadSource", "lead_source", ["revenue_usd"]),
    ("sales", "Revenue by product"): _VisualBindingSpec("SalesByProduct", "product", ["revenue_usd"]),
}

_PAGE_SECTION_ORDER = ["analytics", "seo", "sales"]
_PAGE_DISPLAY_NAMES = {"analytics": "Web Analytics", "seo": "SEO & Site Health", "sales": "Sales Performance"}

#: Single-column layout on purpose (see this node's CHANGELOG entry): a 2-
#: column grid needs per-row-height bookkeeping that isn't worth the
#: complexity for a first pass, and every visual's y-range is trivially
#: provable non-overlapping this way -- verified directly in tests, not
#: just visually inspected.
_PAGE_WIDTH = 900
_CHART_WIDTH = 860
_CHART_HEIGHT = 320
_TEXTBOX_HEIGHT = 50
_MARGIN = 20
_HEADER_HEIGHT = 60
_LOGO_WIDTH = 140
_CARD_WIDTH = 260
_CARD_HEIGHT = 90
_TABLE_HEIGHT = 220


@dataclass
class _KpiCardSpec:
    section: str
    table: str
    field: str
    label: str


#: Same fields/labels html_dashboard.py's own _kpi_cards() already shows on
#: the web dashboard (see this session's read of html_dashboard.py) -- reused
#: verbatim so a card here and a card there never disagree on what to call
#: the same number.
_KPI_CARD_SPECS: list[_KpiCardSpec] = [
    _KpiCardSpec("analytics", "AnalyticsTotals", "sessions", "Web Sessions"),
    _KpiCardSpec("analytics", "AnalyticsTotals", "revenue_usd", "Web Revenue"),
    _KpiCardSpec("analytics", "AnalyticsTotals", "conversion_rate", "Conversion Rate"),
    _KpiCardSpec("seo", "SeoTotals", "total_urls_crawled", "Pages Crawled"),
    _KpiCardSpec("seo", "SeoTotals", "indexable_pct", "Indexable Pages"),
    _KpiCardSpec("sales", "SalesTotals", "revenue_usd", "Closed-Won Revenue"),
    _KpiCardSpec("sales", "SalesTotals", "win_rate_pct", "Win Rate"),
]


@dataclass
class _ExtraTableSpec:
    section: str
    table: str
    title: str
    fields: list[str]


#: Content with no chart at all in the PDF path -- genuinely new material
#: this export surfaces that the PDF/dashboard don't, not a duplicate.
_EXTRA_TABLE_SPECS: list[_ExtraTableSpec] = [
    _ExtraTableSpec("seo", "SeoWorstPages", "Pages needing attention",
                     ["url", "issue_severity", "issues", "impressions_28d", "organic_sessions_28d"]),
    _ExtraTableSpec("seo", "SeoOpportunityPages", "SEO opportunity pages",
                     ["url", "impressions_28d", "avg_position", "ctr", "clicks_28d"]),
]

#: One interactive filter is a concrete, verifiable slice of "make it
#: interactive" -- bound to AnalyticsByChannel (always present whenever the
#: analytics section is), not a table built specifically for this.
_ANALYTICS_SLICER_TABLE = "AnalyticsByChannel"
_ANALYTICS_SLICER_COLUMN = "channel"


def _build_theme_json(branding: dict) -> dict:
    """Real, documented Power BI custom-theme JSON (Microsoft's own
    reportThemeSchema, fetched and schema-checked -- see this node's
    CHANGELOG entry), reusing theme.py's exact palette -- the same colors
    already in the PDF and HTML dashboard, not re-picked here. Only "name"
    is required by the schema; every other key here is optional but real."""
    client_name = branding.get("client_name") or "Report"
    return {
        "$schema": "https://raw.githubusercontent.com/microsoft/powerbi-desktop-samples/main/"
                    "Report%20Theme%20JSON%20Schema/reportThemeSchema-2.145.json",
        "name": f"{client_name} Theme",
        "dataColors": list(theme.CATEGORICAL),
        "background": theme.SURFACE,
        "foreground": theme.INK_PRIMARY,
        "good": theme.STATUS["good"],
        "neutral": theme.STATUS["warning"],
        "bad": theme.STATUS["critical"],
        "accent": branding.get("primary_color") or theme.CATEGORICAL[0],
        "tableAccent": branding.get("accent_color") or theme.CATEGORICAL[3],
    }


_DATA_URI_RE = re.compile(r"^data:image/(png|jpe?g);base64,(.+)$", re.IGNORECASE | re.DOTALL)


def _decode_logo(data_uri: str | None) -> tuple[bytes, str] | None:
    """(raw_bytes, file_extension), or None when there's no logo or the
    string isn't a data: URI this can decode -- never raises on a
    malformed/missing logo, since a bad logo shouldn't block the rest of
    the export."""
    if not data_uri:
        return None
    match = _DATA_URI_RE.match(data_uri.strip())
    if not match:
        return None
    ext = "jpg" if match.group(1).lower().startswith("jp") else "png"
    try:
        return base64.b64decode(match.group(2)), ext
    except (ValueError, TypeError):
        return None


def _lit(value: str) -> dict:
    return {"expr": {"Literal": {"Value": value}}}


def _col_field(entity: str, prop: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def _projection(field: dict, query_ref: str, native_ref: str) -> dict:
    return {"field": field, "queryRef": query_ref, "nativeQueryRef": native_ref}


def _visual_chart_json(name: str, x: int, y: int, w_: int, h: int, visual_type: str,
                        table: str, x_field: str, y_fields: list[str], series_field: str | None) -> dict:
    query_state = {
        "Category": {"projections": [_projection(_col_field(table, x_field), f"{table}.{x_field}", x_field)]},
        "Y": {"projections": [_projection(_col_field(table, yf), f"{table}.{yf}", yf) for yf in y_fields]},
    }
    if series_field:
        query_state["Series"] = {"projections": [
            _projection(_col_field(table, series_field), f"{table}.{series_field}", series_field)]}
    return {
        "$schema": VISUAL_SCHEMA, "name": name,
        "position": {"x": x, "y": y, "z": 1000, "width": w_, "height": h, "tabOrder": 1000},
        "visual": {"visualType": visual_type, "query": {"queryState": query_state}},
    }


def _visual_textbox_json(name: str, x: int, y: int, w_: int, h: int, text: str,
                          font_size: str = "10pt", font_weight: str | None = None, color: str | None = None) -> dict:
    """Static text only -- DECISIONS explicitly calls for a static text box,
    not a measure-bound dynamic run (which the reference doc itself flags
    as unverifiable without round-tripping through a real Desktop install:
    "no Microsoft schema or official sample defines the exact JSON... do
    not invent the structure"). A static paragraph is fully documented and
    schema-checkable; nothing here risks that unverifiable path."""
    text_style = {"fontFamily": "Segoe UI", "fontSize": font_size}
    if font_weight:
        text_style["fontWeight"] = font_weight
    if color:
        text_style["color"] = color
    return {
        "$schema": VISUAL_SCHEMA, "name": name,
        "position": {"x": x, "y": y, "z": 1000, "width": w_, "height": h, "tabOrder": 1000},
        "visual": {
            "visualType": "textbox",
            "objects": {"general": [{"properties": {"paragraphs": [{"textRuns": [{"value": text, "textStyle": text_style}]}]}}]},
            "drillFilterOtherVisuals": False,
        },
    }


def _visual_card_json(name: str, x: int, y: int, w_: int, h: int, table: str, field: str, label: str) -> dict:
    """Bound to a raw column, not a DAX measure -- D2.0's tables are already
    pre-aggregated to one row (see _TableSpec shape="scalar_dict"), so a
    column's own value IS the KPI; a measure isn't needed for correctness
    here (D2.2 is where named measures actually matter, for charts that
    aggregate across multiple rows). visualContainerObjects.title overrides
    the default field-derived label with the same wording html_dashboard.py
    already uses for this number."""
    return {
        "$schema": VISUAL_SCHEMA, "name": name,
        "position": {"x": x, "y": y, "z": 1000, "width": w_, "height": h, "tabOrder": 1000},
        "visual": {
            "visualType": "cardVisual",
            "query": {"queryState": {"Data": {"projections": [_projection(_col_field(table, field), f"{table}.{field}", field)]}}},
            "visualContainerObjects": {"title": [{"properties": {"show": _lit("true"), "text": _lit(f"'{label}'")}}]},
        },
    }


def _visual_slicer_json(name: str, x: int, y: int, w_: int, h: int, table: str, column: str, header: str) -> dict:
    return {
        "$schema": VISUAL_SCHEMA, "name": name,
        "position": {"x": x, "y": y, "z": 1000, "width": w_, "height": h, "tabOrder": 1000},
        "visual": {
            "visualType": "slicer",
            "query": {"queryState": {"Values": {"projections": [_projection(_col_field(table, column), f"{table}.{column}", column)]}}},
            "objects": {
                "data": [{"properties": {"mode": _lit("'Dropdown'")}}],
                "header": [{"properties": {"show": _lit("true"), "text": _lit(f"'{header}'")}}],
            },
        },
    }


def _visual_table_json(name: str, x: int, y: int, w_: int, h: int, table: str, fields: list[str]) -> dict:
    projections = [_projection(_col_field(table, f), f"{table}.{f}", f) for f in fields]
    return {
        "$schema": VISUAL_SCHEMA, "name": name,
        "position": {"x": x, "y": y, "z": 1000, "width": w_, "height": h, "tabOrder": 1000},
        "visual": {"visualType": "tableEx", "query": {"queryState": {"Values": {"projections": projections}}}},
    }


def _visual_image_json(name: str, x: int, y: int, w_: int, h: int, item_name: str) -> dict:
    """References a RegisteredResources package item by name -- the actual
    image bytes are registered separately in report.json's resourcePackages
    and written to StaticResources/RegisteredResources/, confirmed against
    a real PBIR example (data-goblin/power-bi-agentic-development), same
    verify-before-authoring discipline as the textbox shape."""
    return {
        "$schema": VISUAL_SCHEMA, "name": name,
        "position": {"x": x, "y": y, "z": 1000, "width": w_, "height": h, "tabOrder": 1000},
        "visual": {
            "visualType": "image",
            "objects": {"general": [{"properties": {"imageUrl": {"expr": {"ResourcePackageItem": {
                "PackageName": "RegisteredResources", "PackageType": 1, "ItemName": item_name,
            }}}}}]},
            "drillFilterOtherVisuals": True,
        },
    }


def _build_report(out_dir: Path, project_name: str, report_object: ReportObject, tables_written: list[str]) -> dict:
    report_dir = out_dir / f"{project_name}.Report"
    defn = report_dir / "definition"
    branding = report_object.branding or {}
    accent_color = branding.get("primary_color") or theme.CATEGORICAL[0]

    logo = _decode_logo(branding.get("logo_data_uri"))
    logo_item_name = f"logo.{logo[1]}" if logo else None

    visuals_written: list[str] = []
    visuals_skipped: list[dict] = []
    annotations_written: list[str] = []
    annotations_dropped: list[dict] = []
    extra_content_written: list[str] = []

    pages: dict[str, list[dict]] = {}  # section -> ordered list of visual.json dicts
    cursors: dict[str, int] = {}  # section -> next free y

    def ensure_page(section: str) -> None:
        """First touch of a section: write its header (title + logo) once,
        seed the cursor below it. Every later addition to this section just
        appends and advances cursors[section] -- the header is never
        re-emitted."""
        if section in pages:
            return
        title_width = _CHART_WIDTH - (_LOGO_WIDTH + 20 if logo_item_name else 0)
        pages[section] = [_visual_textbox_json(
            f"title_{section}", _MARGIN, 10, title_width, 40,
            _PAGE_DISPLAY_NAMES[section], font_size="20pt", font_weight="600", color=accent_color,
        )]
        if logo_item_name:
            pages[section].append(_visual_image_json(
                f"logo_{section}", _MARGIN + title_width + 20, 10, _LOGO_WIDTH, 40, logo_item_name))
        cursors[section] = _HEADER_HEIGHT

    # KPI cards first -- the most important numbers seen before any chart,
    # same "totals before detail" ordering html_dashboard.py's page already uses.
    cards_by_section: dict[str, list[_KpiCardSpec]] = {}
    for card in _KPI_CARD_SPECS:
        if card.table in tables_written:
            cards_by_section.setdefault(card.section, []).append(card)
    for section, cards in cards_by_section.items():
        ensure_page(section)
        for i, card in enumerate(cards):
            x = _MARGIN + i * (_CARD_WIDTH + 20)
            card_json = _visual_card_json(f"kpi_{section}_{card.field}", x, cursors[section], _CARD_WIDTH, _CARD_HEIGHT, card.table, card.field, card.label)
            pages[section].append(card_json)
            extra_content_written.append(card_json["name"])
        cursors[section] += _CARD_HEIGHT + 20

    # Charts, each with its own annotation textbox directly beneath it.
    for chart in report_object.charts:
        key = (chart.section, chart.caption)
        spec = _VISUAL_SPECS.get(key)
        if spec is None:
            visuals_skipped.append({"chart_id": chart.id, "reason": f"no visual binding spec registered for caption {chart.caption!r}"})
            continue
        if spec.table not in tables_written:
            visuals_skipped.append({"chart_id": chart.id, "reason": f"backing table {spec.table!r} was not generated this run"})
            continue
        visual_type = _CHART_TYPE_TO_VISUAL.get(chart.chart_type)
        if visual_type is None:
            visuals_skipped.append({"chart_id": chart.id, "reason": f"no visual-type mapping for chart_type {chart.chart_type!r}"})
            continue

        ensure_page(chart.section)
        y = cursors[chart.section]
        chart_json = _visual_chart_json(
            f"chart_{chart.id}", _MARGIN, y, _CHART_WIDTH, _CHART_HEIGHT,
            visual_type, spec.table, spec.x_field, spec.y_fields, spec.series_field,
        )
        pages[chart.section].append(chart_json)
        visuals_written.append(chart.id)
        next_y = y + _CHART_HEIGHT + 10

        if chart.annotation:
            text = chart.annotation.get("text")
            if text:
                textbox_json = _visual_textbox_json(f"note_{chart.id}", _MARGIN, next_y, _CHART_WIDTH, _TEXTBOX_HEIGHT, text)
                pages[chart.section].append(textbox_json)
                annotations_written.append(chart.id)
                next_y += _TEXTBOX_HEIGHT + 10
            else:
                annotations_dropped.append({"chart_id": chart.id, "reason": "annotation present but had no text"})

        cursors[chart.section] = next_y + _MARGIN

    # Extra content with no PDF-path chart at all -- genuinely additional material.
    for extra in _EXTRA_TABLE_SPECS:
        if extra.table not in tables_written:
            continue
        ensure_page(extra.section)
        y = cursors[extra.section]
        pages[extra.section].append(_visual_textbox_json(f"heading_{extra.table}", _MARGIN, y, _CHART_WIDTH, 24, extra.title, font_weight="600"))
        table_json = _visual_table_json(f"table_{extra.table}", _MARGIN, y + 28, _CHART_WIDTH, _TABLE_HEIGHT, extra.table, extra.fields)
        pages[extra.section].append(table_json)
        extra_content_written.append(table_json["name"])
        cursors[extra.section] = y + 28 + _TABLE_HEIGHT + _MARGIN

    # One interactive filter -- a slicer that actually cross-filters the
    # analytics page's charts, since they all read AnalyticsByChannel or a
    # table sharing the same channel values.
    if _ANALYTICS_SLICER_TABLE in tables_written and "analytics" in pages:
        y = cursors["analytics"]
        slicer_json = _visual_slicer_json(
            "slicer_channel", _MARGIN, y, 260, 120, _ANALYTICS_SLICER_TABLE, _ANALYTICS_SLICER_COLUMN, "Channel")
        pages["analytics"].append(slicer_json)
        extra_content_written.append(slicer_json["name"])
        cursors["analytics"] = y + 120 + _MARGIN

    #: A report with zero visuals to show isn't a valid deliverable -- write
    #: NOTHING Report-side (no half-written .Report folder, no .pbip root
    #: file) rather than a technically-present-but-empty one. Caught by
    #: testing the zero-chart case directly: an earlier version wrote
    #: .platform/report.json/pages.json unconditionally before this check
    #: even existed, only gating the .pbip file -- leaving a broken,
    #: page-less Report/ folder on disk for that case.
    page_ids = [s for s in _PAGE_SECTION_ORDER if s in pages]
    theme_item_name = None
    if page_ids:
        theme_json = _build_theme_json(branding)
        theme_item_name = f"{project_name}Theme.json"

        _build_platform(report_dir, project_name, "Report", project_name)
        _wj(report_dir / "definition.pbir", {
            "$schema": f"{SCHEMA_BASE}/item/report/definitionProperties/2.0.0/schema.json",
            "version": "4.0",
            "datasetReference": {"byPath": {"path": f"../{project_name}.SemanticModel"}},
        })
        _wj(defn / "version.json", {
            "$schema": f"{SCHEMA_BASE}/item/report/definition/versionMetadata/1.0.0/schema.json",
            "version": "2.0.0",
        })

        registered_items = [{"name": theme_item_name, "path": theme_item_name, "type": "CustomTheme"}]
        if logo_item_name:
            registered_items.append({"name": logo_item_name, "path": logo_item_name, "type": "Image"})
        _wj(defn / "report.json", {
            "$schema": f"{SCHEMA_BASE}/item/report/definition/report/3.3.0/schema.json",
            "themeCollection": {
                "baseTheme": {"name": "CY24SU06", "type": "SharedResources",
                              "reportVersionAtImport": {"visual": "2.9.0", "page": "2.1.0", "report": "3.3.0"}},
                #: Real client branding -- theme.py's exact palette + this
                #: report's primary/accent colors -- layered on top of the
                #: built-in base theme, not a re-guessed one. See
                #: _build_theme_json's docstring for the schema this is
                #: checked against.
                "customTheme": {"name": theme_item_name,
                                 "reportVersionAtImport": {"visual": "2.9.0", "report": "3.3.0", "page": "2.1.0"},
                                 "type": "RegisteredResources"},
            },
            "resourcePackages": [
                {"name": "SharedResources", "type": "SharedResources",
                 "items": [{"name": "CY24SU06", "path": "BaseThemes/CY24SU06.json", "type": "BaseTheme"}]},
                {"name": "RegisteredResources", "type": "RegisteredResources", "items": registered_items},
            ],
        })
        _wj(defn / "pages" / "pages.json", {
            "$schema": f"{SCHEMA_BASE}/item/report/definition/pagesMetadata/1.1.0/schema.json",
            "pageOrder": page_ids,
            "activePageName": page_ids[0],
        })
        for section in page_ids:
            page_dir = defn / "pages" / section
            page_height = max(720, cursors.get(section, _MARGIN))
            _wj(page_dir / "page.json", {
                "$schema": PAGE_SCHEMA, "name": section, "displayName": _PAGE_DISPLAY_NAMES[section],
                "displayOption": "FitToPage", "height": page_height, "width": _PAGE_WIDTH,
            })
            for visual in pages[section]:
                _wj(page_dir / "visuals" / visual["name"] / "visual.json", visual)

        static_dir = report_dir / "StaticResources" / "RegisteredResources"
        _wj(static_dir / theme_item_name, theme_json)
        if logo:
            static_dir.mkdir(parents=True, exist_ok=True)
            (static_dir / logo_item_name).write_bytes(logo[0])

        _wj(out_dir / f"{project_name}.pbip", {
            "$schema": f"{SCHEMA_BASE}/pbip/pbipProperties/1.0.0/schema.json",
            "version": "1.0.0",
            "artifacts": [{"report": {"path": f"{project_name}.Report"}}],
            "settings": {"enableAutoRecovery": True},
        })

    return {
        "report_dir": str(report_dir) if page_ids else None,
        "visuals_written": visuals_written,
        "visuals_skipped": visuals_skipped,
        "annotations_written": annotations_written,
        "annotations_dropped": annotations_dropped,
        "extra_content_written": extra_content_written,
        "theme_written": theme_item_name is not None,
        "logo_written": logo_item_name is not None,
    }
