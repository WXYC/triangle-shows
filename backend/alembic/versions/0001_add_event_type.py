"""Alembic migration 0001: adds an event_type column to the events table.

Role: First migration in the chain (down_revision = None); run automatically by
Alembic when the app calls alembic upgrade head on startup or via the CLI.
Requires: A live PostgreSQL database reachable via the DATABASE_URL env var and
the Alembic env configured in backend/alembic/env.py.
"""

# --- Alembic imports ---
from alembic import op
import sqlalchemy as sa

# --- Revision metadata ---
# These values are read by Alembic to build the migration graph.
revision = '0001'
down_revision = None   # This is the root migration — no predecessor
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable event_type column to the events table."""
    # nullable=True so existing rows are unaffected (no backfill needed)
    op.add_column('events', sa.Column('event_type', sa.String(100), nullable=True))


def downgrade() -> None:
    """Remove the event_type column, reverting this migration."""
    op.drop_column('events', 'event_type')
