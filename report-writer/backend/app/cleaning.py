"""
Deterministic data-cleaning layer — sits between ingestion (parsers.py /
sql_source.py) and the canonical DataFrame that metrics.py sees.

Same trust rule as the rest of this app: nothing here is AI. Real client
exports are rarely clean — currency-formatted numbers, mixed date formats,
stray whitespace/casing, typo'd categories, duplicate rows, and blank or
placeholder cells are the norm, not the exception. Every fix below is plain,
auditable pandas/regex, and every fix is logged as a structured issue so a
report can show a client exactly what was found and corrected — instead of
silently changing their numbers (the same honesty the AI-narrative guardrails
already give the rest of the pipeline).

A value that genuinely can't be read is left missing (NaN/NaT), never
invented as zero — callers decide fill policy explicitly and only after the
issue has been logged.
"""
from __future__ import annotations

from difflib import get_close_matches

import numpy as np
import pandas as pd

_NULL_TOKENS = {"", "na", "n/a", "null", "none", "nan", "-", "--", "unknown", "tbd", "pending"}

_DATE_FORMATS = ["%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y", "%Y/%m/%d",
                  "%d %b %Y", "%d-%b-%Y", "%B %d, %Y", "%b %d %Y", "%m/%d/%y"]


def _issue(source: str, column: str, kind: str, message: str, count: int, sample: list | None = None) -> dict:
    return {
        "source": source,
        "column": column,
        "kind": kind,
        "message": message,
        "count": int(count),
        "sample": [str(s) for s in (sample or [])],
    }


def missing_column(source: str, column: str, row_count: int, default) -> dict:
    """T2 — a column entirely absent from the upload is a different, more
    serious fact than a column present-but-messy: every row silently got
    `default` instead of a real value. Logged as its own `kind` (distinct
    from `missing_value`, which is a per-row gap in an otherwise-real
    column) so report_builder.py can tell "this whole business metric was
    never in the file" from "a few rows in a real column were blank" and
    decide whether a chart built on it would be misleading rather than
    just incomplete."""
    return _issue(
        source, column, "column_missing",
        f"'{column}' was not found in the uploaded file; every row was filled with a default "
        f"({default!r}) instead of a real value.",
        row_count,
    )


def chart_omitted(source: str, caption: str, message: str) -> dict:
    """T2 — a chart the active template declared gets skipped rather than
    rendered because a column it depends on was entirely missing (see
    missing_column() above / template_specs.select_renderable_charts()).
    Shaped as a regular issue so it flows through summarize() into the same
    client-facing Data Quality section a malformed-column note would, rather
    than needing a second, parallel "why isn't this chart here" surface."""
    return _issue(source, caption, "chart_omitted", message, 1)


def clean_numeric(series: pd.Series, *, source: str, column: str) -> tuple[pd.Series, list[dict]]:
    """Coerces currency/percentage/accounting-style strings to plain floats:
    "$1,234.56" -> 1234.56, "(500)" -> -500, "12%" -> 12, blank/placeholder
    tokens ("N/A", "-", "TBD", ...) -> missing. Already-numeric columns pass
    through untouched with no issues logged."""
    if pd.api.types.is_numeric_dtype(series):
        return series, []

    as_str = series.astype(str).str.strip()
    is_blankish = series.isna() | as_str.str.lower().isin(_NULL_TOKENS)
    needs_reformat = as_str.str.contains(r"[\$,%()]", regex=True) & ~is_blankish

    working = as_str.mask(is_blankish, "")
    working = working.str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    working = working.str.replace(r"[,$%\s]", "", regex=True)
    working = working.mask(is_blankish, np.nan)

    cleaned = pd.to_numeric(working, errors="coerce")

    issues: list[dict] = []
    fixed_mask = needs_reformat & cleaned.notna()
    n_fixed = int(fixed_mask.sum())
    if n_fixed:
        issues.append(_issue(
            source, column, "numeric_format",
            f"Reformatted {n_fixed} value(s) in '{column}' from currency/percentage/accounting "
            f"style (e.g. \"$1,234.56\", \"(500)\") into plain numbers.",
            n_fixed, as_str[fixed_mask].head(3).tolist(),
        ))

    unparseable_mask = ~is_blankish & cleaned.isna()
    n_unparseable = int(unparseable_mask.sum())
    if n_unparseable:
        issues.append(_issue(
            source, column, "numeric_unparseable",
            f"{n_unparseable} value(s) in '{column}' were not recognizable numbers and are being "
            f"treated as missing (not zero).",
            n_unparseable, as_str[unparseable_mask].head(3).tolist(),
        ))

    n_missing = int(is_blankish.sum())
    if n_missing:
        issues.append(_issue(
            source, column, "missing_value",
            f"{n_missing} value(s) in '{column}' were blank or a placeholder (e.g. \"N/A\") and are "
            f"being treated as missing.",
            n_missing,
        ))

    return cleaned, issues


def clean_dates(series: pd.Series, *, source: str, column: str) -> tuple[pd.Series, list[dict]]:
    """pandas' own to_datetime already handles ISO and most common formats in
    one pass; this adds a second, per-value pass with explicit formats for
    the stragglers, so a handful of rows in a different format (e.g. a few
    MM/DD/YYYY rows mixed into an otherwise-ISO export) don't get silently
    dropped as unparseable — or crash the whole run the way an uncaught
    parser exception would."""
    original_notna = series.notna()
    result = pd.to_datetime(series, errors="coerce")

    recovered_count = 0
    remaining = result.isna() & original_notna
    if remaining.any():
        for fmt in _DATE_FORMATS:
            remaining = result.isna() & original_notna
            if not remaining.any():
                break
            candidates = pd.to_datetime(
                series[remaining].astype(str).str.strip(), format=fmt, errors="coerce"
            )
            hit = candidates.notna()
            if hit.any():
                result.loc[candidates.index[hit]] = candidates[hit]
                recovered_count += int(hit.sum())

    issues: list[dict] = []
    if recovered_count:
        issues.append(_issue(
            source, column, "date_format",
            f"Parsed {recovered_count} date(s) in '{column}' that used a different format than the "
            f"rest of the file (e.g. MM/DD/YYYY mixed with ISO dates).",
            recovered_count,
        ))

    unparseable_mask = result.isna() & original_notna
    n_unparseable = int(unparseable_mask.sum())
    if n_unparseable:
        issues.append(_issue(
            source, column, "date_unparseable",
            f"{n_unparseable} value(s) in '{column}' could not be parsed as a date and are being "
            f"treated as missing.",
            n_unparseable, series[unparseable_mask].astype(str).head(3).tolist(),
        ))

    n_missing = int((~original_notna).sum())
    if n_missing:
        issues.append(_issue(
            source, column, "missing_value",
            f"{n_missing} value(s) in '{column}' were blank and are being treated as missing.",
            n_missing,
        ))

    return result, issues


def clean_categorical(
    series: pd.Series, *, source: str, column: str,
    known_values: list[str] | None = None, fuzzy_cutoff: float = 0.82,
) -> tuple[pd.Series, list[dict]]:
    """Trims whitespace/collapses double-spacing. If a fixed vocabulary is
    given (e.g. channel names), also snaps close-but-not-exact spellings
    ("Orgnic Search", "PAID SOCIAL") to the canonical value — only above a
    strict similarity cutoff, and only ever logged, never silent, so a value
    is never recoded into the wrong bucket without a trace."""
    # astype(str) on an object-dtype column can leave real NaN/None entries
    # un-stringified (a pandas quirk) — mask them to "" explicitly so every
    # element below is a genuine str, never a float, before any .lower() call.
    is_na = series.isna()
    as_str = series.astype(str).mask(is_na, "")
    trimmed = as_str.str.strip().str.replace(r"\s+", " ", regex=True)

    issues: list[dict] = []
    n_missing = int(is_na.sum())
    if n_missing:
        issues.append(_issue(
            source, column, "missing_value",
            f"{n_missing} value(s) in '{column}' were blank and are being treated as missing.",
            n_missing,
        ))

    n_trimmed = int(((trimmed != as_str) & ~is_na).sum())
    if n_trimmed:
        issues.append(_issue(
            source, column, "whitespace_normalized",
            f"Trimmed stray whitespace/spacing in {n_trimmed} value(s) of '{column}'.", n_trimmed,
        ))

    if not known_values:
        return trimmed, issues

    lookup = {v.lower(): v for v in known_values}
    result = trimmed.copy()
    n_snapped = 0
    examples: list[str] = []
    for val in trimmed.unique():
        if not val or val.lower() in lookup:
            canonical = lookup.get(val.lower())
            if canonical and canonical != val:
                result = result.replace(val, canonical)
            continue
        match = get_close_matches(val.lower(), lookup.keys(), n=1, cutoff=fuzzy_cutoff)
        if match:
            canonical = lookup[match[0]]
            count = int((trimmed == val).sum())
            result = result.replace(val, canonical)
            n_snapped += count
            if len(examples) < 3:
                examples.append(f"{val!r} -> {canonical!r}")

    if n_snapped:
        issues.append(_issue(
            source, column, "category_typo_corrected",
            f"Corrected {n_snapped} value(s) in '{column}' that looked like typos of a known "
            f"category ({'; '.join(examples)}).",
            n_snapped,
        ))

    return result, issues


def dedupe(df: pd.DataFrame, *, source: str, subset: list[str] | None = None) -> tuple[pd.DataFrame, list[dict]]:
    """Drops exact duplicate rows (or duplicates on a subset of columns —
    e.g. the same date+channel+device appearing twice, which double-counts
    every metric downstream if left in)."""
    cols = [c for c in (subset or []) if c in df.columns] or None
    n_before = len(df)
    deduped = df.drop_duplicates(subset=cols, keep="first")
    n_dropped = n_before - len(deduped)

    issues: list[dict] = []
    if n_dropped:
        where = f" (matched on {', '.join(cols)})" if cols else ""
        issues.append(_issue(
            source, ",".join(cols) if cols else "*row*", "duplicate_rows",
            f"Removed {n_dropped} duplicate row(s){where} — left in, they would have double-counted "
            f"every metric derived from them.",
            n_dropped,
        ))

    return deduped.reset_index(drop=True), issues


def drop_missing_required(df: pd.DataFrame, *, source: str, required: list[str]) -> tuple[pd.DataFrame, list[dict]]:
    """Drops rows missing a field with no sane default — e.g. a row with no
    date can't be placed in any time period, so it can only distort a report
    if kept."""
    present = [c for c in required if c in df.columns]
    if not present:
        return df, []

    mask = pd.Series(True, index=df.index)
    for col in present:
        mask &= df[col].notna()

    n_dropped = int((~mask).sum())
    issues: list[dict] = []
    if n_dropped:
        issues.append(_issue(
            source, ",".join(present), "unusable_rows_dropped",
            f"Dropped {n_dropped} row(s) missing a required field ({', '.join(present)}) — there's "
            f"no way to place them in the report without one.",
            n_dropped,
        ))

    return df[mask].reset_index(drop=True), issues


def summarize(issues: list[dict]) -> dict:
    """Rolls a flat issue list into the shape report_builder attaches to the
    report as report['data_quality'] — a client-facing summary of what messy
    input was found and fixed before a single metric was computed."""
    by_kind: dict[str, int] = {}
    for i in issues:
        by_kind[i["kind"]] = by_kind.get(i["kind"], 0) + i["count"]

    return {
        "total_issues_found": len(issues),
        "total_values_affected": sum(i["count"] for i in issues),
        "by_kind": by_kind,
        "details": issues,
    }
