"""Admin Customers zone: v_admin_users, boat_claims, users.role/plan (PAY-01-10)

Revision ID: 0032
Revises: 0030
Create Date: 2026-09-03

The PAY-01-09 dependency (accounts/subscriptions) may already have created
an integer-PK ``users`` table (clerk_id, email, full_name,
subscription_status, stripe_customer_id) plus a ``subscriptions`` table and
``orders.user_id``. This migration is deliberately defensive and
idempotent:

  * creates ``users`` only if absent, otherwise adds the missing
    ``role`` / ``plan`` / ``last_seen_at`` columns;
  * creates ``boat_claims`` (user boat ownership claims) if absent;
  * adds ``orders.user_id`` if absent;
  * creates the ``v_admin_users`` read model backing GET /v1/admin/users.

All DDL is idempotent (IF NOT EXISTS / guards) because the dev database is
shared between long-lived feature worktrees.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0032"
down_revision: Union[str, Sequence[str], None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- users (only if PAY-01-09 hasn't created it already) ---------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id                  BIGSERIAL PRIMARY KEY,
            clerk_id            TEXT UNIQUE,
            email               TEXT UNIQUE,
            full_name           TEXT,
            subscription_status TEXT NOT NULL DEFAULT 'none',
            stripe_customer_id  TEXT,
            created_at          TIMESTAMPTZ DEFAULT now(),
            updated_at          TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    # Pay-01-09's variant used a serial (integer) PK; widen if present.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                 WHERE table_name = 'users' AND column_name = 'id'
                   AND data_type = 'integer'
            ) THEN
                ALTER TABLE users ALTER COLUMN id TYPE BIGINT;
            END IF;
        END $$;
        """
    )
    # Columns the Customers zone needs, whichever branch created the table.
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'customer'")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS plan TEXT NOT NULL DEFAULT 'free'")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users (email)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_users_plan ON users (plan)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users (role)")

    # Keep users.plan in step with the live subscription (PAY-01-09 writes
    # subscriptions.lookup_key e.g. 'pro_monthly_gbp'). Dynamic SQL inside a
    # DO block so the statement is only planned when the table exists.
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.subscriptions') IS NOT NULL THEN
                EXECUTE $q$
                    UPDATE users u
                       SET plan = s.plan
                      FROM (
                            SELECT DISTINCT ON (user_id) user_id, plan
                              FROM subscriptions
                             WHERE plan IS NOT NULL
                               AND status IN ('active', 'trialing')
                             ORDER BY user_id, created_at DESC
                           ) s
                     WHERE u.id = s.user_id
                $q$;
            END IF;
        END $$;
        """
    )

    # --- boat_claims --------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS boat_claims (
            id          BIGSERIAL PRIMARY KEY,
            user_id     BIGINT NOT NULL REFERENCES users(id),
            boat_id     INTEGER NOT NULL REFERENCES boats(id),
            status      TEXT NOT NULL DEFAULT 'pending',
            evidence    TEXT,
            verified_by TEXT,
            verified_at TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_boat_claims_user ON boat_claims (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_boat_claims_boat ON boat_claims (boat_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_boat_claims_status ON boat_claims (status)")

    # --- orders.user_id -----------------------------------------------------
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS user_id BIGINT")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'orders_user_id_fkey'
            ) THEN
                ALTER TABLE orders
                    ADD CONSTRAINT orders_user_id_fkey
                    FOREIGN KEY (user_id) REFERENCES users(id);
            END IF;
        END $$;
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_orders_user ON orders (user_id)")
    op.execute(
        """
        UPDATE orders o
           SET user_id = u.id
          FROM users u
         WHERE o.user_id IS NULL
           AND o.email IS NOT NULL
           AND lower(o.email) = lower(u.email)
        """
    )

    # --- v_admin_users ------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE VIEW v_admin_users AS
        SELECT
            u.id,
            u.email,
            u.full_name,
            u.role,
            u.plan,
            u.subscription_status,
            u.clerk_id,
            u.stripe_customer_id,
            u.created_at                                        AS joined_at,
            u.last_seen_at,
            COALESCE(bc.boats_claimed, 0)                       AS boats_claimed,
            COALESCE(bc.pending_claims, 0)                      AS pending_claims,
            COALESCE(oc.reports_bought, 0)                      AS reports_bought,
            oc.total_spend_cents,
            oc.last_order_currency
        FROM users u
        LEFT JOIN LATERAL (
            SELECT
                count(*) FILTER (WHERE c.status = 'verified') AS boats_claimed,
                count(*) FILTER (WHERE c.status = 'pending')  AS pending_claims
            FROM boat_claims c
            WHERE c.user_id = u.id
        ) bc ON true
        LEFT JOIN LATERAL (
            SELECT
                count(*) FILTER (
                    WHERE o.status IN ('paid', 'generated')
                )                                             AS reports_bought,
                sum(o.amount_cents) FILTER (
                    WHERE o.status IN ('paid', 'generated')
                )                                             AS total_spend_cents,
                (array_agg(o.currency ORDER BY o.created_at DESC)
                    FILTER (WHERE o.status IN ('paid', 'generated')))[1]
                                                                AS last_order_currency
            FROM orders o
            WHERE o.user_id = u.id
        ) oc ON true
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_admin_users")
    op.execute("DROP TABLE IF EXISTS boat_claims")
    # users / orders.user_id may belong to PAY-01-09 — leave them in place.
