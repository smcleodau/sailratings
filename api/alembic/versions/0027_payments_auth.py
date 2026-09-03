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

revision: str = "0027"
down_revision: Union[str, Sequence[str], None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    #
    # Every table below is declared with plain integer/bigint PKs, not UUID.
    # This revision originally declared UUID PKs, but the live database was
    # actually built by a different, unmerged migration lineage (PAY-01-09)
    # using integer PKs — this revision's `if X not in _tables()` guards made
    # its own CREATE TABLE a permanent no-op the moment that happened, so the
    # UUID declaration never took effect anywhere except a from-scratch build
    # (which nothing had done — until this session's tests did, exposing the
    # mismatch). Rewritten to match live reality exactly, captured via
    # `pg_dump --schema-only`. The CHECK constraints this revision used to
    # declare (role IN ('member','admin'), plan IN ('skipper','programme'),
    # …) don't exist live either and don't match live data (role defaults to
    # 'customer', plan values come from Stripe lookup_keys like
    # 'pro_monthly_gbp') — dropped rather than carried forward as fiction.

    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------
    if "users" not in _tables():
        op.create_table(
            "users",
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column("clerk_id", sa.Text),
            sa.Column("email", sa.Text),
            sa.Column("full_name", sa.Text),
            sa.Column(
                "subscription_status",
                sa.Text,
                nullable=False,
                server_default="none",
            ),
            sa.Column("stripe_customer_id", sa.Text),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.UniqueConstraint("clerk_id", name="uq_users_clerk_id"),
            sa.UniqueConstraint("email", name="uq_users_email"),
            sa.UniqueConstraint(
                "stripe_customer_id", name="users_stripe_customer_id_key"
            ),
        )
    _cidx("idx_users_email", "users", ["email"])
    _cidx("idx_users_stripe_customer", "users", ["stripe_customer_id"])

    # ------------------------------------------------------------------
    # subscriptions — subscription truth, derived from Stripe webhooks
    # ------------------------------------------------------------------
    if "subscriptions" not in _tables():
        op.create_table(
            "subscriptions",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column(
                "user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")
            ),
            sa.Column("stripe_subscription_id", sa.Text, nullable=False),
            sa.Column("stripe_customer_id", sa.Text),
            # Stripe subscription status stored verbatim.
            sa.Column("status", sa.Text),
            # Plan derived from the Stripe price lookup_key prefix.
            sa.Column("plan", sa.Text),
            sa.Column("lookup_key", sa.Text),
            sa.Column("price_id", sa.Text),
            sa.Column("current_period_start", sa.DateTime(timezone=True)),
            sa.Column("current_period_end", sa.DateTime(timezone=True)),
            sa.Column(
                "cancel_at_period_end",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column("canceled_at", sa.DateTime(timezone=True)),
            sa.Column("ended_at", sa.DateTime(timezone=True)),
            sa.Column("raw", sa.JSON),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.UniqueConstraint(
                "stripe_subscription_id",
                name="subscriptions_stripe_subscription_id_key",
            ),
        )
    _cidx("idx_subscriptions_user", "subscriptions", ["user_id"])
    _cidx("idx_subscriptions_customer", "subscriptions", ["stripe_customer_id"])

    # ------------------------------------------------------------------
    # stripe_events — webhook idempotency ledger, keyed on the evt_… id
    # ------------------------------------------------------------------
    if "stripe_events" not in _tables():
        op.create_table(
            "stripe_events",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("event_id", sa.Text, nullable=False),
            sa.Column("type", sa.Text),
            sa.Column("api_version", sa.Text),
            sa.Column(
                "livemode", sa.Boolean, nullable=False, server_default=sa.text("false")
            ),
            sa.Column("payload", sa.JSON),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.Column("processed_at", sa.DateTime(timezone=True)),
            sa.Column("error", sa.Text),
            sa.UniqueConstraint("event_id", name="stripe_events_event_id_key"),
        )
    _cidx("idx_stripe_events_type", "stripe_events", ["type"])
    _cidx("idx_stripe_events_error", "stripe_events", ["error"])

    # ------------------------------------------------------------------
    # boat_claims — a user claims ownership of a boat; moderated
    # pending -> verified / rejected.
    # ------------------------------------------------------------------
    if "boat_claims" not in _tables():
        op.create_table(
            "boat_claims",
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column(
                "user_id",
                sa.BigInteger,
                sa.ForeignKey("users.id"),
                nullable=False,
            ),
            sa.Column(
                "boat_id", sa.Integer, sa.ForeignKey("boats.id"), nullable=False
            ),
            sa.Column("status", sa.Text, nullable=False, server_default="pending"),
            sa.Column("evidence", sa.Text),
            # Plain text (an admin identifier), not an FK — matches live.
            sa.Column("verified_by", sa.Text),
            sa.Column("verified_at", sa.DateTime(timezone=True)),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
    _cidx("idx_boat_claims_boat", "boat_claims", ["boat_id"])
    _cidx("idx_boat_claims_status", "boat_claims", ["status"])
    _cidx("idx_boat_claims_user", "boat_claims", ["user_id"])

    # ------------------------------------------------------------------
    # orders: link to users + explicit Stripe payment status
    # ------------------------------------------------------------------
    if "user_id" not in _columns("orders"):
        op.add_column(
            "orders",
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id")),
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

    # v_admin_users is created by 0034 (which needs users.role/plan, added
    # there) and is deliberately not created here — an earlier version of
    # this revision created a transient copy referencing columns
    # (role, deleted_at) that don't exist until later revisions, which only
    # ever worked because live already had a v_admin_users from elsewhere by
    # the time this ran.


def downgrade() -> None:
    for stmt in _COMPAT_DOWN_STATEMENTS:
        op.execute(stmt)

    # These drops must tolerate the objects already being gone. Later
    # revisions own overlapping DDL — 0034's downgrade drops `boat_claims`
    # outright — so a downgrade from head through 0027 previously died on
    # `index "idx_boat_claims_status" does not exist`. Dropping a table
    # removes its indexes anyway, so IF EXISTS is the correct form here and
    # matches the defensive style used by the rest of the chain.
    op.execute("DROP INDEX IF EXISTS idx_orders_user")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS stripe_payment_status")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS user_id")

    op.execute("DROP INDEX IF EXISTS idx_boat_claims_user")
    op.execute("DROP INDEX IF EXISTS idx_boat_claims_status")
    op.execute("DROP INDEX IF EXISTS idx_boat_claims_boat")
    op.execute("DROP TABLE IF EXISTS boat_claims")

    op.execute("DROP INDEX IF EXISTS idx_stripe_events_error")
    op.execute("DROP INDEX IF EXISTS idx_stripe_events_type")
    op.execute("DROP TABLE IF EXISTS stripe_events")

    op.execute("DROP INDEX IF EXISTS idx_subscriptions_customer")
    op.execute("DROP INDEX IF EXISTS idx_subscriptions_user")
    op.execute("DROP TABLE IF EXISTS subscriptions")

    op.execute("DROP INDEX IF EXISTS idx_users_stripe_customer")
    op.execute("DROP INDEX IF EXISTS idx_users_email")
    op.execute("DROP TABLE IF EXISTS users")
