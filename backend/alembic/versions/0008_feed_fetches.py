"""Alembic migration 0008: feed_fetches table for server-side .ics telemetry.

Role: Purely additive — creates feed_fetches (see app/models.py::FeedFetch) to
record one append-only row per successfully served GET /feeds/events.ics. No
existing tables or rows are touched. The composite (fetched_at, client_hash) index
is the sole index: every report query range-scans fetched_at first (month window,
trailing 28 days) with COUNT(DISTINCT client_hash) on top, so a standalone
fetched_at index would be redundant.

Requires: A live PostgreSQL database reachable via DATABASE_URL.
"""

from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feed_fetches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("client_hash", sa.String(length=16), nullable=False),
        sa.Column("venue_filter", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_feed_fetches_fetched_at_client_hash",
        "feed_fetches",
        ["fetched_at", "client_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_feed_fetches_fetched_at_client_hash", table_name="feed_fetches")
    op.drop_table("feed_fetches")
