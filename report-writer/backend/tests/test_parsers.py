"""Tests for app/parsers.py's CSV ingestion quirks-handling."""
import io

import pandas as pd
import pytest

from app.parsers import load_web_analytics


def _csv(text: str):
    buf = io.BytesIO(text.encode("utf-8"))
    buf.name = "export.csv"
    return buf


def test_year_and_month_columns_are_synthesized_into_a_date():
    # Real shape reported from a Google Analytics / Alteryx-style export:
    # no single date column, just separate Year + "Month of the year".
    csv_text = (
        "Source / Medium,Year,Month of the year,Sessions\n"
        "A,2019,11,194667\n"
        "A,2020,5,194114\n"
    )
    df, issues = load_web_analytics(_csv(csv_text))
    assert list(df["date"]) == [pd.Timestamp("2019-11-01"), pd.Timestamp("2020-05-01")]
    assert list(df["sessions"]) == [194667, 194114]
    # T2: this fixture genuinely has no revenue column (a real, common shape
    # for a non-ecommerce GA export) -- revenue_usd is business-critical, so
    # that (and only that) is now flagged, not silently zero-filled.
    assert [i["kind"] for i in issues] == ["column_missing"]
    assert issues[0]["column"] == "revenue_usd"


def test_a_real_date_column_is_never_overridden_by_year_month_synthesis():
    csv_text = (
        "date,Year,Month of the year,Sessions\n"
        "2019-11-15,2019,11,100\n"
    )
    df, issues = load_web_analytics(_csv(csv_text))
    assert list(df["date"]) == [pd.Timestamp("2019-11-15")]


def test_year_without_month_still_raises_the_original_clear_error():
    csv_text = "Source / Medium,Year,Sessions\nA,2019,100\n"
    with pytest.raises(ValueError, match="must include a date column"):
        load_web_analytics(_csv(csv_text))
