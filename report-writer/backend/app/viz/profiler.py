"""
Schema-agnostic ingest + column profiling.

Deliberately does not share code with app/parsers.py: parsers.py exists to
map a client's real-world column names onto this app's fixed canonical
schema (date, sessions, revenue_usd, ...) via a known alias table. This
module assumes nothing about what columns exist or what they're called —
it has to work on a file it has never seen the shape of. Column typing is
purely statistical (parse success rate, cardinality, value shape); it never
keys off a column *name* like "id" or "date", because a name-based shortcut
would silently stop being schema-agnostic the first time it guessed wrong.

Everything is read as raw text first (see load_any) — no formatting evidence
(a zip code's leading zero, a currency symbol) is lost to pandas' automatic
dtype inference before this module or normalize.py (L1) ever sees it. Type
inference and value parsing both happen explicitly, downstream, on purpose.
"""
from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass, field
from typing import BinaryIO

import pandas as pd

#: A column parses as temporal if at least this fraction of its non-null
#: values successfully parse as a date.
TEMPORAL_PARSE_THRESHOLD = 0.9

#: A column is a numeric_identifier (by the high-uniqueness route) if this
#: fraction or more of its non-null values are unique. High but not 1.0 —
#: real id columns occasionally have a handful of legitimate duplicates
#: (retries, corrections) without stopping being an id.
ID_UNIQUENESS_THRESHOLD = 0.98

#: Below this many non-null rows, a high uniqueness ratio isn't meaningful
#: evidence of "this is an identifier" — on a small sample, an ordinary
#: continuous measure (a handful of distinct whole-dollar revenue values) is
#: *expected* to be ~100% unique purely by chance. Caught live in an earlier
#: build: a 6-row revenue column of whole-dollar amounts was misclassified.
MIN_ROWS_FOR_ID_INFERENCE = 20

#: A numeric column is treated as a "year" identifier (never summed/averaged)
#: when every value is a whole number in this range.
YEAR_RANGE = (1900, 2100)

#: Constant string widths this narrow, name-free heuristic treats as
#: identifier-shaped: 5 = US ZIP, 10 = a bare 10-digit phone number. Narrow
#: and explicit on purpose (matches this project's existing practice, e.g.
#: qa.py's bare-year exclusion) rather than a broad, error-prone guess at
#: every locale's id formats.
FIXED_WIDTH_ID_LENGTHS = (5, 10)

#: An object-dtype categorical column needs at least this much of its
#: values to be distinct before free-text is even considered — paired with
#: the prose-shape check below. Cardinality ratio *alone* is unreliable at
#: small N: a 3-category column ("Organic"/"Paid"/"Email") sampled across 6
#: rows can trivially hit a ratio of 0.5, nowhere near what actually
#: distinguishes real free text from an ordinary low-cardinality category.
FREE_TEXT_CARDINALITY_RATIO_FLOOR = 0.3

#: A column is "mixed" type if between (this) and (1 - this) fraction of its
#: non-null values parse as numeric — genuinely straddling numeric/text,
#: not just "mostly one, a few stray typos."
MIXED_TYPE_BAND = (0.15, 0.85)

_ENCODING_CANDIDATES = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
_DELIMITER_CANDIDATES = ",;\t|"


# ---------------------------------------------------------------------------
# Ingest — raw text in, nothing silently coerced.
# ---------------------------------------------------------------------------

def _decode_bytes(raw: bytes) -> tuple[str, str]:
    """Tries a small set of encodings, most-specific first. latin-1 never
    raises (it maps every byte 0-255 to a codepoint), so this always
    terminates — the point of the chain is picking a *correct* decode
    before falling back to one that's merely always possible."""
    for enc in _ENCODING_CANDIDATES:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1"), "latin-1"  # unreachable: latin-1 above always succeeds


def _sniff_delimiter(sample_text: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample_text[:8192], delimiters=_DELIMITER_CANDIDATES)
        return dialect.delimiter
    except csv.Error:
        return ","  # single-column files / short samples have nothing to sniff


def load_any(file: BinaryIO, filename: str) -> tuple[pd.DataFrame, dict]:
    """Loads a CSV or Excel file with zero schema assumptions — no column
    renaming, no alias table, no dropped/defaulted columns, and (CSV) no
    silent dtype inference: every cell comes back as its original text.

    Returns (df, load_meta). load_meta documents what was auto-detected
    (encoding, delimiter, decimal convention) so a caller — or a later
    normalization step — knows exactly what was assumed, not guessed
    silently. decimal is *reported* here, not applied: pandas' `decimal=`
    read_csv argument only affects its own numeric parsing, which never
    happens here since every column is forced to string — actually
    interpreting "1.234,56"-style numbers is normalize.py's (L1) job."""
    lower = filename.lower()
    if lower.endswith((".xlsx", ".xls")):
        df = pd.read_excel(file, dtype=str)
        return df, {"format": "excel", "encoding": None, "delimiter": None, "decimal": None}

    raw = file.read()
    text, encoding = _decode_bytes(raw)
    delimiter = _sniff_delimiter(text)
    # Semicolon-delimited CSVs are the standard European-locale Excel export
    # shape *specifically because* comma is reserved as the decimal
    # separator there — a stated, documented correlation, not a guess.
    decimal = "," if delimiter == ";" else "."
    df = pd.read_csv(io.StringIO(text), sep=delimiter, dtype=str, keep_default_na=True)
    return df, {"format": "csv", "encoding": encoding, "delimiter": delimiter, "decimal": decimal}


# ---------------------------------------------------------------------------
# Column typing — numeric_quantity / numeric_identifier / categorical /
# temporal / free_text, plus the degenerate states mixed and empty.
# ---------------------------------------------------------------------------

def _numeric_parse_rate(non_null: pd.Series) -> float:
    if non_null.empty:
        return 0.0
    parsed = pd.to_numeric(non_null, errors="coerce")
    return float(parsed.notna().mean())


def _temporal_parse_rate(non_null: pd.Series) -> float:
    if non_null.empty:
        return 0.0
    parsed = pd.to_datetime(non_null, errors="coerce", format="mixed")
    return float(parsed.notna().mean())


def _is_integer_valued(numeric_series: pd.Series) -> bool:
    if numeric_series.empty:
        return False
    return bool(((numeric_series.astype(float) % 1) == 0).all())


def _looks_like_id_token(non_null: pd.Series) -> bool:
    """IDs/codes are short, low-entropy tokens ("W0004", a UUID, "promo-12").
    Free text is long-form. A uniqueness ratio alone can't tell these apart —
    a free-text comment column is *also* ~100% unique — so this checks shape."""
    as_str = non_null.astype(str).str.strip()
    mean_words = as_str.str.split().str.len().mean()
    mean_len = as_str.str.len().mean()
    return bool(mean_words <= 3 and mean_len <= 24)


def _looks_like_prose(non_null: pd.Series) -> bool:
    """The free-text counterpart of _looks_like_id_token."""
    as_str = non_null.astype(str).str.strip()
    mean_words = as_str.str.split().str.len().mean()
    mean_len = as_str.str.len().mean()
    return bool(mean_words > 3 or mean_len > 24)


def _numeric_identifier_reason(raw_non_null: pd.Series, parsed: pd.Series) -> str | None:
    """Returns why a numeric-parseable column is an identifier (never a
    quantity to sum/average), or None if it looks like a real quantity.
    Checked in order from most to least certain:

      1. year — every value a whole number in YEAR_RANGE.
      2. leading_zero_formatting — the raw text has a meaningless leading
         zero on at least one value (e.g. "0501") — a real quantity's
         canonical text form never does this; airtight evidence.
      3. fixed_width_code — every raw value is all-digit and exactly the
         same length, at a classic id width (5 = ZIP, 10 = phone), with
         enough rows that "coincidentally always the same width" is
         implausible for a genuine, varying quantity.
      4. high_uniqueness — near-unique integer-valued column (a primary
         key / row id), the original id-detection route.
    """
    if parsed.empty:
        return None

    if _is_integer_valued(parsed) and parsed.min() >= YEAR_RANGE[0] and parsed.max() <= YEAR_RANGE[1]:
        return "year"

    raw_str = raw_non_null.astype(str).str.strip()
    digit_only = raw_str[raw_str.str.match(r"^\d+$")]
    if not digit_only.empty:
        has_meaningless_leading_zero = digit_only.str.match(r"^0\d+$").any()
        if has_meaningless_leading_zero:
            return "leading_zero_formatting"

        widths = digit_only.str.len().unique()
        if (len(widths) == 1 and widths[0] in FIXED_WIDTH_ID_LENGTHS
                and len(digit_only) == len(raw_str) and len(raw_str) >= MIN_ROWS_FOR_ID_INFERENCE):
            return "fixed_width_code"

    ratio = parsed.nunique() / len(parsed)
    if _is_integer_valued(parsed) and ratio >= ID_UNIQUENESS_THRESHOLD and len(parsed) >= MIN_ROWS_FOR_ID_INFERENCE:
        return "high_uniqueness"

    return None


def _infer_type(raw: pd.Series) -> tuple[str, list[str], str | None]:
    """raw is always string-dtype (see load_any) — there is no more native-
    pandas-dtype branch to special-case; every column is typed the same way,
    by parse success rate and value shape. Returns (type, warnings, id_reason)."""
    warnings: list[str] = []
    non_null = raw.dropna()
    non_null = non_null[non_null.astype(str).str.strip() != ""]
    if non_null.empty:
        return "empty", ["column has no non-null values"], None

    if non_null.nunique() == 1:
        warnings.append(f"column has a single constant value across {len(non_null)} row(s)")

    numeric_rate = _numeric_parse_rate(non_null)
    temporal_rate = _temporal_parse_rate(non_null)

    if MIXED_TYPE_BAND[0] <= numeric_rate <= MIXED_TYPE_BAND[1]:
        warnings.append(f"{numeric_rate:.0%} of values parse as numeric, the rest don't — mixed-type column")
        return "mixed", warnings, None

    if numeric_rate > MIXED_TYPE_BAND[1]:
        parsed = pd.to_numeric(non_null, errors="coerce").dropna()
        id_reason = _numeric_identifier_reason(non_null, parsed)
        if id_reason:
            return "numeric_identifier", warnings, id_reason
        return "numeric_quantity", warnings, None

    if temporal_rate >= TEMPORAL_PARSE_THRESHOLD:
        return "temporal", warnings, None

    ratio = non_null.nunique() / len(non_null)
    if (ratio >= ID_UNIQUENESS_THRESHOLD and len(non_null) >= MIN_ROWS_FOR_ID_INFERENCE
            and _looks_like_id_token(non_null)):
        return "categorical", warnings, None  # short-token id-like text, e.g. "W0004" -- not numeric, stays categorical

    return "categorical", warnings, None


def _profile_numeric(non_null_raw: pd.Series) -> dict:
    parsed = pd.to_numeric(non_null_raw, errors="coerce").dropna()
    if parsed.empty:
        return {}
    q1, median, q3 = parsed.quantile([0.25, 0.5, 0.75])
    return {
        "min": float(parsed.min()), "max": float(parsed.max()),
        "mean": float(parsed.mean()), "std": float(parsed.std()) if len(parsed) > 1 else 0.0,
        "q1": float(q1), "median": float(median), "q3": float(q3),
    }


def _profile_categorical(non_null_raw: pd.Series, top_n: int = 10) -> dict:
    counts = non_null_raw.value_counts().head(top_n)
    return {"top_values": [{"value": str(k), "count": int(v)} for k, v in counts.items()]}


def _profile_temporal(non_null_raw: pd.Series) -> dict:
    parsed = pd.to_datetime(non_null_raw, errors="coerce", format="mixed").dropna()
    if parsed.empty:
        return {}
    return {"min": parsed.min().isoformat(), "max": parsed.max().isoformat()}


@dataclass
class ColumnProfile:
    name: str
    inferred_type: str  # numeric_quantity | numeric_identifier | categorical | temporal | free_text | mixed | empty
    identifier_reason: str | None  # year | leading_zero_formatting | fixed_width_code | high_uniqueness | None
    count: int
    null_count: int
    null_pct: float
    cardinality: int
    is_free_text: bool = False
    numeric: dict | None = None
    categorical: dict | None = None
    temporal: dict | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def profile_column(raw: pd.Series, name: str) -> ColumnProfile:
    """raw must be string-dtype (or at least string-like) — pass the column
    exactly as load_any returned it, before any numeric/date coercion."""
    count = int(raw.notna().sum())
    null_count = int(raw.isna().sum())
    total = count + null_count

    inferred_type, warnings, id_reason = _infer_type(raw)
    non_null = raw.dropna()
    non_null = non_null[non_null.astype(str).str.strip() != ""]
    cardinality = int(non_null.nunique())

    is_free_text = False
    numeric = categorical = temporal = None

    if inferred_type == "numeric_quantity":
        numeric = _profile_numeric(non_null)
    elif inferred_type == "numeric_identifier":
        numeric = _profile_numeric(non_null)  # min/max/etc. still meaningful to *see*, just never to sum/average
    elif inferred_type == "temporal":
        temporal = _profile_temporal(non_null)
    elif inferred_type in ("categorical", "mixed"):
        categorical = _profile_categorical(non_null)
        if (inferred_type == "categorical"
                and count > 0 and (cardinality / count) >= FREE_TEXT_CARDINALITY_RATIO_FLOOR
                and _looks_like_prose(non_null)):
            is_free_text = True
            inferred_type = "free_text"
            warnings.append("free text column (prose-like content) — not a usable grouping key")

    return ColumnProfile(
        name=name, inferred_type=inferred_type, identifier_reason=id_reason,
        count=count, null_count=null_count,
        null_pct=round(null_count / total * 100, 2) if total else 0.0,
        cardinality=cardinality, is_free_text=is_free_text,
        numeric=numeric, categorical=categorical, temporal=temporal, warnings=warnings,
    )


@dataclass
class DatasetProfile:
    row_count: int
    duplicate_row_count: int
    columns: dict[str, ColumnProfile]
    load_meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "row_count": self.row_count,
            "duplicate_row_count": self.duplicate_row_count,
            "load_meta": self.load_meta,
            "columns": {name: c.to_dict() for name, c in self.columns.items()},
        }


def profile_dataframe(df: pd.DataFrame, load_meta: dict | None = None) -> DatasetProfile:
    """Never raises on a column's content, however messy — a column that
    can't be sensibly typed comes back as "mixed" or "empty" with warnings
    attached, not an exception. df itself is never modified."""
    columns = {col: profile_column(df[col], col) for col in df.columns}
    duplicate_row_count = int(df.duplicated().sum()) if len(df.columns) else 0
    return DatasetProfile(
        row_count=len(df), duplicate_row_count=duplicate_row_count,
        columns=columns, load_meta=load_meta or {},
    )
