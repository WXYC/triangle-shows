"""add event_type column to events

Revision ID: 0001
Revises:
Create Date: 2026-02-26

"""
from alembic import op
import sqlalchemy as sa

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('events', sa.Column('event_type', sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'event_type')
