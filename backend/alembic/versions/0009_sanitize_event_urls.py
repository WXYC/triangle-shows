"""Alembic migration 0009: clear malformed event ticket_url/image_url.

Role: Data-only migration (no schema change). Runs _validate_absolute_http_url over
every stored events.ticket_url and events.image_url via
app/services/url_backfill.py, NULLing rows written before scrape-time URL validation
existed so they meet the same absolute-http(s)-or-NULL invariant every newly scraped
row now satisfies.

Why this cannot wait for re-scraping, unlike most ingestion-time hardening: the
scrape manager merges these two columns with `existing.ticket_url = se.ticket_url or
existing.ticket_url`, so once validation nulls the fresh value the `or` falls back to
the stored one and preserves a legacy `javascript:alert(1)` indefinitely. It never
ages out. app/api/feeds.py reads the column directly (never through EventResponse's
schema-layer gate) and emits it as an iCalendar URL property, so those rows keep
flowing into the served .ics — and a control-character-bearing one 500s the whole
feed, since icalendar asserts on an unescaped newline in a content line.

This is the same division of labour migration 0006 has with clean_description:
scrape-time sanitization owns new writes, the backfill owns the rows already stored.
Idempotent — safe to re-run.

On a fresh database this runs before any event exists and is a no-op; on an existing
database it runs once, in the same transactional-DDL upgrade as the rest of the
chain.

Downgrade is a no-op: the original malformed values are not retained (and restoring
them would reintroduce what this migration removed).

Requires: A live PostgreSQL database reachable via DATABASE_URL; imports app code
(url_backfill), which alembic/env.py's sys.path setup makes available.
"""

from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Imported here (not module level) so `alembic history` etc. don't need app deps.
    from app.services.url_backfill import sanitize_existing_urls

    sanitize_existing_urls(op.get_bind())


def downgrade() -> None:
    # Irreversible: the pre-validation malformed URLs were not retained, and
    # restoring them would reintroduce what this migration removed.
    pass
