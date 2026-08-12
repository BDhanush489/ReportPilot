# ReportPilot — AI report writer + Power BI dashboards for agencies

Two demo-ready deliverables for the same pitch: *"turn raw client data into a
branded report or dashboard in minutes instead of hours."*

1. **[`report-writer/`](report-writer/)** — a working web app. Upload a web
   analytics export, an SEO/site audit, and a sales spreadsheet; get back a
   branded, client-ready PDF report with charts, written by Claude Opus 4.8
   grounded in numbers computed deterministically from the data (Claude never
   invents a figure — see [design notes](#why-the-numbers-are-trustworthy)).
2. **[`powerbi/`](powerbi/)** — a professional Power BI Project (PBIP): 4
   tables, 17 DAX measures, a 3-page dashboard, built from the same sample
   data, schema-validated against Microsoft's published PBIR/TMDL schemas.

Both use the same synthetic client — **Aurora Home Goods**, a fictitious
DTC home-goods brand — so the numbers and charts tell one consistent story
across the PDF report and the Power BI dashboard.

## Quick start — the report writer (5 minutes)

```powershell
# 1. Backend
cd report-writer/backend
python -m venv venv
./venv/Scripts/pip install -r requirements.txt
./venv/Scripts/python sample_data/generate_sample_data.py   # regenerate sample data (optional, already included)
./venv/Scripts/python -m uvicorn app.main:app --reload --port 8000

# 2. Frontend (separate terminal)
cd report-writer/frontend
npm install
npm run dev
```

Open **http://localhost:3000**, upload the three sample files from
`report-writer/backend/sample_data/`, and click **Generate report**. You'll
get a live text preview plus a downloadable branded PDF. Every report you
generate is saved to disk and shows up under **Recent reports** in the
sidebar — even after restarting the backend — so it's easy to pull up
something you generated last week for a client.

### Turning on real AI-authored narrative

Without an API key, the backend still runs end-to-end using a deterministic
template narrative (clearly labeled "Draft narrative" in the UI and PDF) —
useful for demoing the pipeline with zero setup. To get Claude Opus 4.8's
actual writing:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
# then restart the backend
```

Every number in the report — sessions, revenue, win rate, everything — comes
from `report-writer/backend/app/metrics.py`, computed with pandas before
Claude ever sees the data. Claude is given the computed metrics as JSON and
told, explicitly, to write narrative only and never calculate or invent a
number. That's what makes the AI-authored version safe to hand straight to
an agency's client.

## Quick start — the Power BI dashboard

See **[`powerbi/README.md`](powerbi/README.md)** — requires enabling 3
Power BI Desktop preview features once, then opening `AuroraHomeGoods.pbip`.

## Project layout

```
report-writer/
  backend/            FastAPI app: parsers, metrics engine, Claude agent, PDF renderer
    app/
      parsers.py       ingests analytics CSV / SEO audit CSV / sales XLSX, tolerant of column-name variants
      metrics.py       all deterministic number-crunching (pandas)
      charts.py        matplotlib charts using a validated, colorblind-safe palette
      agent.py         the Claude Opus 4.8 call (structured JSON output) + deterministic fallback
      report_builder.py orchestrates parse -> metrics -> charts -> Claude -> PDF
      templates/report.html  the branded PDF template (Jinja2 -> xhtml2pdf)
    sample_data/       synthetic Aurora Home Goods data (analytics, SEO audit, sales)
  frontend/            Next.js app: upload, branding, live preview, PDF download

powerbi/
  AuroraHomeGoods.pbip + .Report/ + .SemanticModel/   the Power BI project
  build_pbip.py        generates the whole project from a compact Python spec
  validate_pbip.py / check_field_references.py   automated schema + reference checks
  sample_data/         same synthetic data, in the format Power Query expects
  README.md            setup + what's inside
```

## What's a demo shortcut vs. production-ready

Built as a sellable MVP, not a hardened multi-tenant SaaS. Before selling
subscriptions, you'd want to address:

- **Report storage is local disk** (`report-writer/backend/generated/`, one
  folder per report ID with the PDF, HTML, and metadata) — reports survive a
  backend restart and are listed via `GET /api/reports`, but this is a single
  machine's filesystem. Swap for object storage (S3/Blob) + a DB table
  keyed by report ID before running multi-instance.
- **No auth / no multi-tenancy** — anyone who can reach the backend can
  generate reports. Add API keys or session auth before exposing this
  publicly.
- **No usage limits or billing** — the Claude API call is metered by
  Anthropic but nothing here meters *your* customers.
- **Parser column-alias coverage is a starting set**, not exhaustive — real
  client exports from HubSpot, Salesforce, Screaming Frog, etc. will need
  their column names added to the alias maps in `parsers.py` as you onboard
  real agencies.

## Why the numbers are trustworthy

This is the single most important design decision in the whole product, so
it's worth restating: `agent.py`'s system prompt tells Claude "every number
you write MUST come directly from the JSON you are given... never invent a
statistic," and the JSON it receives is the *output* of `metrics.py`, not
the raw uploaded files. Claude never sees a spreadsheet — it sees pre-computed
totals, percentages, and trends, and its only job is to explain them well and
connect the dots across data sources (e.g., "organic sessions are declining,
and the SEO audit shows 10 pages with critical errors" — a connection Claude
is explicitly prompted to look for when more than one data source is
uploaded).

How to export to power bi
Unzip the download — you'll get three items: {ClientName}.pbip, {ClientName}.Report/, {ClientName}.SemanticModel/. Keep them together in the same folder.
You need Power BI Desktop (Windows only, free from Microsoft) installed on the machine you're viewing this on.
One-time setup in Desktop: File → Options and settings → Options → Preview features, enable all three:
Power BI Project (.pbip) save option
Store semantic model using TMDL format
Store reports using enhanced metadata format (PBIR) Then restart Desktop.
Double-click the .pbip file (or File → Open in Desktop). It opens the report and its data model together.

