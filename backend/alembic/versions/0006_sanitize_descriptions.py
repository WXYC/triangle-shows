"""Alembic migration 0006: sanitize existing event descriptions.

Role: Data-only migration (no schema change). Runs clean_description over every
stored events.description via app/services/description_backfill.py, rewriting
rows written before scrape-time sanitization existed down to the safe-HTML subset
the web modal now renders as raw HTML. Scrape-time sanitization only reaches
re-scraped rows; past and tombstoned rows are not (see 0005's note), so this
backfill is what closes the innerHTML-sink exposure for them deterministically at
deploy rather than waiting on the 7-day hard delete. Idempotent — safe to re-run.

On a fresh database this runs before any event exists and is a no-op; on an
existing database it runs once, in the same transactional-DDL upgrade as the rest
of the chain.

Downgrade is a no-op: the original raw HTML is not retained (and restoring
unsanitized markup would reintroduce the vulnerability).

Requires: A live PostgreSQL database reachable via DATABASE_URL; imports app code
(description_backfill), which alembic/env.py's sys.path setup makes available.
"""

from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Imported here (not module level) so `alembic history` etc. don't need app deps.
    from app.services.description_backfill import sanitize_existing_descriptions

    sanitize_existing_descriptions(op.get_bind())


def downgrade() -> None:
    # Irreversible: the pre-sanitization raw HTML was not retained, and restoring
    # it would reintroduce the unsafe markup this migration removed.
    pass
