"""enable fuzzystrmatch extension for sail-number near-match suggestions

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-14
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Standard Postgres contrib extension. Provides levenshtein() which we
    # use as a fallback for sail-number near-matches (pg_trgm similarity
    # is too sparse on 4-char numeric strings).
    op.execute("CREATE EXTENSION IF NOT EXISTS fuzzystrmatch")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS fuzzystrmatch")
