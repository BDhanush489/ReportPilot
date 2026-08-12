# Aurora Home Goods — Power BI dashboard pack

A complete, professional Power BI Project (PBIP) for the same "Aurora Home
Goods" sample data used by the AI report writer — 4 tables, 17 DAX measures,
and a 3-page dashboard (Executive Overview, Marketing & SEO, Sales
Performance), all in the source-controllable PBIP/TMDL/PBIR format.

## What's here

```
AuroraHomeGoods.pbip                    <- open this in Power BI Desktop
AuroraHomeGoods.SemanticModel/          <- data model (TMDL): tables, measures, relationships
AuroraHomeGoods.Report/                 <- report (PBIR): 3 pages, 20 visuals
sample_data/                            <- the same synthetic CSV/XLSX files, copied here
build_pbip.py                           <- generates the whole project from a compact spec
validate_pbip.py                        <- validates every JSON file against Microsoft's official schemas
check_field_references.py               <- confirms every visual points at a real column/measure
```

`build_pbip.py` is the source of truth — the project folders are its output.
To add a page, change a color, or add a measure, edit the `TABLES` / `PAGES`
spec in that script and re-run it, rather than hand-editing the generated
JSON/TMDL. Both validators pass cleanly against the current output:

```
python validate_pbip.py           # 26/26 JSON files pass Microsoft's schemas
python check_field_references.py  # every visual's field references resolve
```

## Before you open it: enable 3 preview features

This format needs three Power BI Desktop preview toggles (they're stable,
just gated behind Preview for now). One-time setup:

**File → Options and settings → Options → Preview features**, check:
- Power BI Project (.pbip) save option
- Store semantic model using TMDL format
- Store reports using enhanced metadata format (PBIR)

Restart Desktop after enabling these.

## Opening it

Double-click `AuroraHomeGoods.pbip`, or open `AuroraHomeGoods.Report/definition.pbir`
directly. Power BI Desktop opens both the report and its connected semantic model.

The `SampleDataFolder` parameter defaults to the absolute path of the
`sample_data/` folder next to this README on the machine this was built on.
If you've moved the project, update it once: **Transform data → Manage
Parameters → SampleDataFolder** → point it at your local `sample_data/`
folder → **Refresh**.

## Getting a real .pbix

This ships as a PBIP (text-based project) rather than a binary .pbix on
purpose — it's the format meant to be reviewed, diffed, and handed to a
developer. To get a traditional `.pbix` file to email a client or upload to
the Power BI service: open the project in Desktop, then **File → Save As →
Power BI file (.pbix)**.

## What's in the model

| Table | Grain | Key measures |
|---|---|---|
| `WebAnalytics` | one row per day × channel × device | Total Sessions, Web Revenue, Conversion Rate, New Users |
| `SEOAudit` | one row per crawled URL | Pages Crawled, Critical Issues, Indexable %, Search CTR |
| `SalesDeals` | one row per CRM deal | Deals Won, Win Rate, Sales Revenue, Avg Deal Size |
| `Date` | one row per calendar day (hidden helper) | — (drives the Year/Quarter/Month axis on trend charts) |

`WebAnalytics[date]` and `SalesDeals[close_date]` both relate to `Date[Date]`
in a standard star schema, so a single Date-based visual filters both fact
tables together.

## Design notes / what to expect

- **Same palette as the PDF reports.** Channel colors (Organic Search =
  blue, Paid Search = green, etc.) match the ones in the AI report writer's
  charts, so a client sees one consistent visual identity across both
  deliverables — see `report-writer/backend/app/charts.py`.
- **Numbers only, colors only where I could verify the exact schema.** I
  deliberately did *not* hand-write per-data-point conditional-color rules
  or a custom registered theme — those parts of the PBIR format weren't
  something I could verify without Power BI Desktop installed, and getting
  them wrong risks a file Desktop refuses to open. Charts use Power BI's
  built-in theme (`CY24SU06`) rather than custom colors; the KPI cards, bar
  charts, line chart, slicer, and two detail tables were built from
  concrete, schema-validated examples and should open cleanly.
- **This machine doesn't have Power BI Desktop installed**, so this project
  has been validated as far as automation can take it (JSON Schema
  validation against Microsoft's published schemas + cross-referencing
  every field against the semantic model — both scripts above, both
  passing) but has *not* been visually opened and confirmed pixel-for-pixel
  in Desktop. Open it once yourself before presenting it to a client, the
  same way you'd proof any generated deliverable.
- Want a different look? Change `PRIMARY`/`CHANNEL_COLORS` at the top of
  `build_pbip.py`, or — easier for one-off styling — apply a theme from
  **View → Themes** in Desktop after opening.

## Extending it for a real client

Swap `sample_data/*.csv|.xlsx` for the client's real exports (same column
names, or update the `TABLES` spec in `build_pbip.py` to match different
columns), point `SampleDataFolder` at the new location, and refresh. The
measures, relationships, and report pages carry over unchanged.
