"""run and pixel natural primary keys

Makes runs.run_number and pixels.pixel_number the primary keys, replacing
the surrogate id columns, since every query is by run number / pixel
number and neither is ever reused. run_pixels.run_id / pixel_id keep
their column names but now hold run_number / pixel_number values
directly instead of opaque surrogate ids.

Revision ID: c3047361f2b1
Revises: 11d04c5fc2b9
Create Date: 2026-07-28 14:42:08.833273

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3047361f2b1'
down_revision: Union[str, None] = '11d04c5fc2b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Drop the FKs that point at the old surrogate ids, so both the
    #    surrogate and natural columns are free to change. Also drop the
    #    (run_id, pixel_id) unique constraint: Postgres checks a
    #    non-deferrable unique constraint after every row of an UPDATE,
    #    and mid-remap a row already converted to its new pixel_number
    #    can collide with a not-yet-converted row whose OLD surrogate id
    #    happens to equal that same number. Recreated once both columns
    #    hold consistent natural-key values.
    op.drop_constraint('run_pixels_run_id_fkey', 'run_pixels', type_='foreignkey')
    op.drop_constraint('run_pixels_pixel_id_fkey', 'run_pixels', type_='foreignkey')
    op.drop_constraint('run_pixels_run_id_pixel_id_key', 'run_pixels', type_='unique')

    # 2. Remap run_pixels.run_id / pixel_id from surrogate id to natural
    #    key, while both columns still exist to read from.
    op.execute("""
        UPDATE run_pixels rp
        SET run_id = r.run_number
        FROM runs r
        WHERE rp.run_id = r.id
    """)
    op.execute("""
        UPDATE run_pixels rp
        SET pixel_id = p.pixel_number
        FROM pixels p
        WHERE rp.pixel_id = p.id
    """)
    op.create_unique_constraint('run_pixels_run_id_pixel_id_key', 'run_pixels',
                                ['run_id', 'pixel_id'])

    # 3. Swap each table's primary key: drop the surrogate id (and its
    #    now-redundant unique index), promote the natural key (already
    #    unique and NOT NULL).
    op.drop_constraint('runs_pkey', 'runs', type_='primary')
    op.drop_index('ix_runs_run_number', table_name='runs')
    op.drop_column('runs', 'id')
    op.create_primary_key('runs_pkey', 'runs', ['run_number'])

    op.drop_constraint('pixels_pkey', 'pixels', type_='primary')
    op.drop_index('ix_pixels_pixel_number', table_name='pixels')
    op.drop_column('pixels', 'id')
    op.create_primary_key('pixels_pkey', 'pixels', ['pixel_number'])

    # 4. Re-point the FKs at the new natural-key primary keys.
    op.create_foreign_key('run_pixels_run_id_fkey', 'run_pixels', 'runs',
                          ['run_id'], ['run_number'])
    op.create_foreign_key('run_pixels_pixel_id_fkey', 'run_pixels', 'pixels',
                          ['pixel_id'], ['pixel_number'])


def downgrade() -> None:
    op.drop_constraint('run_pixels_run_id_fkey', 'run_pixels', type_='foreignkey')
    op.drop_constraint('run_pixels_pixel_id_fkey', 'run_pixels', type_='foreignkey')
    # See upgrade(): avoid the same mid-remap uniqueness collision.
    op.drop_constraint('run_pixels_run_id_pixel_id_key', 'run_pixels', type_='unique')

    op.drop_constraint('runs_pkey', 'runs', type_='primary')
    op.add_column('runs', sa.Column('id', sa.Integer(), autoincrement=True, nullable=True))
    op.execute("""
        CREATE SEQUENCE IF NOT EXISTS runs_id_seq OWNED BY runs.id;
        UPDATE runs SET id = nextval('runs_id_seq') WHERE id IS NULL;
        ALTER TABLE runs ALTER COLUMN id SET NOT NULL;
        ALTER TABLE runs ALTER COLUMN id SET DEFAULT nextval('runs_id_seq');
    """)
    op.create_primary_key('runs_pkey', 'runs', ['id'])
    op.create_index('ix_runs_run_number', 'runs', ['run_number'], unique=True)

    op.drop_constraint('pixels_pkey', 'pixels', type_='primary')
    op.add_column('pixels', sa.Column('id', sa.Integer(), autoincrement=True, nullable=True))
    op.execute("""
        CREATE SEQUENCE IF NOT EXISTS pixels_id_seq OWNED BY pixels.id;
        UPDATE pixels SET id = nextval('pixels_id_seq') WHERE id IS NULL;
        ALTER TABLE pixels ALTER COLUMN id SET NOT NULL;
        ALTER TABLE pixels ALTER COLUMN id SET DEFAULT nextval('pixels_id_seq');
    """)
    op.create_primary_key('pixels_pkey', 'pixels', ['id'])
    op.create_index('ix_pixels_pixel_number', 'pixels', ['pixel_number'], unique=True)

    op.execute("""
        UPDATE run_pixels rp
        SET run_id = r.id
        FROM runs r
        WHERE rp.run_id = r.run_number
    """)
    op.execute("""
        UPDATE run_pixels rp
        SET pixel_id = p.id
        FROM pixels p
        WHERE rp.pixel_id = p.pixel_number
    """)
    op.create_unique_constraint('run_pixels_run_id_pixel_id_key', 'run_pixels',
                                ['run_id', 'pixel_id'])

    op.create_foreign_key('run_pixels_run_id_fkey', 'run_pixels', 'runs',
                          ['run_id'], ['id'])
    op.create_foreign_key('run_pixels_pixel_id_fkey', 'run_pixels', 'pixels',
                          ['pixel_id'], ['id'])
