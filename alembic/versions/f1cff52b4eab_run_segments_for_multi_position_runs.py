"""run segments for multi-position runs

Long "rastering" runs step the source frame through many positions with a
~30 min dwell at each, so the unit with a single source configuration is a
dwell period, not a run. This adds run_segments (one row per dwell) and
moves the source position onto it, then makes run_pixels segment-aware.

Existing runs each get exactly one segment (index 0) carrying the position
they already had, so nothing about them changes in meaning. run_pixels'
run_id/pixel_id are also renamed to run_number/pixel_number, which is what
they have actually held since the natural-primary-key migration.

Revision ID: f1cff52b4eab
Revises: c3047361f2b1
Create Date: 2026-07-28 15:37:04.843767

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1cff52b4eab'
down_revision: Union[str, None] = 'c3047361f2b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_CONVENTION = 'legacy-units'


def upgrade() -> None:
    # 1. The new segment table.
    op.create_table(
        'run_segments',
        sa.Column('run_number', sa.Integer(), nullable=False),
        sa.Column('segment_index', sa.Integer(), nullable=False),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('linear_position', sa.Double(), nullable=True),
        sa.Column('horizontal_position', sa.Double(), nullable=True),
        sa.Column('position_convention', sa.String(length=20), nullable=True),
        sa.Column('notes', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['run_number'], ['runs.run_number'],
                                name='run_segments_run_number_fkey'),
        sa.PrimaryKeyConstraint('run_number', 'segment_index',
                                name='run_segments_pkey'),
    )

    # 2. Every existing run becomes a single-segment run carrying the
    #    position (and time span) it already had.
    op.execute(f"""
        INSERT INTO run_segments (run_number, segment_index, start_time,
                                  end_time, linear_position,
                                  horizontal_position, position_convention)
        SELECT run_number, 0, start_time, end_time, linear_position,
               horizontal_position, '{LEGACY_CONVENTION}'
        FROM runs
    """)

    # 3. run_pixels: say what the columns actually hold, and attach them to
    #    a segment rather than straight to the run.
    op.drop_constraint('run_pixels_run_id_fkey', 'run_pixels', type_='foreignkey')
    op.drop_constraint('run_pixels_pixel_id_fkey', 'run_pixels', type_='foreignkey')
    op.drop_constraint('run_pixels_run_id_pixel_id_key', 'run_pixels', type_='unique')
    op.drop_constraint('run_pixels_run_id_board_channel_key', 'run_pixels',
                       type_='unique')
    op.drop_index('ix_run_pixels_run_id', table_name='run_pixels')
    op.drop_index('ix_run_pixels_pixel_id', table_name='run_pixels')

    op.alter_column('run_pixels', 'run_id', new_column_name='run_number')
    op.alter_column('run_pixels', 'pixel_id', new_column_name='pixel_number')
    op.add_column('run_pixels',
                  sa.Column('segment_index', sa.Integer(), nullable=False,
                            server_default='0'))
    # The default was only needed to backfill existing rows into their run's
    # only segment; new rows must say which segment they belong to.
    op.alter_column('run_pixels', 'segment_index', server_default=None)

    op.create_index('ix_run_pixels_run_number', 'run_pixels', ['run_number'])
    op.create_index('ix_run_pixels_segment_index', 'run_pixels',
                    ['segment_index'])
    op.create_index('ix_run_pixels_pixel_number', 'run_pixels',
                    ['pixel_number'])
    op.create_unique_constraint(
        'run_pixels_run_number_segment_index_pixel_number_key', 'run_pixels',
        ['run_number', 'segment_index', 'pixel_number'])
    op.create_unique_constraint(
        'run_pixels_run_number_segment_index_board_channel_key', 'run_pixels',
        ['run_number', 'segment_index', 'board_channel'])
    op.create_foreign_key('run_pixels_pixel_number_fkey', 'run_pixels',
                          'pixels', ['pixel_number'], ['pixel_number'])
    op.create_foreign_key(
        'run_pixels_segment_fkey', 'run_pixels', 'run_segments',
        ['run_number', 'segment_index'], ['run_number', 'segment_index'])

    # 4. Positions now live on the segment, not the run.
    op.drop_column('runs', 'linear_position')
    op.drop_column('runs', 'horizontal_position')


def downgrade() -> None:
    op.add_column('runs', sa.Column('linear_position', sa.Double(),
                                    nullable=True))
    op.add_column('runs', sa.Column('horizontal_position', sa.Double(),
                                    nullable=True))
    # Only segment 0's position can be represented on the run itself; a
    # multi-position run cannot round-trip through the old schema.
    op.execute("""
        UPDATE runs r
        SET linear_position = s.linear_position,
            horizontal_position = s.horizontal_position
        FROM run_segments s
        WHERE s.run_number = r.run_number AND s.segment_index = 0
    """)

    op.drop_constraint('run_pixels_segment_fkey', 'run_pixels',
                       type_='foreignkey')
    op.drop_constraint('run_pixels_pixel_number_fkey', 'run_pixels',
                       type_='foreignkey')
    op.drop_constraint('run_pixels_run_number_segment_index_pixel_number_key',
                       'run_pixels', type_='unique')
    op.drop_constraint(
        'run_pixels_run_number_segment_index_board_channel_key', 'run_pixels',
        type_='unique')
    op.drop_index('ix_run_pixels_run_number', table_name='run_pixels')
    op.drop_index('ix_run_pixels_segment_index', table_name='run_pixels')
    op.drop_index('ix_run_pixels_pixel_number', table_name='run_pixels')

    # Rows from segments other than 0 have nowhere to go in the old schema.
    op.execute("DELETE FROM run_pixels WHERE segment_index <> 0")
    op.drop_column('run_pixels', 'segment_index')
    op.alter_column('run_pixels', 'run_number', new_column_name='run_id')
    op.alter_column('run_pixels', 'pixel_number', new_column_name='pixel_id')

    op.create_index('ix_run_pixels_run_id', 'run_pixels', ['run_id'])
    op.create_index('ix_run_pixels_pixel_id', 'run_pixels', ['pixel_id'])
    op.create_unique_constraint('run_pixels_run_id_pixel_id_key', 'run_pixels',
                                ['run_id', 'pixel_id'])
    op.create_unique_constraint('run_pixels_run_id_board_channel_key',
                                'run_pixels', ['run_id', 'board_channel'])
    op.create_foreign_key('run_pixels_run_id_fkey', 'run_pixels', 'runs',
                          ['run_id'], ['run_number'])
    op.create_foreign_key('run_pixels_pixel_id_fkey', 'run_pixels', 'pixels',
                          ['pixel_id'], ['pixel_number'])

    op.drop_table('run_segments')
