"""Alembic migration 0004: stable event identity (issue #8).

Role: Adds the source_key and normalized_source_url columns, populates them for
every existing row (same precedence + audit gate as the scrape manager, via
app/services/identity_backfill.py), merges rows that turn out to share an
identity (oldest id survives, newest content wins), drops hash uniqueness
(identity uniqueness moves to the composite (venue_id, source_key) index), and
creates that composite index LAST so it is guaranteed to succeed on merged data.

This is deliberately a single atomic migration, not a 0004/0005/0006 sequence:
Alembic runs upgrade() in one PostgreSQL transaction (transactional DDL), so a
failure at any step — including index creation on an unforeseen duplicate —
rolls the database back to 0003 with no partially-populated state to repair.

Downgrade caveat: restoring the unique constraint on hash fails if duplicate
hashes have accumulated post-migration (transient duplicates are legal under
the new scheme). Merge those rows manually before downgrading.

Requires: A live PostgreSQL database reachable via DATABASE_URL; imports app
code (identity_backfill), which alembic/env.py's sys.path setup makes available.
"""

from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('events', sa.Column('normalized_source_url', sa.String(1000), nullable=True))
    op.add_column('events', sa.Column('source_key', sa.String(1100), nullable=True))
    op.create_index('ix_events_normalized_source_url', 'events', ['normalized_source_url'])

    # Imported here (not module level) so `alembic history` etc. don't need app deps.
    from app.services.identity_backfill import merge_source_key_duplicates, populate_source_keys

    conn = op.get_bind()
    populate_source_keys(conn)
    merge_source_key_duplicates(conn)

    op.alter_column('events', 'source_key', nullable=False)
    op.drop_constraint('uq_events_hash', 'events', type_='unique')
    op.create_index('uq_events_venue_source_key', 'events', ['venue_id', 'source_key'], unique=True)


def downgrade() -> None:
    op.drop_index('uq_events_venue_source_key', table_name='events')
    # Fails on duplicate hashes accumulated post-migration — see module docstring.
    op.create_unique_constraint('uq_events_hash', 'events', ['hash'])
    op.drop_index('ix_events_normalized_source_url', table_name='events')
    op.drop_column('events', 'source_key')
    op.drop_column('events', 'normalized_source_url')
