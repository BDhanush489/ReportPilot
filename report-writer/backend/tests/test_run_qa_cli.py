"""
Headless-CLI tests for scripts/run_qa.py, run as real subprocesses so exit
codes are checked the way an actual caller (a cron job, once Lever 3 lands)
would see them.

Deliberately doesn't go through report_builder.build_report() for fixtures —
that calls the live agent (Claude/Ollama/template chain), which is slow and
non-deterministic. Instead builds a report dir directly, the same shape
main.py's _persist_report writes, from the same ANALYTICS_DF fixture used in
test_qa.py.
"""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from app import metrics as metrics_mod, parsers
from app.qa import compute_source_fingerprint

BACKEND_DIR = Path(__file__).resolve().parent.parent
RUN_QA = BACKEND_DIR / "scripts" / "run_qa.py"

ANALYTICS_DF = pd.DataFrame([
    {"date": "2026-01-01", "sessions": 100, "new_users": 80, "conversions": 5, "revenue_usd": 500.0,
     "channel_group": "Organic Search", "device_category": "desktop"},
    {"date": "2026-01-05", "sessions": 150, "new_users": 90, "conversions": 8, "revenue_usd": 800.0,
     "channel_group": "Paid Search", "device_category": "mobile"},
    {"date": "2026-01-12", "sessions": 200, "new_users": 130, "conversions": 12, "revenue_usd": 1200.0,
     "channel_group": "Organic Search", "device_category": "desktop"},
    {"date": "2026-01-20", "sessions": 160, "new_users": 95, "conversions": 7, "revenue_usd": 700.0,
     "channel_group": "Paid Search", "device_category": "desktop"},
])
ANALYTICS_DF["date"] = pd.to_datetime(ANALYTICS_DF["date"])


def _strip_private(payload):
    if isinstance(payload, dict):
        return {k: _strip_private(v) for k, v in payload.items() if not str(k).startswith("_")}
    if isinstance(payload, list):
        return [_strip_private(v) for v in payload]
    return payload


def _write_report_dir(tmp_path: Path, metrics_payload: dict, report: dict, fingerprints: dict) -> Path:
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    (report_dir / "meta.json").write_text(json.dumps({"report": report}), encoding="utf-8")
    (report_dir / "metrics.json").write_text(
        json.dumps({"metrics": metrics_payload, "source_fingerprints": fingerprints}), encoding="utf-8",
    )
    return report_dir


def _write_source_csv_and_parse(tmp_path: Path) -> tuple[Path, pd.DataFrame]:
    """Writes ANALYTICS_DF as a CSV and reads it back through the same
    parsers.load_web_analytics() the CLI itself uses — the "recorded"
    fingerprint has to be taken from the post-parse df (which normalize_web_
    analytics may reshape: default columns added, dtypes coerced), the same
    thing report_builder.py fingerprints at real generation time. Fingerprinting
    the raw fixture directly here would drift against the CLI's re-derivation
    even when nothing about the underlying data actually changed."""
    csv_path = tmp_path / "analytics.csv"
    ANALYTICS_DF.to_csv(csv_path, index=False)
    with open(csv_path, "rb") as fh:
        parsed_df, _ = parsers.load_web_analytics(fh)
    return csv_path, parsed_df


def _run_cli(report_dir: Path, analytics_csv: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RUN_QA), "--report-dir", str(report_dir),
         "--analytics-csv", str(analytics_csv), "--no-write"],
        capture_output=True, text=True, cwd=str(BACKEND_DIR),
    )


def test_cli_exits_zero_on_a_clean_report(tmp_path):
    csv_path, parsed_df = _write_source_csv_and_parse(tmp_path)
    metrics_payload = {"analytics": _strip_private(metrics_mod.analytics_metrics(parsed_df))}
    report = {
        "report_title": "t", "period_label": "p", "executive_summary": "Clean summary, no numbers here.",
        "highlights": [], "watchouts": [], "sections": [], "next_steps": [],
    }
    fp = compute_source_fingerprint(parsed_df)
    report_dir = _write_report_dir(tmp_path, metrics_payload, report, {"analytics": fp})

    proc = _run_cli(report_dir, csv_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["badge"] in ("PASS", "PASS-WITH-WARNINGS")
    assert payload["drifted_sources"] == []


def test_cli_exits_one_on_a_tampered_total(tmp_path):
    csv_path, parsed_df = _write_source_csv_and_parse(tmp_path)
    metrics_payload = {"analytics": _strip_private(metrics_mod.analytics_metrics(parsed_df))}
    tampered = copy.deepcopy(metrics_payload)
    tampered["analytics"]["totals"]["revenue_usd"] += 999999.0
    report = {
        "report_title": "t", "period_label": "p", "executive_summary": "Clean summary, no numbers here.",
        "highlights": [], "watchouts": [], "sections": [], "next_steps": [],
    }
    fp = compute_source_fingerprint(parsed_df)
    report_dir = _write_report_dir(tmp_path, tampered, report, {"analytics": fp})

    proc = _run_cli(report_dir, csv_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["badge"] == "FAIL"
    assert "aggregation_sanity" in payload["failing_checks"]


def test_cli_exits_two_when_source_has_drifted(tmp_path):
    csv_path, parsed_df = _write_source_csv_and_parse(tmp_path)
    metrics_payload = {"analytics": _strip_private(metrics_mod.analytics_metrics(parsed_df))}
    report = {
        "report_title": "t", "period_label": "p", "executive_summary": "Clean summary, no numbers here.",
        "highlights": [], "watchouts": [], "sections": [], "next_steps": [],
    }
    # Fingerprint recorded at "generation time" doesn't match what we'll
    # re-derive below — simulates the source having moved since the report
    # was built.
    stale_fp = {"row_count": 999, "sha256": "not-the-real-hash"}
    report_dir = _write_report_dir(tmp_path, metrics_payload, report, {"analytics": stale_fp})

    proc = _run_cli(report_dir, csv_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["badge"] == "FAIL" or payload["drifted_sources"] == ["analytics"]
    assert payload["drifted_sources"] == ["analytics"]
    assert payload["aggregation_sanity"]["inconclusive_sources"] == ["analytics"]


def test_cli_writes_qa_json_unless_no_write(tmp_path):
    csv_path, parsed_df = _write_source_csv_and_parse(tmp_path)
    metrics_payload = {"analytics": _strip_private(metrics_mod.analytics_metrics(parsed_df))}
    report = {
        "report_title": "t", "period_label": "p", "executive_summary": "Clean summary, no numbers here.",
        "highlights": [], "watchouts": [], "sections": [], "next_steps": [],
    }
    fp = compute_source_fingerprint(parsed_df)
    report_dir = _write_report_dir(tmp_path, metrics_payload, report, {"analytics": fp})

    proc = subprocess.run(
        [sys.executable, str(RUN_QA), "--report-dir", str(report_dir), "--analytics-csv", str(csv_path)],
        capture_output=True, text=True, cwd=str(BACKEND_DIR),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (report_dir / "qa.json").exists()
