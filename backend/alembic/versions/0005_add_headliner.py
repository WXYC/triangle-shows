"""Alembic migration 0005: best-effort clean headliner (issue #18).

Role: Adds events.headliner — the nullable cleaned performer (support acts,
ticketing tags, and framing stripped) that GET /api/v1/events exposes alongside
the untouched name/artist. No data backfill: the derivation heuristic lives in
Python (app/scrapers/headliner.py), and the scrape manager repopulates every
live event's row on its next scrape cycle. Rows that are never rescraped
(tombstoned or past events) keep a null headliner by design — the field is
documented best-effort and consumers fall back to their own extraction.
Requires: A live PostgreSQL database reachable via DATABASE_URL.
"""

from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('events', sa.Column('headliner', sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'headliner')
