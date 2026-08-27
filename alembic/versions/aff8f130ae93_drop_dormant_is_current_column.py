"""drop dormant is_current column

Revision ID: aff8f130ae93
Revises: 6ed9910381f5
Create Date: 2026-08-27 10:36:24.131717

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'aff8f130ae93'
down_revision: Union[str, None] = '6ed9910381f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Label ruling (2026-08-20): no calibration-of-record exists. The
    # column had been dormant (every row true) since the partial index
    # was dropped (6ed9910381f5). See docs/cleanup_findings.md.
    op.drop_column("calibrations", "is_current")


def downgrade() -> None:
    op.add_column(
        "calibrations",
        sa.Column("is_current", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")))
