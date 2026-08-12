"""Tests for app/viz/normalize.py — deterministic, logged, reversible value
normalization (L1). Normalization is NOT mutation: the raw column is always
preserved, and every re-encoding is logged with the rule that produced it."""
import pandas as pd
import pytest

from app.viz.normalize import (
    format_back,
    normalize_numeric_column,
    normalize_temporal_column,
)


# ---------------------------------------------------------------------------
# Numeric: currency / thousands / percent / K-M-B suffix / accounting negative
# ---------------------------------------------------------------------------

def test_currency_symbol_is_parsed_and_logged():
    raw = pd.Series(["$1,234.56"])
    result = normalize_numeric_column(raw)
    assert result.parsed.iloc[0] == pytest.approx(1234.56)
    assert result.log[0].rule == "currency_symbol_stripped+thousands_separator_removed"
    assert result.log[0].original == "$1,234.56"


def test_accounting_negative_parens_becomes_negative_number():
    raw = pd.Series(["(500)", "(1,200.50)"])
    result = normalize_numeric_column(raw)
    assert result.parsed.iloc[0] == -500.0
    assert result.parsed.iloc[1] == -1200.50
    assert all("accounting_negative" in e.rule for e in result.log)


def test_percent_parses_to_fractional_value():
    raw = pd.Series(["12%", "0.5%"])
    result = normalize_numeric_column(raw)
    assert result.parsed.iloc[0] == pytest.approx(0.12)
    assert result.parsed.iloc[1] == pytest.approx(0.005)
    assert result.log[0].rule == "percent_to_fraction"


def test_k_m_b_suffix_multipliers():
    raw = pd.Series(["1.2K", "3.5M", "2B"])
    result = normalize_numeric_column(raw)
    assert result.parsed.tolist() == pytest.approx([1200.0, 3_500_000.0, 2_000_000_000.0])
    assert all(e.rule == "magnitude_suffix" for e in result.log)


def test_plain_numbers_parse_with_no_log_entry():
    # Nothing to disclose -- already a plain number, no transformation happened.
    raw = pd.Series(["42", "3.14"])
    result = normalize_numeric_column(raw)
    assert result.parsed.tolist() == [42.0, 3.14]
    assert result.log == []


def test_european_decimal_comma_locale_is_respected():
    # "1.234,56" under a comma-decimal locale means 1234.56, not 1.234
    raw = pd.Series(["1.234,56"])
    result = normalize_numeric_column(raw, decimal=",")
    assert result.parsed.iloc[0] == pytest.approx(1234.56)


def test_us_thousands_comma_is_not_confused_with_decimal():
    raw = pd.Series(["1,234.56"])
    result = normalize_numeric_column(raw, decimal=".")
    assert result.parsed.iloc[0] == pytest.approx(1234.56)


def test_combined_currency_percent_and_accounting_negative():
    raw = pd.Series(["($1,500.00)"])
    result = normalize_numeric_column(raw)
    assert result.parsed.iloc[0] == -1500.0


# ---------------------------------------------------------------------------
# Logging / raw preservation / reversibility
# ---------------------------------------------------------------------------

def test_raw_column_is_preserved_unchanged():
    raw = pd.Series(["$1,234.56", "42"])
    result = normalize_numeric_column(raw)
    pd.testing.assert_series_equal(result.raw, raw)


def test_source_series_is_never_mutated():
    raw = pd.Series(["$1,234.56"])
    before = raw.copy()
    normalize_numeric_column(raw)
    pd.testing.assert_series_equal(raw, before)


def test_every_transformed_value_has_a_log_entry_original_to_parsed():
    raw = pd.Series(["$100", "$200"])
    result = normalize_numeric_column(raw)
    assert len(result.log) == 2
    assert result.log[0].original == "$100" and result.log[0].parsed == 100.0
    assert result.log[1].original == "$200" and result.log[1].parsed == 200.0


def test_format_back_reconstructs_a_display_string():
    assert format_back(0.12, "percent_to_fraction") == "12.00%"
    assert format_back(1234.56, "currency_symbol_stripped") == "$1,234.56"
    assert format_back(-500.0, "accounting_negative") == "(500.00)"


# ---------------------------------------------------------------------------
# Unparseable cells: flagged, never silently coerced to 0
# ---------------------------------------------------------------------------

def test_unparseable_cell_is_flagged_not_coerced_to_zero():
    raw = pd.Series(["not a number", "$100"])
    result = normalize_numeric_column(raw)
    assert result.parsed.iloc[0] != 0.0  # NaN, not silently zero
    assert pd.isna(result.parsed.iloc[0])
    assert len(result.unparseable) == 1
    assert result.unparseable[0].original == "not a number"


def test_null_cell_is_distinct_from_unparseable_cell():
    # A genuinely blank/missing cell and a cell with garbage text are
    # semantically different situations -- only the latter is "unparseable".
    raw = pd.Series([None, "garbage"])
    result = normalize_numeric_column(raw)
    assert pd.isna(result.parsed.iloc[0])
    assert pd.isna(result.parsed.iloc[1])
    assert len(result.unparseable) == 1  # only the garbage cell, not the null one
    assert result.unparseable[0].original == "garbage"


# ---------------------------------------------------------------------------
# Temporal: mixed formats + Excel serials + timezone rule
# ---------------------------------------------------------------------------

def test_mixed_date_string_formats_parse():
    raw = pd.Series(["2026-01-15", "01/20/2026", "March 3, 2026"])
    result = normalize_temporal_column(raw)
    assert result.parsed.iloc[0] == pd.Timestamp("2026-01-15")
    assert result.parsed.iloc[1] == pd.Timestamp("2026-01-20")
    assert result.parsed.iloc[2] == pd.Timestamp("2026-03-03")


def test_excel_serial_date_is_detected_and_logged():
    # 45658 -> 2025-01-01 under the correct (1899-12-30) Excel epoch --
    # verified independently: (Timestamp("2025-01-01") - Timestamp("1899-12-30")).days == 45658.
    raw = pd.Series(["45658"])
    result = normalize_temporal_column(raw)
    assert result.parsed.iloc[0] == pd.Timestamp("2025-01-01")
    assert result.excel_serials_detected == 1
    assert result.log[0].rule == "excel_serial_date"


def test_timezone_rule_is_stated_and_applied():
    raw = pd.Series(["2026-01-15T10:00:00+05:00"])
    result = normalize_temporal_column(raw)
    assert result.parsed.iloc[0].tzinfo is None  # stored naive, per the stated rule
    assert result.parsed.iloc[0] == pd.Timestamp("2026-01-15T05:00:00")  # converted to UTC first
    assert "UTC" in result.timezone_rule


def test_unparseable_date_is_flagged_not_silently_nat_treated_as_valid():
    raw = pd.Series(["2026-01-15", "not a date at all"])
    result = normalize_temporal_column(raw)
    assert pd.isna(result.parsed.iloc[1])
    assert len(result.unparseable) == 1
    assert result.unparseable[0].original == "not a date at all"


def test_temporal_raw_column_is_preserved():
    raw = pd.Series(["2026-01-15"])
    result = normalize_temporal_column(raw)
    pd.testing.assert_series_equal(result.raw, raw)
