"""PAY-01-07 / SPEC-23 §1: payments & auth schema.

One place subscription truth lives — derived from Stripe, never hand-edited.

Creates:
  * ``users`` (per AUTH-01-01 plus the SPEC-23 columns ``stripe_customer_id``,
    ``role``, ``last_seen_at``, ``deleted_at``; deliberately **no**
    ``subscription_status`` column — subscription truth lives in
    ``subscriptions``, derived from Stripe webhooks).
  * ``subscriptions`` — Stripe subscription mirror
    (``stripe_subscription_id`` unique, ``plan`` derived from the Stripe
    price ``lookup_key`` prefix, Stripe ``status`` stored verbatim, period
    bounds, cancel flags, and the full payload in ``raw`` jsonb).
  * ``stripe_events`` — ``evt_…`` id as primary key for webhook idempotency.
  * ``boat_claims`` — user ↔ boat ownership claims
    (unique ``user_id`` + ``boat_id``, status pending/verified/rejected).
  * ``orders.user_id`` / ``orders.stripe_payment_status``.
  * view ``v_admin_users`` — admin customer overview joining users with
    their current subscription, verified boat claims and order aggregates.

This revision also keeps the DP-03-05 compatibility surface on the canonical
chain — the ``schema_migrations`` / ``backup_checks`` evidence tables and the
``v1_boat_ratings`` / ``v1_race_results`` / ``v1_fact_assertions_current``
stable consumer views — as idempotent ensures (previously created by the
abandoned ``0026_canonical_merge_and_compat`` side branch, which is retired
to ``alembic/legacy_versions/`` so the canonical chain is linear and
``alembic upgrade head`` unambiguous).

Revision ID: 0027
Revises: 0026 (0026_policy_v1_rulings — DP-01-02 policy stamp, kept canonical)
Create Date: 2026-09-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0027"
down_revision: Union[str, Sequence[str], None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_V_ADMIN_USERS_SQL = """
CREATE VIEW v_admin_users AS
SELECT
    u.id,
    u.email,
    u.full_name,
    u.role,
    u.clerk_id,
    u.stripe_customer_id,
    s.plan AS subscription_plan,
    s.status AS subscription_status,
    s.current_period_end AS subscription_current_period_end,
    COALESCE(bc.boats_claimed, 0) AS boats_claimed,
    COALESCE(o.orders_count, 0) AS orders_count,
    o.total_spend_cents,
    u.created_at,
    u.last_seen_at
FROM users u
LEFT JOIN LATERAL (
    SELECT s1.plan, s1.status, s1.current_period_end
    FROM subscriptions s1
    WHERE s1.user_id = u.id
    ORDER BY s1.updated_at DESC NULLS LAST, s1.created_at DESC
    LIMIT 1
) s ON true
LEFT JOIN (
    SELECT user_id, count(*) AS boats_claimed
    FROM boat_claims
    WHERE status = 'verified'
    GROUP BY user_id
) bc ON bc.user_id = u.id
LEFT JOIN (
    SELECT user_id, count(*) AS orders_count, sum(amount_cents) AS total_spend_cents
    FROM orders
    GROUP BY user_id
) o ON o.user_id = u.id
WHERE u.deleted_at IS NULL
"""


# ---------------------------------------------------------------------------
# DP-03-05 compatibility surface (kept on the canonical chain; idempotent)
# ---------------------------------------------------------------------------

_COMPAT_STATEMENTS = [
    # -- migration evidence -------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        id              BIGSERIAL PRIMARY KEY,
        revision        TEXT NOT NULL,
        direction       TEXT NOT NULL DEFAULT 'upgrade',
        applied_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        checksum        TEXT,
        rows_affected   BIGINT,
        duration_ms     BIGINT,
        notes           TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_schema_migrations_revision ON schema_migrations (revision)",
    "CREATE INDEX IF NOT EXISTS ix_schema_migrations_applied_at ON schema_migrations (applied_at)",

    # -- backup / restore checks -------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS backup_checks (
        id              BIGSERIAL PRIMARY KEY,
        backup_id       TEXT NOT NULL,
        db_name         TEXT NOT NULL,
        taken_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        size_bytes      BIGINT,
        sha256          TEXT,
        verified_at     TIMESTAMPTZ,
        status          TEXT NOT NULL DEFAULT 'pending',
        notes           TEXT,
        CONSTRAINT ck_backup_checks_status
            CHECK (status IN ('pending', 'verified', 'failed'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_backup_checks_backup_id ON backup_checks (backup_id)",
    "CREATE INDEX IF NOT EXISTS ix_backup_checks_status ON backup_checks (status)",

    # -- v1_boat_ratings: current rating per boat ---------------------------
    """
    CREATE OR REPLACE VIEW v1_boat_ratings AS
    SELECT
        b.id            AS boat_id,
        b.boat_name     AS boat_name,
        b.sail_number   AS sail_number,
        b.cert_number   AS cert_number,
        b.design        AS design,
        b.country       AS country,
        b.year_built    AS year_built,
        t.snapshot_date AS rating_date,
        t.tcc           AS tcc,
        t.non_spi_tcc   AS non_spi_tcc,
        t.endorsed      AS endorsed
    FROM boats b
    LEFT JOIN LATERAL (
        SELECT s.snapshot_date, s.tcc, s.non_spi_tcc, s.endorsed
        FROM tcc_snapshots s
        WHERE s.boat_id = b.id
        ORDER BY s.snapshot_date DESC
        LIMIT 1
    ) t ON true
    """,

    # -- v1_race_results: results flattened via the strict-3NF join ---------
    """
    CREATE OR REPLACE VIEW v1_race_results AS
    SELECT
        r.id             AS race_result_id,
        e.name           AS event_name,
        e.start_date     AS event_date,
        r.race_name      AS race_name,
        r.race_number    AS race_number,
        r.division       AS division,
        r.class_name     AS class_name,
        r.place          AS place,
        r.status         AS status,
        r.rating_value   AS rating_value,
        r.tcc_at_race    AS tcc_at_race,
        r.elapsed_time   AS elapsed_time,
        r.corrected_time AS corrected_time,
        b.id             AS boat_id,
        b.boat_name      AS boat_name,
        b.sail_number    AS sail_number,
        b.design         AS design,
        b.country        AS country
    FROM race_results r
    JOIN event_entries ee ON ee.id = r.event_entry_id
    LEFT JOIN events e ON e.id = ee.event_id
    LEFT JOIN boats b ON b.id = ee.boat_id
    """,

    # -- v1_fact_assertions_current: current resolved truth -----------------
    """
    CREATE OR REPLACE VIEW v1_fact_assertions_current AS
    SELECT
        a.assertion_id   AS assertion_id,
        a.entity_type    AS entity_type,
        a.entity_key     AS entity_key,
        a.field          AS field,
        a.value_json     AS value_json,
        a.unit           AS unit,
        a.valid_from     AS valid_from,
        a.valid_to       AS valid_to,
        a.recorded_at    AS recorded_at,
        a.source_slug    AS source_slug,
        a.confidence     AS confidence
    FROM fact_assertions a
    WHERE a.status = 'active'
      AND a.superseded_by IS NULL
    """,
]

_COMPAT_DOWN_STATEMENTS = [
    "DROP VIEW IF EXISTS v1_fact_assertions_current",
    "DROP VIEW IF EXISTS v1_race_results",
    "DROP VIEW IF EXISTS v1_boat_ratings",
    "DROP TABLE IF EXISTS backup_checks",
    "DROP TABLE IF EXISTS schema_migrations",
]


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _cidx(name: str, table: str, columns: list[str]) -> None:
    """create_index only when missing (idempotent convergence)."""
    bind = op.get_bind()
    if name not in {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}:
        op.create_index(name, table, columns)


def upgrade() -> None:
    # This revision is idempotent: databases that already carry some of these
    # objects (e.g. from the retired DP-03-05 side branch) converge cleanly.

    # ------------------------------------------------------------------
    # users (AUTH-01-01 columns + SPEC-23 additions; no subscription_status)
    # ------------------------------------------------------------------
    if "users" not in _tables():
        op.create_table(
            "users",
            sa.Column(
                "id",
                sa.Uuid,
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("clerk_id", sa.Text, nullable=False),
            sa.Column("email", sa.Text, nullable=False),
            sa.Column("full_name", sa.Text),
            sa.Column("stripe_customer_id", sa.Text),
            sa.Column("role", sa.Text, nullable=False, server_default="member"),
            sa.Column("last_seen_at", sa.DateTime(timezone=True)),
            sa.Column("deleted_at", sa.DateTime(timezone=True)),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.UniqueConstraint("clerk_id", name="uq_users_clerk_id"),
            sa.UniqueConstraint("email", name="uq_users_email"),
            sa.CheckConstraint(
                "role IN ('member', 'admin')", name="ck_users_role"
            ),
        )
    _cidx("idx_users_clerk_id", "users", ["clerk_id"])
    _cidx("idx_users_email", "users", ["email"])
    _cidx("idx_users_stripe_customer_id", "users", ["stripe_customer_id"])

    # ------------------------------------------------------------------
    # subscriptions — subscription truth, derived from Stripe webhooks
    # ------------------------------------------------------------------
    if "subscriptions" not in _tables():
        op.create_table(
            "subscriptions",
            sa.Column(
                "id",
                sa.Uuid,
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "user_id",
                sa.Uuid,
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("stripe_subscription_id", sa.Text, nullable=False),
            sa.Column("stripe_customer_id", sa.Text, nullable=False),
            # Plan derived from the Stripe price lookup_key prefix
            # (e.g. 'skipper_monthly' -> 'skipper').
            sa.Column("plan", sa.Text, nullable=False),
            # Stripe subscription status stored verbatim.
            sa.Column("status", sa.Text, nullable=False),
            sa.Column("current_period_start", sa.DateTime(timezone=True)),
            sa.Column("current_period_end", sa.DateTime(timezone=True)),
            sa.Column(
                "cancel_at_period_end",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column("cancel_at", sa.DateTime(timezone=True)),
            sa.Column("canceled_at", sa.DateTime(timezone=True)),
            sa.Column("ended_at", sa.DateTime(timezone=True)),
            sa.Column(
                "raw",
                postgresql.JSONB,
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.UniqueConstraint(
                "stripe_subscription_id", name="uq_subscriptions_stripe_subscription_id"
            ),
            sa.CheckConstraint(
                "plan IN ('skipper', 'programme')", name="ck_subscriptions_plan"
            ),
            sa.CheckConstraint(
                "status IN ('incomplete', 'incomplete_expired', 'trialing', 'active',"
                " 'past_due', 'canceled', 'unpaid', 'paused')",
                name="ck_subscriptions_status",
            ),
        )
    _cidx("idx_subscriptions_user", "subscriptions", ["user_id"])
    _cidx("idx_subscriptions_stripe_customer", "subscriptions", ["stripe_customer_id"])
    _cidx("idx_subscriptions_status", "subscriptions", ["status"])

    # ------------------------------------------------------------------
    # stripe_events — evt_ id as PK so webhook redelivery is idempotent
    # ------------------------------------------------------------------
    if "stripe_events" not in _tables():
        op.create_table(
            "stripe_events",
            sa.Column("id", sa.Text, primary_key=True),
            sa.Column("type", sa.Text, nullable=False),
            sa.Column("livemode", sa.Boolean, nullable=False),
            sa.Column(
                "payload",
                postgresql.JSONB,
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "processed_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
        )
    _cidx("idx_stripe_events_type", "stripe_events", ["type"])
    _cidx("idx_stripe_events_processed_at", "stripe_events", ["processed_at"])

    # ------------------------------------------------------------------
    # boat_claims — a user claims ownership of a boat; one open record per
    # (user, boat), moderated pending -> verified / rejected.
    # ------------------------------------------------------------------
    if "boat_claims" not in _tables():
        op.create_table(
            "boat_claims",
            sa.Column(
                "id",
                sa.Uuid,
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "user_id",
                sa.Uuid,
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "boat_id",
                sa.Integer,
                sa.ForeignKey("boats.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("status", sa.Text, nullable=False, server_default="pending"),
            sa.Column("evidence", sa.Text),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.Column("verified_at", sa.DateTime(timezone=True)),
            sa.Column(
                "verified_by", sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")
            ),
            sa.UniqueConstraint("user_id", "boat_id", name="uq_boat_claims_user_boat"),
            sa.CheckConstraint(
                "status IN ('pending', 'verified', 'rejected')",
                name="ck_boat_claims_status",
            ),
        )
    _cidx("idx_boat_claims_boat", "boat_claims", ["boat_id"])
    _cidx("idx_boat_claims_status", "boat_claims", ["status"])

    # ------------------------------------------------------------------
    # orders: link to users + explicit Stripe payment status
    # ------------------------------------------------------------------
    if "user_id" not in _columns("orders"):
        op.add_column(
            "orders",
            sa.Column(
                "user_id",
                sa.Uuid,
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    if "stripe_payment_status" not in _columns("orders"):
        op.add_column(
            "orders", sa.Column("stripe_payment_status", sa.Text, nullable=True)
        )
    _cidx("idx_orders_user", "orders", ["user_id"])

    # ------------------------------------------------------------------
    # DP-03-05 compatibility surface (evidence tables + stable v1 views)
    # ------------------------------------------------------------------
    for stmt in _COMPAT_STATEMENTS:
        op.execute(stmt)

    # ------------------------------------------------------------------
    # v_admin_users — admin customer overview
    # ------------------------------------------------------------------
    op.execute(_V_ADMIN_USERS_SQL)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_admin_users")

    for stmt in _COMPAT_DOWN_STATEMENTS:
        op.execute(stmt)

    op.drop_index("idx_orders_user", table_name="orders")
    op.drop_column("orders", "stripe_payment_status")
    op.drop_column("orders", "user_id")

    op.drop_index("idx_boat_claims_status", table_name="boat_claims")
    op.drop_index("idx_boat_claims_boat", table_name="boat_claims")
    op.drop_table("boat_claims")

    op.drop_index("idx_stripe_events_processed_at", table_name="stripe_events")
    op.drop_index("idx_stripe_events_type", table_name="stripe_events")
    op.drop_table("stripe_events")

    op.drop_index("idx_subscriptions_status", table_name="subscriptions")
    op.drop_index("idx_subscriptions_stripe_customer", table_name="subscriptions")
    op.drop_index("idx_subscriptions_user", table_name="subscriptions")
    op.drop_table("subscriptions")

    op.drop_index("idx_users_stripe_customer_id", table_name="users")
    op.drop_index("idx_users_email", table_name="users")
    op.drop_index("idx_users_clerk_id", table_name="users")
    op.drop_table("users")
