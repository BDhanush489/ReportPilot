#!/usr/bin/env python
"""
Headless auto-QA runner: given a previously generated report directory
(generated/<report_id>/, containing meta.json and metrics.json — see
app/main.py::_persist_report), re-derives source rows from the same files
the report was originally built from, and runs app/qa.py's checks against it.

Usage:
    python scripts/run_qa.py --report-dir ../generated/<report_id> \
        --analytics-csv sample_data/web_analytics.csv \
        --seo-csv sample_data/seo_audit.csv \
        --sales-xlsx sample_data/sales_pipeline.xlsx

Source file args are optional and independent — pass whichever ones you have.
A source with no file supplied, or whose re-derived fingerprint doesn't match
what was recorded at generation time, is excluded from aggregation sanity
(marked inconclusive, never silently treated as a pass) rather than compared
against data that may no longer be the data the report was actually built
from.

Exit codes, most severe first:
    1  badge is FAIL — a check found something concretely wrong.
    2  no FAIL, but at least one supplied source's fingerprint doesn't match
       what was recorded at generation time — the source moved since the
       report was built; aggregation sanity for it is inconclusive, not
       failed, but that's worth a distinct signal from an ordinary warning.
    0  clean run — badge is PASS or PASS-WITH-WARNINGS, no drift.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import parsers, qa  # noqa: E402


def _load_report_dir(report_dir: Path) -> tuple[dict, dict, dict]:
    meta = json.loads((report_dir / "meta.json").read_text(encoding="utf-8"))
    metrics_doc = json.loads((report_dir / "metrics.json").read_text(encoding="utf-8"))
    return meta["report"], metrics_doc["metrics"], metrics_doc.get("source_fingerprints", {})


def _derive_source_frames(args) -> dict:
    """Re-parses the same way report_builder.py does at generation time, so
    aggregation sanity is comparing against rows produced by the identical
    code path — not a second, potentially-diverging parsing routine."""
    frames = {}
    if args.analytics_csv:
        with open(args.analytics_csv, "rb") as fh:
            df, _ = parsers.load_web_analytics(fh)
        frames["analytics"] = df
    if args.seo_csv:
        with open(args.seo_csv, "rb") as fh:
            df, _ = parsers.load_seo_audit(fh)
        frames["seo"] = df
    if args.sales_xlsx:
        with open(args.sales_xlsx, "rb") as fh:
            deals, monthly, _ = parsers.load_sales_pipeline(fh)
        frames["sales"] = (deals, monthly)
    return frames


def _fingerprint_source_row_df(source: str, frame) -> "object":
    # check_aggregation_sanity wants {"sales": (deals, monthly)} but a
    # fingerprint is a row-count+hash over one DataFrame — for sales that's
    # the deals table, matching how report_builder.py fingerprints it at
    # generation time.
    return frame[0] if source == "sales" else frame


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report-dir", required=True, type=Path)
    ap.add_argument("--analytics-csv", type=Path)
    ap.add_argument("--seo-csv", type=Path)
    ap.add_argument("--sales-xlsx", type=Path)
    ap.add_argument("--no-write", action="store_true", help="Don't write qa.json into --report-dir.")
    args = ap.parse_args()

    report, metrics_payload, recorded_fingerprints = _load_report_dir(args.report_dir)
    derived_frames = _derive_source_frames(args)

    usable_frames = {}
    drifted_sources = []
    for source, frame in derived_frames.items():
        row_df = _fingerprint_source_row_df(source, frame)
        fresh_fp = qa.compute_source_fingerprint(row_df)
        recorded_fp = recorded_fingerprints.get(source)
        if recorded_fp is not None and fresh_fp != recorded_fp:
            drifted_sources.append(source)
            continue  # excluded -> check_aggregation_sanity marks it inconclusive, not failed
        usable_frames[source] = frame

    qa_report = qa.run_qa(report, metrics_payload, source_frames=usable_frames)
    payload = qa_report.to_dict()
    payload["drifted_sources"] = drifted_sources

    print(json.dumps(payload, indent=2))

    if not args.no_write:
        (args.report_dir / "qa.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if qa_report.badge == "FAIL":
        return 1
    if drifted_sources:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
