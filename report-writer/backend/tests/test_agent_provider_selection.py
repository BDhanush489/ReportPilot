"""Tests for agent.generate_report()'s provider-selection chain, specifically
the claude_allowed gate added for the global daily usage cap (app/ai_usage.py).

generate_report() stays DB-free on purpose (see its own docstring) -- the
cap is resolved by the CALLER (report_builder.py) before this function ever
runs, so these tests monkeypatch the provider functions directly rather than
needing a db_session fixture."""
from app import agent

VALID_SECTIONS = ["Web Analytics"]
METRICS = {"web_analytics": {"sessions": 100}}
BRANDING = {"agency_name": "Acme", "client_name": "Client"}


def _canned_report(**overrides) -> dict:
    base = {
        "report_title": "t", "period_label": "p", "executive_summary": "s",
        "highlights": [], "watchouts": [],
        "sections": [{"heading": "Web Analytics", "narrative": "n", "recommendations": []}],
        "next_steps": [],
    }
    base.update(overrides)
    return base


def test_claude_skipped_and_limit_flag_set_when_claude_allowed_is_false(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(agent, "_ollama_available", lambda: False)

    def _boom(*a, **kw):
        raise AssertionError("Claude must not be called when claude_allowed=False")
    monkeypatch.setattr(agent, "_generate_via_anthropic", _boom)

    report = agent.generate_report(METRICS, BRANDING, VALID_SECTIONS, claude_allowed=False)

    assert not report.get("_ai_generated")
    assert report["_ai_limit_reached"] is True


def test_claude_still_attempted_when_claude_allowed_is_true(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(
        agent, "_generate_via_anthropic",
        lambda *a, **kw: _canned_report(_ai_generated=True, _ai_provider="Claude Haiku 4.5"),
    )

    report = agent.generate_report(METRICS, BRANDING, VALID_SECTIONS, claude_allowed=True)

    assert report["_ai_generated"] is True
    assert report["_ai_provider"] == "Claude Haiku 4.5"


def test_claude_allowed_defaults_to_true_so_existing_callers_are_unaffected(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(
        agent, "_generate_via_anthropic",
        lambda *a, **kw: _canned_report(_ai_generated=True, _ai_provider="Claude Haiku 4.5"),
    )

    report = agent.generate_report(METRICS, BRANDING, VALID_SECTIONS)  # no claude_allowed passed

    assert report["_ai_generated"] is True


def test_ollama_is_not_gated_by_claude_allowed(monkeypatch):
    """The cap protects Anthropic spend specifically -- a free local model
    has no budget to protect, so it must still be tried even when the
    global Claude cap is exhausted."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(agent, "_ollama_available", lambda: True)
    monkeypatch.setattr(
        agent, "_generate_via_ollama",
        lambda *a, **kw: _canned_report(_ai_generated=True, _ai_provider="local Ollama (llama3.2:3b)"),
    )

    report = agent.generate_report(METRICS, BRANDING, VALID_SECTIONS, claude_allowed=False)

    assert report["_ai_generated"] is True
    assert report["_ai_provider"] == "local Ollama (llama3.2:3b)"


def test_no_api_key_at_all_is_unaffected_by_claude_allowed(monkeypatch):
    """Matches pre-existing behavior: with no key configured, the value of
    claude_allowed is moot (agent.py's `if api_key` gate wins first)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(agent, "_ollama_available", lambda: False)

    report = agent.generate_report(METRICS, BRANDING, VALID_SECTIONS, claude_allowed=True)

    assert not report.get("_ai_generated")
    assert not report.get("_ai_limit_reached")
