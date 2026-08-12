# What's new in ReportPilot — and why it matters to you

## If you're a boutique agency (5–50 clients)

**You can now connect a client's live data instead of re-exporting CSVs every
month.** Onboard a client once — point ReportPilot at their analytics/CRM
tables, it figures out what your columns mean (you confirm/correct anything
it's unsure about), and every report after that pulls live, with zero
re-upload. For an agency doing this across 20+ clients, that's the difference
between "reporting day" being a scramble and it not existing as a task at all.

*What we verified:* a full client onboarding + report generation from a real
SQL database (not CSVs) — same PDF, same charts, same numbers as the file-
upload path, byte-for-byte identical metrics. That's the proof this isn't a
demo trick; it's the same trustworthy pipeline with a different front door.

*Coming next (see the build plan):* recurring/scheduled generation, so once a
client is connected, their report just arrives on the 1st of the month — no
one has to remember to run it.

## If you're a fractional/solo consultant

**Nothing changes about how you use ReportPilot today** — file upload still
works exactly as before, and always will (it's the fallback path even for
warehouse-connected clients). What's coming next for you specifically: a
"QA passed" badge and methodology footnote on every report, so you can hand a
client a PDF without personally re-checking every number first. That's the
next thing we're building, prioritized because it's the single highest-
leverage thing for someone whose name is the only name on the report.

## If you're an in-house growth team

**ReportPilot can now speak SQL, not just CSV.** Your data already lives in a
warehouse — Postgres works today; Snowflake, BigQuery, and Databricks
connectors are written to each platform's real API and ready to test the
moment we have a live instance to point them at. The same schema-mapping
step that helps agencies onboard clients is what lets your data team hand
this off without a bespoke integration project: point it at your tables, the
system asks what a handful of ambiguous columns mean, and it remembers the
answer forever.

## Under the hood (for anyone technical evaluating this)

- **The core invariant didn't move an inch:** every number in every report —
  whether sourced from an uploaded CSV or a live SQL warehouse — is computed
  by the same deterministic pandas code in `metrics.py`. The only thing that
  changed is where the *rows* come from before they reach that code. The
  AI touches column *names* during onboarding (matching your schema to ours)
  and report *prose* during narrative writing — never a data value, never a
  calculation.
- We also found and fixed two real bugs while stress-testing this: unrounded
  floats leaking into AI-written narrative (root-caused to `metrics.py`, not
  the model), and the local free-tier model occasionally leaving empty
  `next_steps`/`highlights` lists that a validation guard now catches before
  they'd ever reach you.
