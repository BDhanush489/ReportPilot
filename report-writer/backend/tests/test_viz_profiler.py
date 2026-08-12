"""Tests for app/viz/profiler.py — schema-agnostic ingest + column typing (L0)."""
import io

import pandas as pd
import pytest

from app.viz.profiler import load_any, profile_column, profile_dataframe

N = 40


def _mixed_dataset() -> pd.DataFrame:
    """All string dtype, matching what load_any actually hands profile_*."""
    return pd.DataFrame({
        "widget_id": [f"W{i:04d}" for i in range(N)],
        "region": [["North", "South", "East", "West"][i % 4] for i in range(N)],
        "sale_date": pd.date_range("2026-01-01", periods=N, freq="D").astype(str),
        "amount": [str(round(100 + i * 3.7, 2)) for i in range(N)],
        "notes": [f"Customer left a detailed comment number {i} about their experience" for i in range(N)],
        "coupon_code_or_blank": [str(i) if i % 3 == 0 else f"promo-{i}" for i in range(N)],
    }).astype(str)


def test_numeric_quantity_column_is_typed_and_profiled_correctly():
    df = _mixed_dataset()
    profile = profile_column(df["amount"], "amount")
    assert profile.inferred_type == "numeric_quantity"
    assert profile.identifier_reason is None
    assert profile.numeric["min"] == 100.0
    assert profile.null_pct == 0.0


def test_categorical_column_is_typed_with_top_values():
    df = _mixed_dataset()
    profile = profile_column(df["region"], "region")
    assert profile.inferred_type == "categorical"
    assert profile.cardinality == 4
    assert not profile.is_free_text
    top_values = {v["value"] for v in profile.categorical["top_values"]}
    assert top_values == {"North", "South", "East", "West"}


def test_temporal_column_is_typed_from_date_strings():
    df = _mixed_dataset()
    profile = profile_column(df["sale_date"], "sale_date")
    assert profile.inferred_type == "temporal"
    assert profile.temporal["min"].startswith("2026-01-01")


def test_short_token_id_column_stays_categorical_not_numeric():
    # "W0004" etc. isn't numeric-parseable, so it can't be numeric_identifier
    # under the 5-type taxonomy -- it's a short, low-entropy categorical.
    df = _mixed_dataset()
    profile = profile_column(df["widget_id"], "widget_id")
    assert profile.inferred_type == "categorical"
    assert not profile.is_free_text


def test_free_text_column_is_flagged():
    df = _mixed_dataset()
    profile = profile_column(df["notes"], "notes")
    assert profile.inferred_type == "free_text"
    assert profile.is_free_text
    assert any("free text" in w for w in profile.warnings)


def test_mixed_type_column_is_flagged_not_crashed_on():
    df = _mixed_dataset()
    profile = profile_column(df["coupon_code_or_blank"], "coupon_code_or_blank")
    assert profile.inferred_type == "mixed"
    assert any("mixed-type" in w for w in profile.warnings)


def test_nulls_are_counted_not_dropped_silently():
    df = _mixed_dataset()
    df.loc[0:4, "amount"] = None
    profile = profile_column(df["amount"], "amount")
    assert profile.null_count == 5
    assert profile.count == N - 5
    assert profile.null_pct == pytest.approx(5 / N * 100)


def test_empty_column_does_not_crash():
    df = pd.DataFrame({"blank": [None] * 10})
    profile = profile_column(df["blank"], "blank")
    assert profile.inferred_type == "empty"
    assert profile.count == 0


def test_typing_has_no_hardcoded_schema():
    # Same data, columns renamed to meaningless labels -- typing must be
    # identical, proving nothing keys off a column *name* like "id"/"date".
    df = _mixed_dataset().rename(columns={
        "widget_id": "col_a", "region": "col_b", "sale_date": "col_c",
        "amount": "col_d", "notes": "col_e", "coupon_code_or_blank": "col_f",
    })
    dataset_profile = profile_dataframe(df)
    assert dataset_profile.columns["col_a"].inferred_type == "categorical"
    assert dataset_profile.columns["col_b"].inferred_type == "categorical"
    assert dataset_profile.columns["col_c"].inferred_type == "temporal"
    assert dataset_profile.columns["col_d"].inferred_type == "numeric_quantity"
    assert dataset_profile.columns["col_e"].is_free_text


def test_profile_dataframe_reports_duplicate_rows():
    df = pd.DataFrame({"a": ["1", "1", "2", "3"], "b": ["x", "x", "y", "z"]})
    dataset_profile = profile_dataframe(df)
    assert dataset_profile.duplicate_row_count == 1
    assert dataset_profile.row_count == 4


def test_source_dataframe_is_never_mutated():
    df = _mixed_dataset()
    before = df.copy(deep=True)
    profile_dataframe(df)
    pd.testing.assert_frame_equal(df, before)


# ---------------------------------------------------------------------------
# numeric_identifier sub-types (zip, phone, year, id)
# ---------------------------------------------------------------------------

def test_year_column_is_a_numeric_identifier_not_a_quantity():
    years = pd.Series([str(2019 + (i % 6)) for i in range(N)])
    profile = profile_column(years, "year")
    assert profile.inferred_type == "numeric_identifier"
    assert profile.identifier_reason == "year"


def test_zip_code_with_leading_zero_is_a_numeric_identifier():
    # A real US ZIP ("00501", the IRS's own ZIP) -- the leading zero is
    # meaningless as a *number* but unambiguous evidence this isn't a quantity.
    zips = pd.Series(["00501", "10001", "00544", "90210", "02134"] * 8)
    profile = profile_column(zips, "zip")
    assert profile.inferred_type == "numeric_identifier"
    assert profile.identifier_reason == "leading_zero_formatting"


def test_fixed_width_zip_without_leading_zero_is_a_numeric_identifier():
    zips = pd.Series([str(10000 + i) for i in range(N)])  # all 5-digit, no leading zeros
    profile = profile_column(zips, "zip")
    assert profile.inferred_type == "numeric_identifier"
    assert profile.identifier_reason == "fixed_width_code"


def test_ten_digit_phone_number_is_a_numeric_identifier():
    phones = pd.Series([f"55501{i:05d}" for i in range(N)])  # constant 10-digit width
    profile = profile_column(phones, "phone")
    assert profile.inferred_type == "numeric_identifier"
    assert profile.identifier_reason == "fixed_width_code"


def test_near_unique_integer_column_is_a_numeric_identifier_high_uniqueness():
    ids = pd.Series([str(100000 + i) for i in range(N)])  # varying width, all unique
    profile = profile_column(ids, "row_id")
    assert profile.inferred_type == "numeric_identifier"
    assert profile.identifier_reason == "high_uniqueness"


def test_a_real_quantity_is_never_misclassified_as_identifier():
    # Regression guard: whole-dollar revenue values must stay numeric_quantity,
    # not get swept up by the fixed-width/uniqueness identifier heuristics.
    revenue = pd.Series([str(round(50 + i * 137.3, 2)) for i in range(N)])
    profile = profile_column(revenue, "revenue")
    assert profile.inferred_type == "numeric_quantity"
    assert profile.identifier_reason is None


# ---------------------------------------------------------------------------
# Degenerate cases
# ---------------------------------------------------------------------------

def test_constant_column_is_reported_not_crashed_on():
    df = pd.DataFrame({"flag": ["yes"] * 15})
    profile = profile_column(df["flag"], "flag")
    assert profile.cardinality == 1
    assert any("constant value" in w for w in profile.warnings)


def test_all_null_column_is_reported_as_empty():
    df = pd.DataFrame({"blank": pd.Series([None] * 20, dtype=object)})
    dataset_profile = profile_dataframe(df)
    assert dataset_profile.columns["blank"].inferred_type == "empty"


def test_single_row_file_does_not_crash():
    df = pd.DataFrame({"a": ["1"], "b": ["hello"], "c": ["2026-01-01"]})
    dataset_profile = profile_dataframe(df)
    assert dataset_profile.row_count == 1
    assert dataset_profile.columns["a"].inferred_type in ("numeric_quantity", "numeric_identifier")
    assert dataset_profile.columns["c"].inferred_type == "temporal"


# ---------------------------------------------------------------------------
# load_any: delimiter / encoding / decimal-locale auto-detection
# ---------------------------------------------------------------------------

def test_load_any_reads_comma_delimited_csv():
    buf = io.BytesIO(b"a,b\n1,2\n3,4\n")
    df, meta = load_any(buf, "plain.csv")
    assert list(df.columns) == ["a", "b"]
    assert meta["delimiter"] == ","
    assert meta["decimal"] == "."


def test_load_any_detects_semicolon_delimiter_and_comma_decimal():
    # Standard European-locale Excel export shape.
    buf = io.BytesIO("a;b\n1,5;2,25\n3,0;4,75\n".encode("utf-8"))
    df, meta = load_any(buf, "european.csv")
    assert list(df.columns) == ["a", "b"]
    assert meta["delimiter"] == ";"
    assert meta["decimal"] == ","
    # values come back as raw, untouched text -- L1's job to parse "1,5"
    assert df["a"].tolist() == ["1,5", "3,0"]


def test_load_any_falls_back_to_comma_when_nothing_to_sniff():
    buf = io.BytesIO(b"single_column\nvalue1\nvalue2\n")
    df, meta = load_any(buf, "one_col.csv")
    assert list(df.columns) == ["single_column"]
    assert meta["delimiter"] == ","


def test_load_any_handles_non_utf8_encoding():
    text = "name,city\nJosé,São Paulo\n"
    buf = io.BytesIO(text.encode("cp1252"))
    df, meta = load_any(buf, "latin.csv")
    assert meta["encoding"] in ("cp1252", "latin-1")
    assert "os" in df["name"].iloc[0]  # decoded without raising, name is legible


def test_load_any_preserves_leading_zeros_no_dtype_inference():
    buf = io.BytesIO(b"zip\n00501\n10001\n")
    df, meta = load_any(buf, "zips.csv")
    assert df["zip"].tolist() == ["00501", "10001"]  # not coerced to int, zero not lost


def test_load_any_reads_excel():
    df_in = _mixed_dataset()
    buf = io.BytesIO()
    df_in.to_excel(buf, index=False)
    buf.seek(0)
    loaded, meta = load_any(buf, "arbitrary_export.xlsx")
    assert list(loaded.columns) == list(df_in.columns)
    assert len(loaded) == len(df_in)
    assert meta["format"] == "excel"


def test_load_any_has_no_column_alias_renaming():
    # Unlike parsers.py, whatever header the file has is preserved verbatim.
    buf = io.BytesIO(b"Weird Header !!,another one\n1,2\n3,4\n")
    df, _ = load_any(buf, "weird.csv")
    assert list(df.columns) == ["Weird Header !!", "another one"]


def test_profile_dataframe_carries_load_meta_through():
    buf = io.BytesIO(b"a,b\n1,2\n")
    df, meta = load_any(buf, "plain.csv")
    dataset_profile = profile_dataframe(df, load_meta=meta)
    assert dataset_profile.load_meta == meta
