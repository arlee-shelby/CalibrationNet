"""drop one-current calibration index

Revision ID: 6ed9910381f5
Revises: ee04eeb163b2
Create Date: 2026-08-21 16:03:45.555986

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '6ed9910381f5'
down_revision: Union[str, None] = 'ee04eeb163b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Bookkeeping ruling (AS 2026-08-20): calibration labels are
    # permanent coexisting families with NO cross-label "current" —
    # is_current is dormant (always true). This partial index enforced
    # the retired one-current-per-(run_pixel, type) rule and broke
    # same-label replaces (insert flushes before delete) as well as
    # any two coexisting labels. See docs/fit_storage.md registry.
    op.drop_index("ix_calibrations_one_current", table_name="calibrations")


def downgrade() -> None:
    op.create_index("ix_calibrations_one_current", "calibrations",
                    ["run_pixel_id", "calibration_type"], unique=True,
                    postgresql_where=sa.text("is_current"))
