"""Alembic migration 0003: vanished-event tombstones (issue #9).

Role: Adds events.removed_at (nullable soft tombstone: when the venue stopped
advertising the event) and the event_miss_state table (consecutive-miss
bookkeeping for the scrape snapshot diff, kept off the events row so
events.updated_at only moves on client-visible changes). The FK cascades at the
database level because the nightly 7-day cleanup deletes events via Core
delete(), which bypasses ORM cascades.
Requires: A live PostgreSQL database reachable via DATABASE_URL.
"""

from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('events', sa.Column('removed_at', sa.DateTime(), nullable=True))

    op.create_table(
        'event_miss_state',
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('miss_count', sa.Integer(), nullable=False),
        sa.Column('last_miss_date', sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('event_id'),
    )


def downgrade() -> None:
    op.drop_table('event_miss_state')
    op.drop_column('events', 'removed_at')
