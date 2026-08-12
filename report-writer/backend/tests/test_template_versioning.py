"""
Tests for T3 — template versioning. GOAL: template drift must not break
scheduled-report reproducibility.

Exit criteria proven here:
  - ReportObject records template id + version
    (test_a_generated_report_records_the_resolved_template_id_and_version).
  - Regeneration pins to the recorded version, not latest
    (test_bumping_a_template_does_not_change_a_pinned_regeneration,
    test_regenerate_run_pins_to_the_old_reports_recorded_version).
  - Bump a template, re-run a past report, output matches the original
    (same two tests -- chart list is asserted identical, not just the
    version number).
"""
import sqlite3
from datetime import date

import pytest

from app import data_context, delivery, report_builder, report_store, scheduler, template_specs

BRANDING = {"agency_name": "Test Agency", "client_name": "Aurora Home Goods",
            "primary_color": "#2a78d6", "accent_color": "#eda100"}

#: Track E1 -- the checked-in "aurora-home-goods" SQLite data context fixture
#: lives under this fixed tenant_id (data_contexts/demo-tenant/aurora-home-goods.json).
TENANT = "demo-tenant"


@pytest.fixture(autouse=True)
def _force_deterministic_narrative(monkeypatch):
    """This environment has a reachable local Ollama server; force the
    deterministic fallback so these tests are fast and don't depend on live
    model output neither this file's assertions nor T3's mechanism cares
    about (see test_template_specs.py's identical fixture/rationale).

    Also resets template_specs._CACHE before AND after every test: it's
    process-global module state, and _bump_default_template() repopulates it
    from a monkeypatched _SPEC_DIR that reverts at test end -- without this,
    a stale cached spec object from one test's bump silently leaks into the
    next test's load_template() call, even after _SPEC_DIR is back to normal."""
    template_specs.clear_cache()
    monkeypatch.setattr("app.agent._ollama_available", lambda: False)
    yield
    template_specs.clear_cache()


# ---------------------------------------------------------------------------
# template_specs.py: version resolution
# ---------------------------------------------------------------------------

def test_load_template_with_no_version_resolves_latest():
    spec = template_specs.load_template("default")
    assert spec.version == 1


def test_load_template_with_explicit_version_pins_exactly():
    spec = template_specs.load_template("default", version=1)
    assert spec.id == "default"
    assert spec.version == 1


def test_load_template_with_unknown_version_raises():
    with pytest.raises(ValueError, match="version=99"):
        template_specs.load_template("default", version=99)


def _bump_default_template(tmp_path, monkeypatch) -> None:
    """Simulates "the default template got bumped, right now": a real v2
    file on disk with a genuinely SMALLER chart list than v1, so a test can
    tell which version actually rendered by counting/naming the charts it
    got, not by trusting a version number alone. A plain function (not a
    fixture) so tests control exactly WHEN the bump happens relative to
    other calls -- some tests need a report generated under v1 BEFORE the
    bump exists."""
    spec_dir = tmp_path / "template_specs"
    if not spec_dir.exists():
        spec_dir.mkdir()
        for existing in template_specs._SPEC_DIR.glob("*.v*.json"):
            (spec_dir / existing.name).write_text(existing.read_text(encoding="utf-8"), encoding="utf-8")
        monkeypatch.setattr(template_specs, "_SPEC_DIR", spec_dir)

    v2 = spec_dir / "default.v2.json"
    v2.write_text(
        """{
  "id": "default",
  "version": 2,
  "tone": "manager",
  "sections": [
    {"key": "analytics", "label": "Web Analytics", "charts": [
      {"caption": "Sessions by device", "builder": "device_split_pie_chart",
       "builder_args": ["by_device"], "chart_type": "pie",
       "metric_paths": ["metrics.analytics.by_device"], "shape": "records",
       "x_field": "device_category", "y_field": "sessions"}
    ]}
  ]
}""",
        encoding="utf-8",
    )
    template_specs.clear_cache()


def test_bumping_a_template_changes_what_latest_resolves_to(tmp_path, monkeypatch):
    _bump_default_template(tmp_path, monkeypatch)
    v1 = template_specs.load_template("default", version=1)
    latest = template_specs.load_template("default")
    assert latest.version == 2
    assert len(latest.sections[0].charts) == 1
    assert len(v1.sections[0].charts) == 5  # v1 untouched on disk


# ---------------------------------------------------------------------------
# report_builder.py: the resolved id/version land on the object
# ---------------------------------------------------------------------------

def test_a_generated_report_records_the_resolved_template_id_and_version():
    from tests.conftest import seed_aurora_home_goods_data_context

    seed_aurora_home_goods_data_context()
    result = report_builder.build_report_from_data_context(
        TENANT, "aurora-home-goods", BRANDING, report_id="t3-version-record-test")
    obj = result["report_object"]
    assert obj.template_id == "default"
    assert obj.template_version == 1


def test_bumping_a_template_does_not_change_a_pinned_regeneration(tmp_path, monkeypatch):
    """The direct exit-criterion test: bump the template, then re-run a
    build pinned to the OLD version, and assert the chart structure matches
    what v1 would have produced -- not what "latest" (v2) now produces."""
    from tests.conftest import seed_aurora_home_goods_data_context

    seed_aurora_home_goods_data_context()
    _bump_default_template(tmp_path, monkeypatch)
    pinned = report_builder.build_report_from_data_context(
        TENANT, "aurora-home-goods", BRANDING, report_id="t3-pinned-test",
        template_id="default", template_version=1,
    )["report_object"]
    assert pinned.template_version == 1
    assert {c.caption for c in pinned.charts} >= {"Weekly revenue", "Revenue by channel"}

    unpinned = report_builder.build_report_from_data_context(
        TENANT, "aurora-home-goods", BRANDING, report_id="t3-unpinned-test", template_id="default",
    )["report_object"]
    assert unpinned.template_version == 2
    assert {c.caption for c in unpinned.charts} == {"Sessions by device"}


# ---------------------------------------------------------------------------
# scheduler.py: regenerate_run pins to the OLD report's recorded version
# ---------------------------------------------------------------------------

ANALYTICS_ROWS = [
    ("2026-01-01", "Organic Search", "desktop", 100, 80, 5, 500.0),
    ("2026-01-08", "Paid Search", "mobile", 150, 90, 8, 800.0),
    ("2026-01-15", "Organic Search", "desktop", 200, 130, 12, 1200.0),
]


def _make_sqlite_client(tmp_path, tenant_id: str, client_id: str) -> None:
    db_path = tmp_path / f"{client_id}.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE analytics (date TEXT, channel_group TEXT, device_category TEXT, "
        "sessions INTEGER, new_users INTEGER, conversions INTEGER, revenue_usd REAL)"
    )
    conn.executemany("INSERT INTO analytics VALUES (?, ?, ?, ?, ?, ?, ?)", ANALYTICS_ROWS)
    conn.commit()
    conn.close()
    fields = ["date", "channel_group", "device_category", "sessions", "new_users", "conversions", "revenue_usd"]
    data_context.save_data_context(
        tenant_id, client_id, "sqlite", {"path": str(db_path)},
        {"analytics": {"table": "analytics", "column_map": {f: f for f in fields}}},
    )


@pytest.fixture
def scheduled_client(tmp_path, monkeypatch, db_session):
    cid = "t3-sched-client"
    monkeypatch.setattr("app.agent._ollama_available", lambda: False)  # deterministic + fast
    _make_sqlite_client(tmp_path, TENANT, cid)
    return cid


def test_regenerate_run_pins_to_the_old_reports_recorded_version(scheduled_client, tmp_path, monkeypatch):
    sched = scheduler.Schedule(tenant_id=TENANT, client_id=scheduled_client, data_source_ref=scheduled_client,
                                cadence="weekly", branding=BRANDING)
    as_of = date(2026, 3, 1)
    first = scheduler.run_schedule(sched, as_of, dry_run=False)
    assert first.status == "generated"
    old_report_id = first.report_id
    old_obj = report_store.load_report_object(TENANT, old_report_id)
    assert old_obj.template_version == 1

    # The template gets bumped to v2 AFTER this report was already generated
    # under v1 -- exactly the drift scenario T3 exists to survive.
    _bump_default_template(tmp_path, monkeypatch)

    result = scheduler.regenerate_run(sched, as_of)
    assert result.status == "regenerated"
    assert result.report_id != old_report_id

    new_obj = report_store.load_report_object(TENANT, result.report_id)
    assert new_obj.template_version == 1  # pinned, NOT the bumped "latest" (v2)
    assert {c.caption for c in new_obj.charts} == {c.caption for c in old_obj.charts}

    # Old report_id's files are still on disk (never deleted) -- W2: history retained.
    assert report_store.report_exists(TENANT, old_report_id)
    # schedule.runs now points at the regenerated report for this as_of.
    assert sched.runs[as_of.isoformat()] == result.report_id


def test_regenerate_run_errors_clearly_when_theres_nothing_to_regenerate(scheduled_client):
    sched = scheduler.Schedule(tenant_id=TENANT, client_id=scheduled_client, data_source_ref=scheduled_client,
                                cadence="weekly", branding=BRANDING)
    result = scheduler.regenerate_run(sched, date(2026, 5, 1))
    assert result.status == "error"
    assert "no existing report" in result.detail
