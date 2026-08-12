#!/usr/bin/env python
"""
Seeds a set of demo tenants/users spanning every plan tier (solo/agency/
in-house) plus a multi-member workspace and a second platform admin, so the
Admin panel (/admin) shows a realistic, varied picture for a demo instead of
just your own single real account.

These are DATA-ONLY seeds -- there is no password auth in this app (only
real Google OAuth), so none of these accounts can actually log in. They
exist purely to populate GET /api/admin/users and /api/admin/tenants with
something worth looking at. If you need to click around AS one of them,
that's a different, larger feature (a dev-only magic-link login) -- ask for
that explicitly if the demo needs it.

Every demo account is tagged with a `demo-` google_sub prefix, so --reset
can find and remove exactly this seed data and nothing else -- it will
never touch your own real account or any other real login.

Usage:
    python scripts/seed_demo_accounts.py                    # seed (skips
                                                              # tenants that
                                                              # already exist)
    python scripts/seed_demo_accounts.py --with-reports      # also generate
                                                              # one REAL report
                                                              # per new tenant
    python scripts/seed_demo_accounts.py --reset             # wipe prior demo
                                                              # seed data first,
                                                              # then reseed clean

--with-reports runs the real pipeline (app/report_builder.py) against the
same Aurora Home Goods sample_data/ fixtures this project's own test suite
uses -- every number in the resulting PDF is genuinely computed, nothing is
invented. Only branding (agency name, colors) varies per workspace; the
underlying business is the same fixture every time, which is honest since
it IS the same underlying dataset regardless of which agency is presenting
it. Reports themselves are stored in the DB now (see app/store_models.py),
same as everything else -- a copy of each PDF is also written locally under
demo_reports/ purely as a convenience so you can open a real file directly
with no login needed (there's no password auth for these demo accounts to
log in with in the first place).
"""
from __future__ import annotations

import argparse
import io
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import auth, data_context, report_builder, report_store, scheduler  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import AuthSession, Membership, Tenant, User  # noqa: E402
from app.store_models import GeneratedReport  # noqa: E402

DEMO_SUB_PREFIX = "demo-"
SAMPLE_DATA_DIR = Path(__file__).resolve().parent.parent / "sample_data"
#: Local convenience copies of --with-reports PDFs -- NOT where reports are
#: actually persisted (that's the generated_reports DB table); just a place
#: to open a real file from without going through the API/UI.
DEMO_REPORTS_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "demo_reports"

# (tenant_name, plan, [(email, name, role, is_platform_admin), ...],
#  active_client_count, (primary_color, accent_color))
# active_client_count is realized as that many fake Schedule files, purely so
# the Admin panel's "active clients / cap" column shows real, plan-relative
# numbers -- including two tenants deliberately sitting AT their plan's cap
# (Bluewave at solo's 5, Vertex at agency's 50) to make the enforcement
# boundary visible, not just the plan label.
DEMO_TENANTS = [
    ("Northlight Growth Partners", "agency",
     [("alex@northlight.demo", "Alex Rivera", "owner", False)], 12, ("#2a78d6", "#eda100")),
    ("Meridian Digital", "agency",
     [("jordan@meridiandigital.demo", "Jordan Lee", "owner", True)], 31, ("#1f9d55", "#f2545b")),
    ("Vertex Analytics Co", "agency",
     [("morgan@vertexanalytics.demo", "Morgan Chen", "owner", False),
      ("casey@vertexanalytics.demo", "Casey Patel", "member", False)], 50, ("#6a3fd6", "#22c1c3")),
    ("Atlas Home & Co", "agency",
     [("jamie@atlashome.demo", "Jamie Torres", "owner", False)], 5, ("#c2410c", "#0ea5a3")),
    ("Solo Freelance SEO", "solo",
     [("sam@soloseo.demo", "Sam Okafor", "owner", False)], 4, ("#0f766e", "#f59e0b")),
    ("Bright Path Marketing", "solo",
     [("taylor@brightpath.demo", "Taylor Kim", "owner", False)], 2, ("#be185d", "#fbbf24")),
    ("Bluewave Consulting", "solo",
     [("drew@bluewaveconsulting.demo", "Drew Martinez", "owner", False)], 5, ("#1d4ed8", "#38bdf8")),
    ("Summit Retail Group", "inhouse",
     [("riley@summitretail.demo", "Riley Nguyen", "owner", False)], 63, ("#334155", "#eab308")),
]


def _demo_google_sub(email: str) -> str:
    return f"{DEMO_SUB_PREFIX}{email}"


def _wipe_existing_demo_data(db) -> int:
    demo_users = db.query(User).filter(User.google_sub.like(f"{DEMO_SUB_PREFIX}%")).all()
    if not demo_users:
        return 0
    tenant_ids = {m.tenant_id for u in demo_users for m in
                  db.query(Membership).filter_by(user_id=u.id).all()}
    for tenant_id in tenant_ids:
        for sched in scheduler.list_schedules_for_tenant(tenant_id):
            scheduler.delete_schedule(tenant_id, sched.client_id)
            data_context.delete_data_context(tenant_id, sched.client_id)
        db.query(GeneratedReport).filter_by(tenant_id=tenant_id).delete()
    shutil.rmtree(DEMO_REPORTS_OUTPUT_DIR, ignore_errors=True)  # local convenience-copy PDFs
    for u in demo_users:
        db.query(AuthSession).filter_by(user_id=u.id).delete()
        db.delete(u)  # cascades this user's Membership rows
    db.commit()
    for tenant_id in tenant_ids:
        tenant = db.get(Tenant, tenant_id)
        if tenant:
            db.delete(tenant)
    db.commit()
    return len(demo_users)


def _load_uploads() -> dict:
    """Real sample_data/ fixtures -- the same Aurora Home Goods CSVs this
    project's own test suite runs against -- fed through the real
    build_report() pipeline. Nothing about the computed numbers is
    fabricated; only which agency is presenting them varies."""
    analytics_path = SAMPLE_DATA_DIR / "web_analytics.csv"
    seo_path = SAMPLE_DATA_DIR / "seo_audit.csv"
    sales_path = SAMPLE_DATA_DIR / "sales_pipeline.xlsx"
    sales_buf = io.BytesIO(sales_path.read_bytes())
    sales_buf.name = sales_path.name  # report_builder inspects .name to detect xlsx vs csv
    return {
        "analytics": (analytics_path.name, io.BytesIO(analytics_path.read_bytes())),
        "seo": (seo_path.name, io.BytesIO(seo_path.read_bytes())),
        "sales": (sales_path.name, sales_buf),
    }


def _seed_demo_report(tenant_id: str, agency_name: str, colors: tuple[str, str]) -> tuple[str, Path]:
    report_id = uuid.uuid4().hex[:12]
    primary_color, accent_color = colors
    branding = {
        "agency_name": agency_name, "client_name": "Aurora Home Goods",
        "primary_color": primary_color, "accent_color": accent_color,
        "logo_data_uri": None, "font_family": None, "footer_text": None,
        "signature_name": None, "signature_title": None, "disclaimer_text": None,
    }
    result = report_builder.build_report(_load_uploads(), branding, report_id=report_id)
    report_store.persist_report(tenant_id, report_id, result, branding)

    DEMO_REPORTS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    local_path = DEMO_REPORTS_OUTPUT_DIR / f"{report_id}.pdf"
    local_path.write_bytes(result["pdf_bytes"])
    return report_id, local_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="Remove prior demo seed data before reseeding.")
    parser.add_argument("--with-reports", action="store_true",
                         help="Also generate one real report per new tenant (real pipeline, real sample_data).")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.reset:
            removed = _wipe_existing_demo_data(db)
            print(f"Removed {removed} previously-seeded demo user(s) and their tenants/schedules.\n")

        print(f"{'Workspace':<28} {'Plan':<9} {'Owner':<28} {'Active clients':<16} {'Status'}")
        print("-" * 100)
        for name, plan, members, active_clients, colors in DEMO_TENANTS:
            slug_guess = auth._slugify(name)
            already = db.query(Tenant).filter_by(slug=slug_guess).first()
            owner_email = next(email for email, _n, role, _a in members if role == "owner")
            if already:
                print(f"{name:<28} {plan:<9} {owner_email:<28} {'(unchanged)':<16} skipped (already exists)")
                continue

            tenant = Tenant(name=name, slug=auth._unique_slug(db, name), plan=plan)
            db.add(tenant)
            db.flush()  # assigns tenant.id

            for email, person_name, role, is_platform_admin in members:
                user = User(
                    google_sub=_demo_google_sub(email), email=email, name=person_name,
                    is_platform_admin=is_platform_admin,
                )
                db.add(user)
                db.flush()  # assigns user.id
                db.add(Membership(user_id=user.id, tenant_id=tenant.id, role=role))

            # Commit BEFORE calling into data_context/scheduler below: those
            # each open and commit their OWN session on the same SQLite file
            # (see scheduler.py's module docstring) -- leaving this session's
            # tenant/user/membership inserts flushed-but-uncommitted here
            # would hold a writer transaction open and deadlock against them
            # ("database is locked"), since SQLite only ever allows one
            # writer at a time regardless of WAL mode.
            db.commit()

            for i in range(active_clients):
                fake_client_id = f"demo-client-{i + 1:02d}"
                # save_schedule() requires a data context to already exist for
                # its data_source_ref -- a placeholder sqlite config nobody
                # ever actually queries, since these fake schedules are only
                # here to make the Admin panel's active-clients count real.
                data_context.save_data_context(
                    tenant.id, fake_client_id, "sqlite", {"path": f"{fake_client_id}.db"}, {},
                )
                scheduler.save_schedule(scheduler.Schedule(
                    tenant_id=tenant.id, client_id=fake_client_id,
                    data_source_ref=fake_client_id, cadence="monthly",
                ))

            cap = {"solo": 5, "agency": 50, "inhouse": None}[plan]
            cap_label = f"{active_clients}/{cap}" if cap is not None else f"{active_clients}/unlimited"

            report_note = ""
            if args.with_reports:
                report_id, local_path = _seed_demo_report(tenant.id, name, colors)
                report_note = f" -- report: {local_path}"
            print(f"{name:<28} {plan:<9} {owner_email:<28} {cap_label:<16} created{report_note}")

        print("\nDone. View at /admin (Users and Workspaces tables) once logged in as a platform admin.")
        if args.with_reports:
            print(f"Real report PDFs are also saved locally under {DEMO_REPORTS_OUTPUT_DIR} --")
            print("open them directly, no login needed (see the paths printed above).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
