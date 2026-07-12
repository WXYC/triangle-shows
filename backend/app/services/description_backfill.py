"""One-time backfill: sanitize pre-existing event descriptions.

Role: Called by Alembic migration 0006 (and testable without Alembic). Runs
clean_description over every stored Event.description so rows written before
scrape-time sanitization existed are brought to the same safe-HTML subset the
web modal now renders as raw HTML (frontend/js/modal.js). Scrape-time
sanitization only reaches rows that are re-scraped; past and tombstoned rows are
not (see migration 0005's note), so without this backfill their unescaped markup
would keep reaching the innerHTML sink until they age out at the 7-day hard
delete. This closes that window deterministically at deploy for every row.

Idempotent: clean_description is stable on its own output, so re-running rewrites
nothing the second time — safe if the migration is re-applied on a restored DB.

Schema coupling: names only the (id, description) columns via a frozen table
literal — it must run against the schema at its migration's revision, so it does
NOT import the ORM models (that would enumerate columns added by later migrations
and break fresh-install chains). clean_description is a pure function with no
schema binding, so importing it is safe.

Requires: a sync SQLAlchemy Connection (op.get_bind() inside a migration; tests
adapt via AsyncConnection.run_sync).
"""
import logging

import sqlalchemy as sa
from sqlalchemy import bindparam, select, update
from sqlalchemy.engine import Connection

from app.scrapers.base import clean_description

logger = logging.getLogger(__name__)

# Frozen at the migration-0006 schema — names only what the backfill reads/writes.
_events = sa.table(
    "events",
    sa.column("id", sa.Integer),
    sa.column("description", sa.Text),
)


def sanitize_existing_descriptions(conn: Connection) -> int:
    """Re-sanitize every stored event description in place. Returns rows changed.

    Selects rows with a non-null description, runs clean_description over each,
    and writes back only those whose value actually changes (raw HTML -> safe
    subset, or empty markup -> NULL). Rows already at the safe subset sanitize to
    themselves and are skipped, so this is idempotent and cheap to re-run.

    Deliberately leaves updated_at untouched (the frozen table literal carries no
    onupdate hook): sanitizing stored markup is a safety rewrite, not new content,
    and a mass bump would force every incremental-sync consumer into a full
    refetch. The web client reloads the full list each time, so it sees the
    sanitized values regardless.
    """
    rows = conn.execute(
        select(_events.c.id, _events.c.description).where(_events.c.description.isnot(None))
    ).all()

    params = [
        {"b_id": row.id, "b_description": cleaned}
        for row in rows
        if (cleaned := clean_description(row.description)) != row.description
    ]

    if params:
        conn.execute(
            update(_events)
            .where(_events.c.id == bindparam("b_id"))
            .values(description=bindparam("b_description")),
            params,
        )
    logger.info(
        f"sanitize_existing_descriptions: rewrote {len(params)} of {len(rows)} "
        "non-null description rows"
    )
    return len(params)
