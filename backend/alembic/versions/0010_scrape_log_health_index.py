"""Alembic migration 0010: composite (venue_id, started_at DESC) index on scrape_logs.

Role: Serves the scrape-health evaluator (app/services/scrape_health.py, issue #86
part 1) — both the per-venue latest-N query and the 30-day baseline scan filter and
sort on started_at within a venue, and scrape_logs is never pruned, so the table
only grows. Mirrors the established pattern of an ORM __table_args__ Index paired
with the migration that creates it (migration 0004 / uq_events_venue_source_key):
the test harness builds schema from Base.metadata.create_all, never Alembic, so a
migration-only index would exist in no test database.

Requires: A live PostgreSQL database reachable via DATABASE_URL.
"""

from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        'ix_scrape_logs_venue_id_started_at',
        'scrape_logs',
        ['venue_id', sa.text('started_at DESC')],
    )


def downgrade() -> None:
    op.drop_index('ix_scrape_logs_venue_id_started_at', table_name='scrape_logs')
