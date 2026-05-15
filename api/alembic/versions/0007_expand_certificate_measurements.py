"""expand certificate measurements

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("certificates", sa.Column("lwp", sa.Numeric(6, 2)))
    op.add_column("certificates", sa.Column("dlr", sa.Integer()))
    op.add_column("certificates", sa.Column("x", sa.Numeric(6, 2)))
    op.add_column("certificates", sa.Column("y", sa.Numeric(6, 2)))
    op.add_column("certificates", sa.Column("internal_ballast", sa.Numeric(8, 1)))
    op.add_column("certificates", sa.Column("hsa", sa.Numeric(8, 2)))
    op.add_column("certificates", sa.Column("headsails_max", sa.Integer()))
    op.add_column("certificates", sa.Column("flying_headsails_max", sa.Integer()))
    op.add_column("certificates", sa.Column("fsa", sa.Numeric(8, 2)))
    op.add_column("certificates", sa.Column("flu", sa.Numeric(6, 2)))
    op.add_column("certificates", sa.Column("flp", sa.Numeric(6, 2)))
    op.add_column("certificates", sa.Column("fuw", sa.Numeric(6, 2)))
    op.add_column("certificates", sa.Column("ftw", sa.Numeric(6, 2)))
    op.add_column("certificates", sa.Column("fhw", sa.Numeric(6, 2)))
    op.add_column("certificates", sa.Column("fsfl", sa.Numeric(6, 2)))
    op.add_column("certificates", sa.Column("fshw", sa.Numeric(6, 2)))
    op.add_column("certificates", sa.Column("spa", sa.Numeric(8, 2)))
    op.add_column("certificates", sa.Column("spinnakers_max", sa.Integer()))
    op.add_column("certificates", sa.Column("stl_fh_max", sa.Numeric(6, 2)))
    op.add_column("certificates", sa.Column("aft_rigging", sa.Integer()))


def downgrade() -> None:
    op.drop_column("certificates", "aft_rigging")
    op.drop_column("certificates", "stl_fh_max")
    op.drop_column("certificates", "spinnakers_max")
    op.drop_column("certificates", "spa")
    op.drop_column("certificates", "fshw")
    op.drop_column("certificates", "fsfl")
    op.drop_column("certificates", "fhw")
    op.drop_column("certificates", "ftw")
    op.drop_column("certificates", "fuw")
    op.drop_column("certificates", "flp")
    op.drop_column("certificates", "flu")
    op.drop_column("certificates", "fsa")
    op.drop_column("certificates", "flying_headsails_max")
    op.drop_column("certificates", "headsails_max")
    op.drop_column("certificates", "hsa")
    op.drop_column("certificates", "internal_ballast")
    op.drop_column("certificates", "y")
    op.drop_column("certificates", "x")
    op.drop_column("certificates", "dlr")
    op.drop_column("certificates", "lwp")
