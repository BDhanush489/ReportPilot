"""Tests for app/ai_usage.py's global daily cap on Claude narrative calls.

Deliberately NOT tenant-scoped -- see the module's own docstring for why a
per-tenant cap can't protect Anthropic API spend the way this one does
(a per-tenant limit resets on every fresh signup)."""
from app import ai_usage


def test_try_consume_allows_up_to_the_limit_then_blocks(db_session, monkeypatch):
    monkeypatch.setenv("AI_NARRATIVE_DAILY_LIMIT", "2")

    assert ai_usage.try_consume() is True
    assert ai_usage.try_consume() is True
    assert ai_usage.try_consume() is False  # 3rd call, over the cap of 2
    assert ai_usage.try_consume() is False  # stays blocked, doesn't re-increment


def test_limit_of_zero_disables_claude_entirely_without_touching_the_db(db_session, monkeypatch):
    monkeypatch.setenv("AI_NARRATIVE_DAILY_LIMIT", "0")
    assert ai_usage.try_consume() is False


def test_negative_limit_also_disables_claude_entirely(db_session, monkeypatch):
    monkeypatch.setenv("AI_NARRATIVE_DAILY_LIMIT", "-1")
    assert ai_usage.try_consume() is False


def test_daily_limit_defaults_to_eight_when_unset(monkeypatch):
    monkeypatch.delenv("AI_NARRATIVE_DAILY_LIMIT", raising=False)
    assert ai_usage.daily_limit() == 8


def test_different_days_get_independent_counters(db_session, monkeypatch):
    monkeypatch.setenv("AI_NARRATIVE_DAILY_LIMIT", "1")

    monkeypatch.setattr(ai_usage, "_today", lambda: "2026-01-01")
    assert ai_usage.try_consume() is True
    assert ai_usage.try_consume() is False  # day 1's single slot is used

    monkeypatch.setattr(ai_usage, "_today", lambda: "2026-01-02")
    assert ai_usage.try_consume() is True  # a new day, a fresh slot
