"""Alembic migration 0002: no-op squash marker.

Role: Preserves the revision '0002' head so that existing deployed databases
(which already ran the original add/drop event_type pair) stay at a valid head
and don't re-run any DDL. The original 0001+0002 pair cancelled each other out
and has been consolidated into 0001_initial_schema.
"""

from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
