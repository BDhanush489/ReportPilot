"""
A GLOBAL daily cap on Claude narrative calls, shared across every tenant --
not a per-tenant limit. A per-tenant cap resets the instant someone signs up
with a fresh Google account, so it can't bound total Anthropic API spend the
way a shared counter can; this exists specifically to protect that budget
against exactly that pattern (many free Gmail accounts each getting their
own "fresh" allowance).

Opens/commits its own short-lived session, same pattern as report_store.py/
data_context.py/scheduler.py/etc (see their module docstrings) --
report_builder.py has no request-scoped session to draw from when called
from main.py's background report-generation thread.

Ollama is NOT gated by this: a local model costs nothing to run, so there's
no budget to protect there. Only the Claude path checks this cap (see
agent.py's `claude_allowed` parameter).
"""
from __future__ import annotations

import datetime
import os

from . import db as db_mod
from .store_models import AiUsageCounter


def _today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def daily_limit() -> int:
    """AI_NARRATIVE_DAILY_LIMIT env var, default 8. <= 0 disables Claude
    entirely (Ollama/the deterministic template still work normally)."""
    return int(os.environ.get("AI_NARRATIVE_DAILY_LIMIT", "8"))


def try_consume() -> bool:
    """True (and increments today's counter) if the global cap isn't yet
    hit; False (no increment) once it is."""
    limit = daily_limit()
    if limit <= 0:
        return False

    today = _today()
    with db_mod.SessionLocal() as session:
        row = session.get(AiUsageCounter, today)
        if row is None:
            row = AiUsageCounter(date=today, count=0)
            session.add(row)
        if row.count >= limit:
            return False
        row.count += 1
        session.commit()
        return True
