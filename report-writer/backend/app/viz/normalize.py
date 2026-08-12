"""
Value normalization (L1): turns L0's raw text columns into computable
numbers/dates without ever altering meaning.

Distinction this whole module exists to keep visible (see qa.py's own
docstring for the equivalent statement about the canonical pipeline):
normalization is deterministic, logged, and value-preserving — "$1,200"
becoming 1200.0 is not a mutation, it's a disclosed re-encoding of the same
value. Nothing here ever guesses a value that isn't already in the cell, and
the raw column is never overwritten — every result carries the original
text alongside the parsed number so the transformation is always auditable.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

import pandas as pd

#: 1899-12-30 is the correct Excel epoch (not 1900-01-01 — Excel's famous
#: leap-year bug means day 0 has to be Dec 30, 1899 for serials to land on
#: the right real-world date from 1900-03-01 onward, which is the range
#: every real spreadsheet date actually falls in).
_EXCEL_EPOCH = pd.Timestamp("1899-12-30")

#: Plausible Excel date-serial range: 1 (1900-01-01) to 60000 (~2064). Only
#: used to decide whether a bare number *might* be an Excel serial date —
#: never applied unless the column's string-date parse rate is too low to
#: already have called it temporal by L0's own rules.
_EXCEL_SERIAL_RANGE = (1, 60000)

_CURRENCY_SYMBOLS = r"[$€£¥₹]"
_SUFFIX_MULTIPLIERS = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


@dataclass
class NormalizationEntry:
    row_index: int
    original: str
    parsed: float | None
    rule: str


@dataclass
class NumericNormalizationResult:
    parsed: pd.Series  # float64, NaN where unparseable -- never 0
    raw: pd.Series  # untouched original text, same index
    log: list[NormalizationEntry] = field(default_factory=list)
    unparseable: list[NormalizationEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "log": [asdict(e) for e in self.log],
            "unparseable": [asdict(e) for e in self.unparseable],
            "unparseable_count": len(self.unparseable),
        }


def _parse_one_numeric(raw_value: str, decimal: str) -> tuple[float | None, str | None]:
    """Returns (value, rule_applied). rule_applied is None for a value that
    was already a plain number (no transformation happened — nothing to
    log). Returns (None, None) for genuinely unparseable text."""
    s = raw_value.strip()
    if s == "":
        return None, None

    rules: list[str] = []
    working = s

    is_accounting_negative = working.startswith("(") and working.endswith(")")
    if is_accounting_negative:
        working = working[1:-1]
        rules.append("accounting_negative")

    if re.match(rf"^{_CURRENCY_SYMBOLS}", working) or re.search(rf"{_CURRENCY_SYMBOLS}$", working):
        working = re.sub(_CURRENCY_SYMBOLS, "", working)
        rules.append("currency_symbol_stripped")

    is_percent = working.endswith("%")
    if is_percent:
        working = working[:-1]
        rules.append("percent_to_fraction")

    working = working.strip()
    suffix_multiplier = 1.0
    if working and working[-1].upper() in _SUFFIX_MULTIPLIERS:
        suffix_multiplier = _SUFFIX_MULTIPLIERS[working[-1].upper()]
        working = working[:-1]
        rules.append("magnitude_suffix")

    # Thousands-separator handling depends on the locale's decimal convention
    # (see profiler.py's load_meta.decimal, detected from the delimiter).
    if decimal == ",":
        working = working.replace(".", "").replace(",", ".")
        if "." in s or "," in s:
            rules.append("thousands_separator_removed")
    else:
        if working.count(",") and re.search(r",\d{3}(\D|$)", working):
            working = working.replace(",", "")
            rules.append("thousands_separator_removed")

    working = working.strip()
    if working in ("", "-", "+"):
        return None, None

    try:
        value = float(working)
    except ValueError:
        return None, None

    if is_percent:
        value = value / 100
    value *= suffix_multiplier
    if is_accounting_negative:
        value = -abs(value)

    if not rules:
        return value, None
    return value, "+".join(dict.fromkeys(rules))  # de-dupe, preserve order


def normalize_numeric_column(raw: pd.Series, decimal: str = ".") -> NumericNormalizationResult:
    """raw: a string-dtype column exactly as profiler.py's load_any/typing
    saw it. decimal: "." or "," — from profiler.py's load_meta, the
    delimiter-implied decimal convention (see profiler.py's docstring)."""
    parsed_values: dict = {}
    log: list[NormalizationEntry] = []
    unparseable: list[NormalizationEntry] = []

    for idx, value in raw.items():
        if pd.isna(value):
            parsed_values[idx] = float("nan")
            continue
        text = str(value)
        parsed, rule = _parse_one_numeric(text, decimal)
        if parsed is None:
            parsed_values[idx] = float("nan")
            unparseable.append(NormalizationEntry(row_index=idx, original=text, parsed=None, rule="unparseable"))
            continue
        parsed_values[idx] = parsed
        if rule:
            log.append(NormalizationEntry(row_index=idx, original=text, parsed=parsed, rule=rule))

    parsed_series = pd.Series(parsed_values, index=raw.index, dtype="float64")
    return NumericNormalizationResult(parsed=parsed_series, raw=raw.copy(), log=log, unparseable=unparseable)


def format_back(value: float, rule: str) -> str:
    """Best-effort reconstruction of a display string from a parsed value +
    the rule that produced it — a convenience for "reversible," not the
    guarantee itself (the guarantee is that .raw is always kept alongside
    .parsed; this can't always recover the exact original formatting, e.g.
    which currency symbol or how many thousands-groups)."""
    if "percent_to_fraction" in rule:
        return f"{value * 100:.2f}%"
    if "currency_symbol_stripped" in rule:
        return f"${value:,.2f}"
    if "accounting_negative" in rule and value < 0:
        return f"({abs(value):,.2f})"
    return f"{value:,.2f}"


# ---------------------------------------------------------------------------
# Temporal normalization
# ---------------------------------------------------------------------------

@dataclass
class TemporalNormalizationResult:
    parsed: pd.Series  # datetime64[ns], NaT where unparseable
    raw: pd.Series
    excel_serials_detected: int
    log: list[NormalizationEntry] = field(default_factory=list)
    unparseable: list[NormalizationEntry] = field(default_factory=list)
    timezone_rule: str = "any timezone-aware timestamp is converted to UTC, then stored naive"

    def to_dict(self) -> dict:
        return {
            "excel_serials_detected": self.excel_serials_detected,
            "timezone_rule": self.timezone_rule,
            "log": [asdict(e) for e in self.log],
            "unparseable": [asdict(e) for e in self.unparseable],
            "unparseable_count": len(self.unparseable),
        }


def _to_naive_utc(ts: pd.Timestamp) -> pd.Timestamp:
    if ts.tzinfo is not None:
        return ts.tz_convert("UTC").tz_localize(None)
    return ts


def normalize_temporal_column(raw: pd.Series) -> TemporalNormalizationResult:
    """Parses mixed date-string formats first; for values that fail string
    parsing but parse as a plain number inside the plausible Excel-serial
    range, reinterprets them as an Excel date serial (see _EXCEL_EPOCH)."""
    parsed_values: dict = {}
    log: list[NormalizationEntry] = []
    unparseable: list[NormalizationEntry] = []
    excel_serial_count = 0

    for idx, value in raw.items():
        if pd.isna(value):
            parsed_values[idx] = pd.NaT
            continue
        text = str(value).strip()
        if text == "":
            parsed_values[idx] = pd.NaT
            continue

        ts = pd.to_datetime(text, errors="coerce")
        if pd.notna(ts):
            parsed_values[idx] = _to_naive_utc(ts)
            continue

        numeric = pd.to_numeric(text, errors="coerce")
        if pd.notna(numeric) and _EXCEL_SERIAL_RANGE[0] <= numeric <= _EXCEL_SERIAL_RANGE[1]:
            serial_ts = _EXCEL_EPOCH + pd.to_timedelta(float(numeric), unit="D")
            parsed_values[idx] = serial_ts
            excel_serial_count += 1
            log.append(NormalizationEntry(row_index=idx, original=text, parsed=serial_ts.timestamp(), rule="excel_serial_date"))
            continue

        parsed_values[idx] = pd.NaT
        unparseable.append(NormalizationEntry(row_index=idx, original=text, parsed=None, rule="unparseable"))

    parsed_series = pd.Series(parsed_values, index=raw.index)
    return TemporalNormalizationResult(
        parsed=parsed_series, raw=raw.copy(),
        excel_serials_detected=excel_serial_count, log=log, unparseable=unparseable,
    )
