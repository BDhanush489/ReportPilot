# ReportPilot — ICP Positioning (v1)

*Grounded in the Aurora Home Goods sample dataset: 3 sources, 30 columns, 1,940 rows
(1,086 analytics rows across 6 channels × 3 devices × 181 days; 220 SEO-crawled URLs
across 12 distinct issue types; 634 deals across 5 reps × 5 products × 4 regions ×
6 lead sources). That's the realistic shape of what one client's monthly reporting
job actually requires — cross-tabulating 4-6 dimensions per source, correlating
findings across sources, and writing it up. `data:analyze` / `data:statistical-analysis`
are not available in this environment; the numbers below come from direct pandas
inspection of the sample data (see the exploration output in this session).*

## Segment 1 — Boutique marketing/SEO agencies (5–50 clients)

- **Job-to-be-done:** produce a branded monthly performance report per client,
  synthesizing analytics + SEO audit (+ sometimes ads/CRM), on a recurring deadline
  that doesn't move.
- **Hours wasted/month:** ~4-6 hrs/client × 5-50 clients = **20-300 hrs/month** across
  the agency. At a $75-125/hr blended rate, that's $1,500-$37,500/month of billable-adjacent
  time spent on a task the client isn't paying extra for — pure margin drag.
- **The one feature that makes them buy:** **recurring, scheduled generation** (lever
  #3). An agency doesn't want a tool that makes report #1 fast — they want to never
  manually trigger report #13, #14, #15... The moment reporting becomes "it just
  arrives," this stops being a tool and becomes infrastructure they can't churn off of.
- **Willingness to pay:** **highest** — this is a direct, quantifiable labor
  replacement with a clear multi-client multiplier (ROI scales with client count).

## Segment 2 — Fractional consultants (solo)

- **Job-to-be-done:** look enterprise-grade to a client who's paying a premium for a
  solo operator, without an analyst team to lean on.
- **Hours wasted/month:** ~4-6 hrs/client × 2-6 clients = **8-36 hrs/month** — smaller
  absolute number, but it's *the consultant's own unbillable time*, and the credibility
  gap (a lone consultant handing over a report that looks homemade) is a deal-risk,
  not just a time cost.
- **The one feature that makes them buy:** **trust/defensibility** (lever #2) — the
  "QA passed" badge and methodology footnote. A solo consultant's biggest fear is a
  client fact-checking a number and finding it wrong; the badge is what lets them
  send the PDF without a personal re-check pass, and it's also a credibility signal
  in the document itself ("this was QA'd," not "trust me").
- **Willingness to pay:** **medium** — smaller volume than Segment 1, but higher price
  sensitivity to *anything* that reduces personal risk, since their name is on it alone.

## Segment 3 — In-house growth teams (reporting up to execs)

- **Job-to-be-done:** turn scattered warehouse tables into an exec-readable narrative
  on a cadence execs expect (weekly/monthly), without asking a data analyst to hand-
  build a deck every cycle.
- **Hours wasted/month:** variable, but the real cost is analyst *opportunity* cost —
  time spent formatting a recurring deck instead of doing net-new analysis. Often
  invisible on a time sheet, which makes it a harder sell on hours-saved alone.
- **The one feature that makes them buy:** **warehouse connectivity** (lever #1) — an
  in-house team's data already lives in Snowflake/BigQuery, not CSVs someone exports
  by hand. A CSV-only tool is a non-starter for this segment regardless of report
  quality; a warehouse connector is the entry ticket.
- **Willingness to pay:** **lowest, but highest ceiling** — harder to reach (longer
  sales cycle, needs data-team buy-in, security review), but a per-seat/enterprise
  deal here is worth more than either agency segment individually. Not the near-term
  target.

## Ranking and what it means for build order

**Segment 1 (agencies) ranks #1 on willingness-to-pay** — direct, multi-client labor
replacement, fastest sales cycle (one strategist decides, not a security review),
and the clearest recurring-revenue shape for us too (they'll pay monthly for as long
as they have clients).

Segment 1's own #1 ask is lever #3 (recurring/scheduled generation) — but they can't
get there without lever #1 first, because most agencies' "data source" is already a
half-dozen live client accounts, not a folder of CSVs someone remembers to re-export.
**Warehouse/live-source connectivity is the prerequisite for recurring generation to
mean anything** — a scheduled job re-reading a stale CSV isn't recurring value, it's
the same report twice. That's why lever #1 is built first in this session even though
Segment 1 (the top-ranked ICP) technically ranks lever #3 highest — #1 is the enabling
dependency, not a detour.

Everything after this document targets **Segment 1 first**, with Segment 2's QA badge
(lever #2) as the immediate next build (cheap relative to its trust payoff), and
Segment 3's full warehouse story as the long-tail beneficiary of the same lever #1
infrastructure.
