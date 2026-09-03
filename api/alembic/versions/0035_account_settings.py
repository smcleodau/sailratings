"""AUTH-01-03: account settings, data export and deletion.

Members control their own data.

Creates:
  * ``user_settings`` — one row per user holding the editable profile fields
    (display name / home club / country) and the notification preferences.
    Kept out of ``users`` itself so the AUTH-01-01 identity row stays a thin
    mirror of Clerk + Stripe linkage, and so settings can be wiped
    independently of the identity audit trail on deletion.
  * ``users.deletion_requested_at`` / ``users.deletion_completed_at`` —
    privacy-policy audit trail for account deletion. Deletion anonymises the
    identity row (email / clerk_id / stripe customer cleared, personal
    fields nulled) but keeps the row so ``orders`` keeps its referential
    integrity for financial-record retention, per the privacy policy's
    "we keep transaction records for up to 7 years" clause. Everything
    keyed by ``user_id`` (subscriptions, boat claims, settings) is deleted
    outright via ON DELETE CASCADE / explicit deletes.

Idempotent (IF NOT EXISTS guards) per the repo's canonical-chain convention:
the dev database is shared between long-lived feature worktrees.

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0035"
down_revision: Union[str, Sequence[str], None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # users.id is UUID on the canonical chain (0027) but BIGINT on dev
    # databases built from the retired PAY-01-09 side branch (see 0034's
    # widening note). Match the live type so the FK always applies.
    users_id_type = next(
        (c["type"] for c in sa.inspect(bind).get_columns("users")
         if c["name"] == "id"),
        None,
    )
    id_is_uuid = users_id_type is None or isinstance(
        users_id_type, postgresql.UUID
    )
    user_id_type = sa.Uuid if id_is_uuid else sa.BigInteger

    # ------------------------------------------------------------------
    # user_settings — editable profile + notification preferences
    # ------------------------------------------------------------------
    if "user_settings" not in tables:
        op.create_table(
            "user_settings",
            sa.Column(
                "user_id",
                user_id_type,
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("display_name", sa.Text),
            sa.Column("home_club", sa.Text),
            sa.Column("country", sa.Text),
            # Notification preferences — defaults honour the privacy policy's
            # "only essential transactional email unless you opt in" rule.
            sa.Column(
                "notify_product_updates",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "notify_rating_changes",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "notify_event_reminders",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "notify_marketing",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
        )

    # ------------------------------------------------------------------
    # users — deletion audit trail (privacy-policy retention)
    # ------------------------------------------------------------------
    cols = {c["name"] for c in sa.inspect(bind).get_columns("users")}
    if "deletion_requested_at" not in cols:
        op.add_column(
            "users",
            sa.Column("deletion_requested_at", sa.DateTime(timezone=True)),
        )
    if "deletion_completed_at" not in cols:
        op.add_column(
            "users",
            sa.Column("deletion_completed_at", sa.DateTime(timezone=True)),
        )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS deletion_completed_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS deletion_requested_at")
    op.execute("DROP TABLE IF EXISTS user_settings")
