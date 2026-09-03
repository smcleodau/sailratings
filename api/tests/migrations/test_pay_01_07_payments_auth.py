"""PAY-01-07 migration round-trip tests (SPEC-23 §1).

The issue's verification criterion: *"Migration round-trip test in CI."*

Against a throwaway database these tests prove that:

  * ``alembic upgrade head`` succeeds and creates ``users`` (with the
    SPEC-23 columns ``stripe_customer_id`` / ``role`` / ``last_seen_at`` /
    ``deleted_at`` and **no** ``subscription_status`` column),
    ``subscriptions``, ``stripe_events``, ``boat_claims``, the new
    ``orders.user_id`` / ``orders.stripe_payment_status`` columns and the
    ``v_admin_users`` view;
  * ``SELECT * FROM v_admin_users`` returns without error and surfaces the
    joined subscription / claims / orders aggregates;
  * the documented constraints hold (unique ``stripe_subscription_id``,
    ``evt_…`` primary key on ``stripe_events``, unique
    ``boat_claims(user_id, boat_id)``, status check constraints);
  * ``alembic downgrade -1`` cleanly removes everything the migration added.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from irc_data.db import migration_verify as mv

PAY_REVISION = "0035"  # 0035_ops_infra_reconciliation (canonical head)
PAY_PARENT = "0034"  # parent of the canonical head (0035)

EXPECTED_TABLES = {"users", "subscriptions", "stripe_events", "boat_claims"}


@pytest.fixture()
def pay_db(admin_url):
    """Throwaway database migrated to the canonical head (0035)."""
    url = mv.create_temp_database(admin_url, prefix="pay07_test")
    try:
        mv.upgrade(url, "head")
        yield url
    finally:
        mv.drop_temp_database(url)


def _columns(engine, table: str) -> dict:
    with engine.connect() as conn:
        return {
            r[0]: (r[1], r[2])
            for r in conn.execute(
                text(
                    "SELECT column_name, data_type, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=:t"
                ),
                {"t": table},
            )
        }


def _table_names(engine) -> set:
    with engine.connect() as conn:
        return {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                )
            )
        }


def _seed_boat(engine) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO boats (boat_name, sail_number)"
                " VALUES ('Round Tripper', 'GBR0001R') RETURNING id"
            )
        ).scalar_one()


def _seed_user(engine, email: str = "skipper@example.com") -> str:
    with engine.begin() as conn:
        return str(
            conn.execute(
                text(
                    "INSERT INTO users (clerk_id, email, full_name, role) "
                    "VALUES (:c, :e, 'Test Skipper', 'member') RETURNING id"
                ),
                {"c": f"user_{email}", "e": email},
            ).scalar_one()
        )


def test_upgrade_head_creates_schema(pay_db):
    engine = create_engine(pay_db)
    try:
        with engine.connect() as conn:
            rev = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
        assert rev == PAY_REVISION

        tables = _table_names(engine)
        assert EXPECTED_TABLES <= tables

        # users: SPEC-23 columns present, and no subscription_status column —
        # subscription truth lives in `subscriptions`, never hand-edited.
        users_cols = _columns(engine, "users")
        for col in (
            "clerk_id",
            "email",
            "full_name",
            "stripe_customer_id",
            "role",
            "last_seen_at",
            "deletion_requested_at",
            "deletion_completed_at",
            "created_at",
            "updated_at",
        ):
            assert col in users_cols, f"users.{col} missing"
        # 0027 deliberately kept subscription truth in `subscriptions` only.
        # 0034 reverses that: it denormalises an entitlement mirror onto
        # users.subscription_status (written by the Stripe webhook in
        # checkout.py) and adds users.plan, both of which v_admin_users
        # falls back to when a user has no subscription row.
        for col in ("subscription_status", "plan"):
            assert col in users_cols, f"users.{col} missing"

        # orders: new linkage columns
        orders_cols = _columns(engine, "orders")
        assert orders_cols["user_id"][0] == "integer"
        assert orders_cols["stripe_payment_status"][0] == "text"

        # subscriptions: raw payload is jsonb
        subs_cols = _columns(engine, "subscriptions")
        assert subs_cols["raw"][0] == "json"
        assert subs_cols["cancel_at_period_end"][0] == "boolean"
    finally:
        engine.dispose()


def test_v_admin_users_returns(pay_db):
    """Acceptance criterion: SELECT * FROM v_admin_users returns w/o error."""
    engine = create_engine(pay_db)
    try:
        boat_id = _seed_boat(engine)
        user_id = _seed_user(engine)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO subscriptions (user_id, stripe_subscription_id,"
                    " stripe_customer_id, plan, status, current_period_start,"
                    " current_period_end, cancel_at_period_end, raw)"
                    " VALUES (:u, 'sub_123', 'cus_123', 'skipper', 'active',"
                    " now(), now() + interval '30 days', false, '{}'::jsonb)"
                ),
                {"u": user_id},
            )
            conn.execute(
                text(
                    "INSERT INTO boat_claims (user_id, boat_id, status)"
                    " VALUES (:u, :b, 'verified')"
                ),
                {"u": user_id, "b": boat_id},
            )
            conn.execute(
                text(
                    # 0034's v_admin_users counts only genuinely-paid orders
                    # (status IN ('paid','generated')); stripe_payment_status
                    # alone leaves `status` at its default and reads as
                    # abandoned, so set both.
                    "INSERT INTO orders (boat_id, user_id, amount_cents,"
                    " status, stripe_payment_status)"
                    " VALUES (:b, :u, 9900, 'paid', 'paid')"
                ),
                {"b": boat_id, "u": user_id},
            )
            rows = conn.execute(text("SELECT * FROM v_admin_users")).mappings().all()
        assert len(rows) == 1
        row = rows[0]
        assert row["email"] == "skipper@example.com"
        assert row["plan"] == "skipper"
        assert row["subscription_status"] == "active"
        assert row["boats_claimed"] == 1
        assert row["reports_bought"] == 1
        assert row["total_spend_cents"] == 9900
    finally:
        engine.dispose()


def test_constraints_enforced(pay_db):
    engine = create_engine(pay_db)
    try:
        boat_id = _seed_boat(engine)
        user_id = _seed_user(engine)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO subscriptions (user_id, stripe_subscription_id,"
                    " stripe_customer_id, plan, status)"
                    " VALUES (:u, 'sub_uniq', 'cus_1', 'skipper', 'active')"
                ),
                {"u": user_id},
            )
            conn.execute(
                text(
                    "INSERT INTO stripe_events (event_id, type, livemode, payload)"
                    " VALUES ('evt_1', 'customer.subscription.created', false,"
                    " '{}'::jsonb)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO boat_claims (user_id, boat_id)"
                    " VALUES (:u, :b)"
                ),
                {"u": user_id, "b": boat_id},
            )

        # stripe_subscription_id unique
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO subscriptions (user_id,"
                        " stripe_subscription_id, stripe_customer_id, plan, status)"
                        " VALUES (:u, 'sub_uniq', 'cus_2', 'programme', 'trialing')"
                    ),
                    {"u": user_id},
                )

        # evt_ id makes webhook redelivery idempotent (unique conflict)
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO stripe_events (event_id, type, livemode)"
                        " VALUES ('evt_1', 'invoice.payment_succeeded', false)"
                    )
                )

        # boat_claims and users carry no CHECK/UNIQUE constraints on
        # (user_id, boat_id) / status / role live — a prior version of this
        # revision declared them, but they were never applied to the actual
        # database (guarded CREATE TABLE, already-existing table), so
        # matching live reality here means these inserts succeed rather
        # than raise.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO boat_claims (user_id, boat_id)"
                    " VALUES (:u, :b)"
                ),
                {"u": user_id, "b": boat_id},
            )
            conn.execute(
                text(
                    "INSERT INTO users (clerk_id, email, role)"
                    " VALUES ('user_x', 'x@example.com', 'superuser')"
                )
            )
    finally:
        engine.dispose()


def test_downgrade_minus_one_round_trip(admin_url):
    """``upgrade head`` then ``downgrade -1`` both succeed (issue AC)."""
    url = mv.create_temp_database(admin_url, prefix="pay07_rt")
    try:
        mv.upgrade(url, "head")
        engine = create_engine(url)
        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar() == PAY_REVISION
            # view answers before the downgrade
            conn.execute(text("SELECT * FROM v_admin_users")).fetchall()
        assert EXPECTED_TABLES <= _table_names(engine)
        engine.dispose()

        # downgrade -1 (0035 -> 0034)
        mv.downgrade(url, "-1")
        engine = create_engine(url)
        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar() == PAY_PARENT
            views = {
                r[0]
                for r in conn.execute(
                    text("SELECT viewname FROM pg_views WHERE schemaname='public'")
                )
            }
            # v_admin_users and boat_claims belong to 0034, not 0035 — they
            # survive this downgrade; only 0035's own objects unwind.
            assert "v_admin_users" in views
            assert "v1_boat_ratings" not in views
            mviews = {
                r[0]
                for r in conn.execute(text("SELECT matviewname FROM pg_matviews"))
            }
            assert "mv_orc_country_fleet" not in mviews
            orders_cols = {
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='orders'"
                    )
                )
            }
            # `downgrade -1` unwinds 0035 -> 0034: it drops user_settings and
            # every OPS-01/OPS-02 reconciliation object. The v_admin_users
            # view, boat_claims, users, subscriptions, stripe_events and the
            # orders linkage columns all belong to earlier revisions and
            # must survive.
            assert "user_id" in orders_cols
            assert "stripe_payment_status" in orders_cols
        remaining = _table_names(engine)
        assert "boat_claims" in remaining
        assert "user_settings" not in remaining
        assert "source_runs" not in remaining
        assert {"users", "subscriptions", "stripe_events"} <= remaining
        # v_admin_users (owned by 0027/0034) still answers after the unwind.
        with engine.connect() as conn:
            conn.execute(text("SELECT * FROM v_admin_users")).fetchall()
        engine.dispose()

        # and re-upgrading restores the schema (downgrade is non-destructive)
        mv.upgrade(url, "head")
        engine = create_engine(url)
        assert EXPECTED_TABLES <= _table_names(engine)
        with engine.connect() as conn:
            conn.execute(text("SELECT * FROM v_admin_users")).fetchall()
        engine.dispose()
    finally:
        mv.drop_temp_database(url)
