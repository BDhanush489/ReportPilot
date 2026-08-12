"""Tests for app/period_diff.py (Track B1 — period-over-period diff)."""
from app.period_diff import (
    DimensionChange,
    MetricDelta,
    describe_delta,
    describe_dimension_change,
    diff_dimension,
    diff_totals,
)

# Two known consecutive periods -- exact numbers, asserted exactly.
CURRENT_TOTALS = {"sessions": 1200, "revenue_usd": 6000.0, "conversion_rate": 4.0}
PRIOR_TOTALS = {"sessions": 1000, "revenue_usd": 5000.0, "conversion_rate": 5.0}

CURRENT_BY_CHANNEL = [
    {"channel": "Organic Search", "revenue_usd": 3000.0, "sessions": 600},
    {"channel": "Paid Search", "revenue_usd": 2000.0, "sessions": 400},
    {"channel": "Email", "revenue_usd": 1000.0, "sessions": 200},  # new this period
]
PRIOR_BY_CHANNEL = [
    {"channel": "Organic Search", "revenue_usd": 2500.0, "sessions": 550},
    {"channel": "Paid Search", "revenue_usd": 2500.0, "sessions": 450},
    {"channel": "Referral", "revenue_usd": 0.0, "sessions": 10},  # dropped this period
]


# ---------------------------------------------------------------------------
# diff_totals: exact deltas against a known fixture
# ---------------------------------------------------------------------------

def test_diff_totals_computes_exact_abs_and_pct_deltas():
    deltas = diff_totals(CURRENT_TOTALS, PRIOR_TOTALS)

    assert deltas["sessions"] == MetricDelta(field="sessions", current=1200, prior=1000, abs_delta=200, pct_delta=20.0)
    assert deltas["revenue_usd"] == MetricDelta(field="revenue_usd", current=6000.0, prior=5000.0, abs_delta=1000.0, pct_delta=20.0)
    # conversion_rate DECLINED 5.0 -> 4.0 -- exact negative delta, not abs()'d away.
    assert deltas["conversion_rate"] == MetricDelta(field="conversion_rate", current=4.0, prior=5.0, abs_delta=-1.0, pct_delta=-20.0)


def test_diff_totals_pct_delta_is_none_when_prior_is_zero():
    deltas = diff_totals({"x": 50}, {"x": 0})
    assert deltas["x"].abs_delta == 50
    assert deltas["x"].pct_delta is None


def test_diff_totals_skips_fields_not_present_in_both_by_default():
    deltas = diff_totals({"a": 1, "b_only_current": 2}, {"a": 1, "c_only_prior": 3})
    assert set(deltas.keys()) == {"a"}


def test_diff_totals_respects_explicit_field_list():
    deltas = diff_totals(CURRENT_TOTALS, PRIOR_TOTALS, fields=["sessions"])
    assert set(deltas.keys()) == {"sessions"}


def test_diff_totals_ignores_non_numeric_values_without_crashing():
    deltas = diff_totals({"a": 1, "label": "text"}, {"a": 2, "label": "text"})
    assert set(deltas.keys()) == {"a"}


# ---------------------------------------------------------------------------
# diff_dimension: new/dropped detection against a known fixture
# ---------------------------------------------------------------------------

def test_diff_dimension_classifies_continuing_new_and_dropped_correctly():
    changes = diff_dimension(CURRENT_BY_CHANNEL, PRIOR_BY_CHANNEL, "channel", ["revenue_usd", "sessions"])
    by_key = {c.key: c for c in changes}

    assert by_key["Organic Search"].status == "continuing"
    assert by_key["Paid Search"].status == "continuing"
    assert by_key["Email"].status == "new"
    assert by_key["Referral"].status == "dropped"
    assert set(by_key.keys()) == {"Organic Search", "Paid Search", "Email", "Referral"}


def test_diff_dimension_continuing_key_carries_exact_deltas():
    changes = diff_dimension(CURRENT_BY_CHANNEL, PRIOR_BY_CHANNEL, "channel", ["revenue_usd"])
    organic = next(c for c in changes if c.key == "Organic Search")
    assert organic.deltas["revenue_usd"] == MetricDelta(
        field="revenue_usd", current=3000.0, prior=2500.0, abs_delta=500.0, pct_delta=20.0
    )


def test_diff_dimension_new_key_carries_current_values_not_deltas():
    changes = diff_dimension(CURRENT_BY_CHANNEL, PRIOR_BY_CHANNEL, "channel", ["revenue_usd", "sessions"])
    email = next(c for c in changes if c.key == "Email")
    assert email.deltas == {}
    assert email.current_values == {"revenue_usd": 1000.0, "sessions": 200}
    assert email.prior_values is None


def test_diff_dimension_dropped_key_carries_prior_values_not_deltas():
    changes = diff_dimension(CURRENT_BY_CHANNEL, PRIOR_BY_CHANNEL, "channel", ["revenue_usd", "sessions"])
    referral = next(c for c in changes if c.key == "Referral")
    assert referral.deltas == {}
    assert referral.prior_values == {"revenue_usd": 0.0, "sessions": 10}
    assert referral.current_values is None


def test_diff_dimension_no_changes_when_periods_are_identical():
    changes = diff_dimension(CURRENT_BY_CHANNEL, CURRENT_BY_CHANNEL, "channel", ["revenue_usd"])
    assert all(c.status == "continuing" for c in changes)
    assert all(d.abs_delta == 0 for c in changes for d in c.deltas.values())


# ---------------------------------------------------------------------------
# describe_delta / describe_dimension_change: deterministic, built only from
# the diff's own numbers -- the "why" narrative's raw material.
# ---------------------------------------------------------------------------

def test_describe_delta_states_growth_with_exact_numbers():
    delta = MetricDelta(field="revenue_usd", current=6000.0, prior=5000.0, abs_delta=1000.0, pct_delta=20.0)
    sentence = describe_delta("Revenue", delta)
    assert "grew" in sentence
    assert "20" in sentence
    assert "5000" in sentence and "6000" in sentence


def test_describe_delta_states_decline_not_growth():
    delta = MetricDelta(field="conversion_rate", current=4.0, prior=5.0, abs_delta=-1.0, pct_delta=-20.0)
    sentence = describe_delta("Conversion rate", delta)
    assert "declined" in sentence
    assert "grew" not in sentence


def test_describe_delta_handles_undefined_pct_change_from_zero_prior():
    delta = MetricDelta(field="x", current=50, prior=0, abs_delta=50, pct_delta=None)
    sentence = describe_delta("X", delta)
    assert "%" not in sentence  # no fabricated percentage when one isn't defined
    assert "0" in sentence and "50" in sentence


def test_describe_dimension_change_new_and_dropped_are_labeled_distinctly():
    new_change = DimensionChange(key="Email", status="new", current_values={"revenue_usd": 1000.0})
    dropped_change = DimensionChange(key="Referral", status="dropped", prior_values={"revenue_usd": 0.0})

    new_sentence = describe_dimension_change("channel", new_change)
    dropped_sentence = describe_dimension_change("channel", dropped_change)

    assert "New" in new_sentence and "Email" in new_sentence
    assert "no longer present" in dropped_sentence and "Referral" in dropped_sentence


def test_every_word_in_a_describe_sentence_traces_to_the_diff_it_was_given():
    """The 'why' narrative cites only diff metrics: build a sentence from
    one delta, confirm the only numbers in it are that delta's own current/
    prior/pct values -- nothing injected from outside the diff."""
    delta = MetricDelta(field="revenue_usd", current=6000.0, prior=5000.0, abs_delta=1000.0, pct_delta=20.0)
    sentence = describe_delta("Revenue", delta)
    import re
    numbers_in_sentence = {float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", sentence)}
    assert numbers_in_sentence <= {delta.current, delta.prior, abs(delta.pct_delta)}
