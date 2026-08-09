"""One-time backfill: clear pre-existing malformed event ticket_url/image_url.

Role: Called by Alembic migration 0009 (and testable without Alembic). Runs
_validate_absolute_http_url over every stored Event.ticket_url and
Event.image_url so rows written before scrape-time URL validation existed are
brought to the same absolute-http(s)-or-NULL invariant every newly scraped row now
satisfies.

Why a backfill is required rather than optional here — this differs from the
description case, and is worse. Scrape-time validation only reaches a row when a
scraper rewrites it with a non-null value, and the scrape manager merges these two
columns with `existing.ticket_url = se.ticket_url or existing.ticket_url`
(app/scrapers/manager.py). So the moment validation starts returning None for a bad
scraped value, the `or` falls back to the stored value and *preserves* it — a legacy
`javascript:alert(1)` survives every subsequent re-scrape indefinitely rather than
ageing out at the 7-day hard delete the way an unsanitized description would.

The two consumers that gate does not cover are the reason this matters:
app/api/feeds.py reads the ORM column directly and never passes through
EventResponse's schema-layer validator, and the Backend-Service "On Tour" reader
consumes whatever the database holds. A control-character-bearing ticket_url is
additionally a live availability bug — icalendar asserts on an unescaped newline in
a content line, so one such row 500s the entire .ics feed.

This backfill composes correctly with the merge: once the stored value is NULL,
`se.ticket_url or existing.ticket_url` yields None and the field stays cleared.

Idempotent: _validate_absolute_http_url is stable on its own output (a value it
accepts validates to itself; a value it rejects becomes NULL and is then skipped by
the non-null filter), so re-running rewrites nothing the second time — safe if the
migration is re-applied on a restored DB.

Schema coupling: names only the (id, ticket_url, image_url) columns via a frozen
table literal — it must run against the schema at its migration's revision, so it
does NOT import the ORM models (that would enumerate columns added by later
migrations and break fresh-install chains). _validate_absolute_http_url is a pure
function with no schema binding, so importing it is safe.

Requires: a sync SQLAlchemy Connection (op.get_bind() inside a migration; tests
adapt via AsyncConnection.run_sync).
"""
import logging

import sqlalchemy as sa
from sqlalchemy import bindparam, or_, select, update
from sqlalchemy.engine import Connection

from app.scrapers.base import _validate_absolute_http_url

logger = logging.getLogger(__name__)

# Frozen at the migration-0009 schema — names only what the backfill reads/writes.
_events = sa.table(
    "events",
    sa.column("id", sa.Integer),
    sa.column("ticket_url", sa.String),
    sa.column("image_url", sa.String),
)


def sanitize_existing_urls(conn: Connection) -> int:
    """Clear every stored event URL that isn't an absolute http(s) URL. Returns rows changed.

    Selects rows where either column is non-null, revalidates both, and writes back
    only those rows where at least one value actually changes. A row with one bad
    column and one good one counts once and keeps the good value.

    Deliberately leaves updated_at untouched (the frozen table literal carries no
    onupdate hook): clearing an unusable URL is a safety rewrite, not new content,
    and a mass bump would force every incremental-sync consumer into a full refetch.
    The web client reloads the full list each time, so it sees the cleared values
    regardless.
    """
    rows = conn.execute(
        select(_events.c.id, _events.c.ticket_url, _events.c.image_url).where(
            or_(_events.c.ticket_url.isnot(None), _events.c.image_url.isnot(None))
        )
    ).all()

    params = []
    for row in rows:
        ticket = _validate_absolute_http_url(row.ticket_url)
        image = _validate_absolute_http_url(row.image_url)
        if ticket != row.ticket_url or image != row.image_url:
            params.append({"b_id": row.id, "b_ticket_url": ticket, "b_image_url": image})

    if params:
        conn.execute(
            update(_events)
            .where(_events.c.id == bindparam("b_id"))
            .values(ticket_url=bindparam("b_ticket_url"), image_url=bindparam("b_image_url")),
            params,
        )
    logger.info(
        f"sanitize_existing_urls: cleared a malformed URL on {len(params)} of "
        f"{len(rows)} rows carrying at least one URL"
    )
    return len(params)
