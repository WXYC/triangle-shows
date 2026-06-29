"""
Alembic migration 0002: drops the event_type column from the events table.

Role: Runs as part of the Alembic migration chain (after 0001). Applied automatically
on app startup via alembic upgrade head in main.py, or manually with `alembic upgrade 0002`.
Requires: A live database connection configured via DATABASE_URL in .env.
"""

# --- Alembic Migration: drop event_type ---
# event_type was removed from the Event model; this migration cleans it from the schema.
from alembic import op

# --- Revision Metadata ---
# Alembic uses these to order and chain migrations correctly.
revision = '0002'
down_revision = '0001'  # This migration depends on 0001 having been applied first.
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Remove the event_type column — it is no longer part of the Event model."""
    op.drop_column('events', 'event_type')


def downgrade() -> None:
    """Re-add event_type as a nullable string column to restore pre-0002 schema."""
    # sqlalchemy is imported here (not at module level) to keep the migration lightweight
    # when only upgrade() is called.
    import sqlalchemy as sa
    op.add_column('events', sa.Column('event_type', sa.String(100), nullable=True))
