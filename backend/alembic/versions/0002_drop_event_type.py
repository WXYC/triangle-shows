"""drop event_type column from events

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-26

"""
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column('events', 'event_type')


def downgrade() -> None:
    import sqlalchemy as sa
    op.add_column('events', sa.Column('event_type', sa.String(100), nullable=True))
