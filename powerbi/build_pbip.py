"""
Generates a complete Power BI Project (PBIP) — TMDL semantic model + PBIR report —
for the "Aurora Home Goods" sample dataset used elsewhere in this repo.

Why a generator instead of hand-edited files: PBIP/TMDL/PBIR is a large set of
small, precisely-shaped text files (one per table, one per page, one per visual).
Generating them from a compact spec keeps every ID, schema URL, and cross-reference
consistent, and makes the whole project reproducible / easy to extend (add a page
by editing PAGES below and re-running, rather than hand-editing JSON).

Run: python build_pbip.py
Output: ./AuroraHomeGoods.pbip, ./AuroraHomeGoods.Report/, ./AuroraHomeGoods.SemanticModel/

Requires Power BI Desktop with these preview features enabled (File > Options and
settings > Options > Preview features):
  - "Power BI Project (.pbip) save option"
  - "Store semantic model using TMDL format"
  - "Store reports using enhanced metadata format (PBIR)"
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).parent
PROJECT = "AuroraHomeGoods"
REPORT_DIR = ROOT / f"{PROJECT}.Report"
MODEL_DIR = ROOT / f"{PROJECT}.SemanticModel"
SAMPLE_DATA_ABS_PATH = str((ROOT / "sample_data").resolve()) + "\\"

SCHEMA_BASE = "https://developer.microsoft.com/json-schemas/fabric"

# ---------------------------------------------------------------------------
# Brand / palette — same validated colors used in the PDF report charts, so a
# customer sees one consistent visual identity across both deliverables.
# ---------------------------------------------------------------------------
CHANNEL_COLORS = {
    "Organic Search": "#2a78d6", "Paid Search": "#008300", "Paid Social": "#e87ba4",
    "Email": "#eda100", "Direct": "#1baf7a", "Referral": "#eb6834",
}
PRIMARY = "#2a78d6"

TAB = "\t"


def w(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\r\n")


def wj(path: Path, obj: dict):
    w(path, json.dumps(obj, indent=2, ensure_ascii=False))


# ===========================================================================
# 1. Top-level .pbip + .gitignore
# ===========================================================================

def build_root():
    wj(ROOT / f"{PROJECT}.pbip", {
        "$schema": f"{SCHEMA_BASE}/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0.0",
        "artifacts": [{"report": {"path": f"{PROJECT}.Report"}}],
        "settings": {"enableAutoRecovery": True},
    })
    w(ROOT / ".gitignore", "**/.pbi/localSettings.json\n**/.pbi/cache.abf\n")


# ===========================================================================
# 2. .platform files (identical shape for Report and SemanticModel folders)
# ===========================================================================

def build_platform(folder: Path, item_type: str, display_name: str):
    wj(folder / ".platform", {
        "version": "2.0",
        "$schema": f"{SCHEMA_BASE}/platform/platformProperties.json",
        "config": {"logicalId": str(uuid.uuid4())},
        "metadata": {"type": item_type, "displayName": display_name},
    })


# ===========================================================================
# 3. Semantic model — TMDL
# ===========================================================================

TABLES = {
    "WebAnalytics": {
        "source": "csv",
        "file": "web_analytics.csv",
        "columns": [
            ("date", "dateTime", None),
            ("channel_group", "string", "none"),
            ("device_category", "string", "none"),
            ("sessions", "int64", "sum"),
            ("new_users", "int64", "sum"),
            ("engaged_sessions", "int64", "sum"),
            ("conversions", "int64", "sum"),
            ("revenue_usd", "double", "sum"),
            ("bounce_rate", "double", "none"),
            ("avg_session_duration_sec", "int64", "none"),
        ],
        "measures": [
            ("Total Sessions", "SUM(WebAnalytics[sessions])", "#,##0"),
            ("New Users", "SUM(WebAnalytics[new_users])", "#,##0"),
            ("Total Conversions", "SUM(WebAnalytics[conversions])", "#,##0"),
            ("Web Revenue", "SUM(WebAnalytics[revenue_usd])", "$#,##0"),
            ("Conversion Rate", "DIVIDE([Total Conversions], [Total Sessions])", "0.00%"),
        ],
    },
    "SEOAudit": {
        "source": "csv",
        "file": "seo_audit.csv",
        "columns": [
            ("url", "string", "none"),
            ("path", "string", "none"),
            ("status_code", "int64", "none"),
            ("is_indexable", "boolean", "none"),
            ("load_time_ms", "int64", "average"),
            ("title_length", "int64", "none"),
            ("meta_description_length", "int64", "none"),
            ("h1_count", "int64", "none"),
            ("word_count", "int64", "average"),
            ("has_canonical", "boolean", "none"),
            ("mobile_friendly", "boolean", "none"),
            ("broken_internal_links", "int64", "sum"),
            ("images_missing_alt", "int64", "sum"),
            ("impressions_28d", "int64", "sum"),
            ("clicks_28d", "int64", "sum"),
            ("ctr", "double", "none"),
            ("avg_position", "double", "average"),
            ("organic_sessions_28d", "int64", "sum"),
            ("issue_severity", "string", "none"),
            ("issues", "string", "none"),
        ],
        "measures": [
            ("Pages Crawled", "COUNTROWS(SEOAudit)", "#,##0"),
            ("Critical Issues", 'CALCULATE(COUNTROWS(SEOAudit), SEOAudit[issue_severity] = "critical")', "#,##0"),
            ("Indexable Pages", "CALCULATE(COUNTROWS(SEOAudit), SEOAudit[is_indexable] = TRUE)", "#,##0"),
            ("Indexable %", "DIVIDE([Indexable Pages], [Pages Crawled])", "0.0%"),
            ("Total Impressions", "SUM(SEOAudit[impressions_28d])", "#,##0"),
            ("Total Clicks", "SUM(SEOAudit[clicks_28d])", "#,##0"),
            ("Search CTR", "DIVIDE([Total Clicks], [Total Impressions])", "0.00%"),
        ],
    },
    "SalesDeals": {
        "source": "xlsx",
        "file": "sales_pipeline.xlsx",
        "sheet": "Deals",
        "columns": [
            ("deal_id", "int64", "none"),
            ("close_date", "dateTime", None),
            ("sales_rep", "string", "none"),
            ("product", "string", "none"),
            ("region", "string", "none"),
            ("lead_source", "string", "none"),
            ("deal_stage", "string", "none"),
            ("amount_usd", "double", "sum"),
            ("potential_amount_usd", "double", "sum"),
            ("days_to_close", "int64", "average"),
        ],
        "measures": [
            ("Deals Won", 'CALCULATE(COUNTROWS(SalesDeals), SalesDeals[deal_stage] = "Closed Won")', "#,##0"),
            ("Deals Lost", 'CALCULATE(COUNTROWS(SalesDeals), SalesDeals[deal_stage] = "Closed Lost")', "#,##0"),
            ("Win Rate", "DIVIDE([Deals Won], [Deals Won] + [Deals Lost])", "0.0%"),
            ("Sales Revenue", 'CALCULATE(SUM(SalesDeals[amount_usd]), SalesDeals[deal_stage] = "Closed Won")', "$#,##0"),
            ("Avg Deal Size", "DIVIDE([Sales Revenue], [Deals Won])", "$#,##0"),
        ],
    },
}


def m_quote(path: str) -> str:
    return path.replace("\\", "\\\\").replace('"', '""')


def build_partition_csv(table_name: str, file: str, columns: list[tuple]) -> str:
    type_map = {"int64": "Int64.Type", "double": "type number", "string": "type text",
                "dateTime": "type date", "boolean": "type logical"}
    transforms = ", ".join(f'{{"{c[0]}", {type_map[c[1]]}}}' for c in columns)
    return f"""\tpartition {table_name}-Partition = m
\t\tmode: import
\t\tsource =
\t\t\tlet
\t\t\t\tSource = Csv.Document(File.Contents(SampleDataFolder & "{file}"), [Delimiter=",", Columns={len(columns)}, Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
\t\t\t\tPromoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
\t\t\t\tTyped = Table.TransformColumnTypes(Promoted,{{{transforms}}})
\t\t\tin
\t\t\t\tTyped
"""


def build_partition_xlsx(table_name: str, file: str, sheet: str, columns: list[tuple]) -> str:
    type_map = {"int64": "Int64.Type", "double": "type number", "string": "type text",
                "dateTime": "type date", "boolean": "type logical"}
    transforms = ", ".join(f'{{"{c[0]}", {type_map[c[1]]}}}' for c in columns)
    return f"""\tpartition {table_name}-Partition = m
\t\tmode: import
\t\tsource =
\t\t\tlet
\t\t\t\tSource = Excel.Workbook(File.Contents(SampleDataFolder & "{file}"), null, true),
\t\t\t\tSheetTable = Source{{[Item="{sheet}",Kind="Sheet"]}}[Data],
\t\t\t\tPromoted = Table.PromoteHeaders(SheetTable, [PromoteAllScalars=true]),
\t\t\t\tTyped = Table.TransformColumnTypes(Promoted,{{{transforms}}})
\t\t\tin
\t\t\t\tTyped
"""


def build_table_tmdl(name: str, spec: dict) -> str:
    lines = [f"table {name}", ""]
    if spec["source"] == "csv":
        lines.append(build_partition_csv(name, spec["file"], spec["columns"]))
    else:
        lines.append(build_partition_xlsx(name, spec["file"], spec["sheet"], spec["columns"]))

    for mname, expr, fmt in spec["measures"]:
        mname_q = f"'{mname}'" if " " in mname or "%" in mname else mname
        lines.append(f"\tmeasure {mname_q} = {expr}")
        lines.append(f'\t\tformatString: {fmt}')
        lines.append("")

    for cname, dtype, summarize in spec["columns"]:
        cname_q = f"'{cname}'" if not cname.isidentifier() else cname
        lines.append(f"\tcolumn {cname_q}")
        lines.append(f"\t\tdataType: {dtype}")
        lines.append(f"\t\tsourceColumn: {cname}")
        if summarize:
            lines.append(f"\t\tsummarizeBy: {summarize}")
        else:
            lines.append("\t\tsummarizeBy: none")
        lines.append("")
    return "\n".join(lines)


def build_date_table_tmdl() -> str:
    # CALENDAR() produces the single [Date] column; Year/MonthSort/MonthName/Quarter
    # are calculated columns (DAX default-property syntax: 'column Name = <expr>').
    return """table Date

\tpartition Date-Partition = calculated
\t\tmode: import
\t\tsource = CALENDAR(MIN(MIN(WebAnalytics[date]), MIN(SalesDeals[close_date])), MAX(MAX(WebAnalytics[date]), MAX(SalesDeals[close_date])))

\tcolumn Date
\t\tdataType: dateTime
\t\tisKey
\t\tsourceColumn: Date
\t\tsummarizeBy: none
\t\tformatString: Long Date

\tcolumn Year = YEAR([Date])
\t\tdataType: int64
\t\tsummarizeBy: none

\tcolumn MonthSort = YEAR([Date]) * 100 + MONTH([Date])
\t\tdataType: int64
\t\tsummarizeBy: none
\t\tisHidden

\tcolumn MonthName = FORMAT([Date], "MMM YYYY")
\t\tdataType: string
\t\tsummarizeBy: none
\t\tsortByColumn: MonthSort

\tcolumn Quarter = "Q" & FORMAT([Date], "Q")
\t\tdataType: string
\t\tsummarizeBy: none
"""


def build_semantic_model():
    defn = MODEL_DIR / "definition"

    wj(MODEL_DIR / "definition.pbism", {
        "$schema": f"{SCHEMA_BASE}/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.0",
    })
    build_platform(MODEL_DIR, "SemanticModel", PROJECT)

    w(defn / "database.tmdl", f"database {PROJECT}\n\tcompatibilityLevel: 1567\n")

    w(defn / "model.tmdl",
      "model Model\n\tculture: en-US\n\n"
      "ref table WebAnalytics\n"
      "ref table SEOAudit\n"
      "ref table SalesDeals\n"
      "ref table Date\n")

    w(defn / "expressions.tmdl",
      f'expression SampleDataFolder = "{m_quote(SAMPLE_DATA_ABS_PATH)}" '
      'meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n')

    # tables
    for name, spec in TABLES.items():
        w(defn / "tables" / f"{name}.tmdl", build_table_tmdl(name, spec))

    w(defn / "tables" / "Date.tmdl", build_date_table_tmdl())

    rel1, rel2 = str(uuid.uuid4()), str(uuid.uuid4())
    w(defn / "relationships.tmdl",
      f"relationship {rel1}\n\tfromColumn: WebAnalytics.date\n\ttoColumn: Date.Date\n\n"
      f"relationship {rel2}\n\tfromColumn: SalesDeals.close_date\n\ttoColumn: Date.Date\n")


# ===========================================================================
# 4. Report — PBIR
# ===========================================================================

VISUAL_SCHEMA = f"{SCHEMA_BASE}/item/report/definition/visualContainer/2.9.0/schema.json"
PAGE_SCHEMA = f"{SCHEMA_BASE}/item/report/definition/page/2.1.0/schema.json"


def lit(value: str) -> dict:
    return {"expr": {"Literal": {"Value": value}}}


def col_field(entity: str, prop: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def measure_field(entity: str, prop: str) -> dict:
    return {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def projection(field: dict, query_ref: str, native_ref: str) -> dict:
    return {"field": field, "queryRef": query_ref, "nativeQueryRef": native_ref}


def visual_card(name: str, x, y, w_, h, entity: str, measure: str) -> dict:
    return {
        "$schema": VISUAL_SCHEMA,
        "name": name,
        "position": {"x": x, "y": y, "z": 1000, "width": w_, "height": h, "tabOrder": 1000},
        "visual": {
            "visualType": "cardVisual",
            "query": {"queryState": {"Data": {"projections": [
                projection(measure_field(entity, measure), f"{entity}.{measure}", measure)
            ]}}},
        },
    }


def visual_slicer(name: str, x, y, w_, h, entity: str, column: str, header: str) -> dict:
    return {
        "$schema": VISUAL_SCHEMA,
        "name": name,
        "position": {"x": x, "y": y, "z": 1000, "width": w_, "height": h, "tabOrder": 1000},
        "visual": {
            "visualType": "slicer",
            "query": {"queryState": {"Values": {"projections": [
                projection(col_field(entity, column), f"{entity}.{column}", column)
            ]}}},
            "objects": {
                "data": [{"properties": {"mode": lit("'Dropdown'")}}],
                "header": [{"properties": {"show": lit("true"), "text": lit(f"'{header}'")}}],
            },
        },
    }


def visual_chart(name: str, x, y, w_, h, visual_type: str, category: tuple, value: tuple, series: tuple | None = None) -> dict:
    cat_entity, cat_col = category
    val_entity, val_name, val_is_measure = value
    val_field = measure_field(val_entity, val_name) if val_is_measure else col_field(val_entity, val_name)
    query_state = {
        "Category": {"projections": [projection(col_field(cat_entity, cat_col), f"{cat_entity}.{cat_col}", cat_col)]},
        "Y": {"projections": [projection(val_field, f"{val_entity}.{val_name}", val_name)]},
    }
    if series:
        s_entity, s_col = series
        query_state["Series"] = {"projections": [projection(col_field(s_entity, s_col), f"{s_entity}.{s_col}", s_col)]}
    return {
        "$schema": VISUAL_SCHEMA,
        "name": name,
        "position": {"x": x, "y": y, "z": 1000, "width": w_, "height": h, "tabOrder": 1000},
        "visual": {"visualType": visual_type, "query": {"queryState": query_state}},
    }


def visual_table(name: str, x, y, w_, h, entity: str, fields: list[tuple]) -> dict:
    """fields: list of (name, is_measure)"""
    projections = []
    for fname, is_measure in fields:
        field = measure_field(entity, fname) if is_measure else col_field(entity, fname)
        projections.append(projection(field, f"{entity}.{fname}", fname))
    return {
        "$schema": VISUAL_SCHEMA,
        "name": name,
        "position": {"x": x, "y": y, "z": 1000, "width": w_, "height": h, "tabOrder": 1000},
        "visual": {
            "visualType": "tableEx",
            "query": {"queryState": {"Values": {"projections": projections}}},
            "objects": {
                "columnHeaders": [{"properties": {
                    "columnAdjustment": lit("'growToFit'"),
                    "autoSizeColumnWidth": lit("true"),
                    "fontColor": {"solid": {"color": lit("'#FFFFFF'")}},
                    "backColor": {"solid": {"color": lit(f"'{PRIMARY}'")}},
                }}],
                "values": [{"properties": {
                    "backColorPrimary": {"solid": {"color": lit("'#FFFFFF'")}},
                    "backColorSecondary": {"solid": {"color": lit("'#F5F5F5'")}},
                    "fontColorPrimary": {"solid": {"color": lit("'#0B0B0B'")}},
                    "fontColorSecondary": {"solid": {"color": lit("'#0B0B0B'")}},
                }}],
            },
        },
    }


PAGES = [
    {
        "id": "exec_overview",
        "displayName": "Executive Overview",
        "visuals": [
            visual_card("kpi_total_sessions", 20, 20, 300, 100, "WebAnalytics", "Total Sessions"),
            visual_card("kpi_web_revenue", 330, 20, 300, 100, "WebAnalytics", "Web Revenue"),
            visual_card("kpi_sales_revenue", 640, 20, 300, 100, "SalesDeals", "Sales Revenue"),
            visual_card("kpi_win_rate", 950, 20, 300, 100, "SalesDeals", "Win Rate"),
            visual_chart("chart_revenue_trend", 20, 140, 770, 340, "lineChart",
                         ("Date", "Date"), ("WebAnalytics", "Web Revenue", True)),
            visual_chart("chart_sessions_by_channel", 810, 140, 440, 340, "clusteredBarChart",
                         ("WebAnalytics", "channel_group"), ("WebAnalytics", "Total Sessions", True)),
            visual_slicer("slicer_channel", 20, 500, 300, 120, "WebAnalytics", "channel_group", "Channel"),
        ],
    },
    {
        "id": "marketing_seo",
        "displayName": "Marketing & SEO",
        "visuals": [
            visual_card("kpi_pages_crawled", 20, 20, 300, 100, "SEOAudit", "Pages Crawled"),
            visual_card("kpi_critical_issues", 330, 20, 300, 100, "SEOAudit", "Critical Issues"),
            visual_card("kpi_indexable_pct", 640, 20, 300, 100, "SEOAudit", "Indexable %"),
            visual_card("kpi_search_ctr", 950, 20, 300, 100, "SEOAudit", "Search CTR"),
            visual_chart("chart_pages_by_severity", 20, 140, 400, 340, "clusteredColumnChart",
                         ("SEOAudit", "issue_severity"), ("SEOAudit", "Pages Crawled", True)),
            visual_table("table_seo_detail", 440, 140, 810, 480, "SEOAudit", [
                ("url", False), ("issue_severity", False), ("impressions_28d", False), ("organic_sessions_28d", False),
            ]),
        ],
    },
    {
        "id": "sales_performance",
        "displayName": "Sales Performance",
        "visuals": [
            visual_card("kpi_sales_revenue_p3", 20, 20, 300, 100, "SalesDeals", "Sales Revenue"),
            visual_card("kpi_deals_won", 330, 20, 300, 100, "SalesDeals", "Deals Won"),
            visual_card("kpi_win_rate_p3", 640, 20, 300, 100, "SalesDeals", "Win Rate"),
            visual_card("kpi_avg_deal_size", 950, 20, 300, 100, "SalesDeals", "Avg Deal Size"),
            visual_chart("chart_revenue_by_month", 20, 140, 610, 340, "columnChart",
                         ("Date", "MonthName"), ("SalesDeals", "Sales Revenue", True)),
            visual_chart("chart_revenue_by_rep", 650, 140, 600, 340, "clusteredBarChart",
                         ("SalesDeals", "sales_rep"), ("SalesDeals", "Sales Revenue", True)),
            visual_table("table_revenue_by_source", 20, 500, 1230, 200, "SalesDeals", [
                ("lead_source", False), ("Sales Revenue", True), ("Deals Won", True), ("Win Rate", True),
            ]),
        ],
    },
]


def build_report():
    defn = REPORT_DIR / "definition"
    build_platform(REPORT_DIR, "Report", PROJECT)

    wj(REPORT_DIR / "definition.pbir", {
        "$schema": f"{SCHEMA_BASE}/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0",
        "datasetReference": {"byPath": {"path": f"../{PROJECT}.SemanticModel"}},
    })

    wj(defn / "version.json", {
        "$schema": f"{SCHEMA_BASE}/item/report/definition/versionMetadata/1.0.0/schema.json",
        "version": "2.0.0",
    })

    wj(defn / "report.json", {
        "$schema": f"{SCHEMA_BASE}/item/report/definition/report/3.3.0/schema.json",
        "themeCollection": {
            "baseTheme": {
                "name": "CY24SU06",
                "type": "SharedResources",
                "reportVersionAtImport": {"visual": "2.9.0", "page": "2.1.0", "report": "3.3.0"},
            },
        },
    })

    wj(defn / "pages" / "pages.json", {
        "$schema": f"{SCHEMA_BASE}/item/report/definition/pagesMetadata/1.1.0/schema.json",
        "pageOrder": [p["id"] for p in PAGES],
        "activePageName": PAGES[0]["id"],
    })

    for page in PAGES:
        page_dir = defn / "pages" / page["id"]
        wj(page_dir / "page.json", {
            "$schema": PAGE_SCHEMA,
            "name": page["id"],
            "displayName": page["displayName"],
            "displayOption": "FitToPage",
            "height": 720,
            "width": 1280,
        })
        for visual in page["visuals"]:
            wj(page_dir / "visuals" / visual["name"] / "visual.json", visual)


# ===========================================================================

def main():
    build_root()
    build_semantic_model()
    build_report()
    print(f"Wrote {PROJECT}.pbip, {PROJECT}.Report/, {PROJECT}.SemanticModel/ to {ROOT}")
    print(f"SampleDataFolder parameter defaults to: {SAMPLE_DATA_ABS_PATH}")


if __name__ == "__main__":
    main()
