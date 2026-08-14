"""kev peaks detector and hv columns

Simulation keV values can be DETECTOR-dependent (the Jin-2026a set:
per detector, source-independent — source-DEPENDENT sets are still
coming and keep using source_id) and depend on the HV they were
simulated at. Both new columns are nullable: NULL means "not
detector-/HV-specific", which is true of every existing row (NNDC),
so no backfill. hv_kv is the HV magnitude in kV (readback convention:
reported +27 means -27 kV — AS 2026-08-14).

Revision ID: ee04eeb163b2
Revises: 571a40f12016
Create Date: 2026-08-14 16:02:16.564055

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ee04eeb163b2'
down_revision: Union[str, None] = '571a40f12016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("kev_peaks",
                  sa.Column("detector", sa.String(10), nullable=True))
    op.add_column("kev_peaks",
                  sa.Column("hv_kv", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("kev_peaks", "hv_kv")
    op.drop_column("kev_peaks", "detector")
