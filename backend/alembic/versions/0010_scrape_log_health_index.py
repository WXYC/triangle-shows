"""Alembic migration 0010: composite (venue_id, started_at DESC) index on scrape_logs.

Role: Serves the scrape-health evaluator (app/services/scrape_health.py, issue #86
part 1) — both the per-venue latest-N query and the 30-day baseline scan filter and
sort on started_at within a venue, and scrape_logs is never pruned, so the table
only grows. Mirrors the established pattern of an ORM __table_args__ Index paired
with the migration that creates it (migration 0004 / uq_events_venue_source_key):
the test harness builds schema from Base.metadata.create_all, never Alembic, so a
migration-only index would exist in no test database.

Also drops the single-column ix_scrape_logs_venue_id (created by migration 0001):
the new composite covers venue_id as its leading column, so the old index is dead
weight on an append-only table. downgrade() recreates it BY NAME — migration 0001
created it under that exact name, and this repo runs migrations in-process at
container boot (main.py), so a downgrade that can't reproduce the original name is
a production problem, not a cosmetic one.

Requires: A live PostgreSQL database reachable via DATABASE_URL.
"""

from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF EXISTS, not a bare drop_index: migrations run in-process during lifespan
    # startup (main.py), so an UndefinedObject here is not a failed migration, it is a
    # container that will not boot. The index is absent in any database whose schema
    # came from Base.metadata.create_all rather than the migration chain -- the shape
    # tests/conftest.py builds -- and in any database where it was dropped by hand.
    # Being already-gone is exactly the state this step wants; it should not be fatal.
    op.execute('DROP INDEX IF EXISTS ix_scrape_logs_venue_id')
    op.create_index(
        'ix_scrape_logs_venue_id_started_at',
        'scrape_logs',
        ['venue_id', sa.text('started_at DESC')],
    )


def downgrade() -> None:
    op.drop_index('ix_scrape_logs_venue_id_started_at', table_name='scrape_logs')
    op.create_index('ix_scrape_logs_venue_id', 'scrape_logs', ['venue_id'])
