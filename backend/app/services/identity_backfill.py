"""
One-time backfill logic for the stable-identity migration (issue #8).

Role: Called by Alembic migration 0004 (and testable without Alembic). Populates
source_key + normalized_source_url for every pre-existing event row using the
same precedence and audit gate the scrape manager applies, then merges rows that
turn out to share an identity so the composite (venue_id, source_key) unique
index can be created. Kept out of the migration file so the harness can exercise
it against the ORM schema directly.

Schema coupling: this module deliberately does NOT import the ORM models. A
migration must run against the schema as it existed at revision 0004 — binding
to live model metadata would make `select()` enumerate columns added by LATER
migrations and break fresh installs running the chain. The frozen table
literals below name exactly the columns revision 0004 knows about.

Requires: a sync SQLAlchemy Connection (what op.get_bind() provides inside a
migration; tests adapt via AsyncConnection.run_sync).
"""
import logging
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import bindparam, delete, func, select, update
from sqlalchemy.engine import Connection

from app.scrapers.identity import derive_source_key, normalize_source_url, url_identity_verdict

logger = logging.getLogger(__name__)

# Frozen at the revision-0004 schema — see module docstring before adding columns.
_events = sa.table(
    "events",
    sa.column("id", sa.Integer),
    sa.column("venue_id", sa.Integer),
    sa.column("external_id", sa.String),
    sa.column("name", sa.String),
    sa.column("artist", sa.String),
    sa.column("support_artists", sa.Text),
    sa.column("date", sa.Date),
    sa.column("doors_time", sa.Time),
    sa.column("show_time", sa.Time),
    sa.column("ticket_url", sa.String),
    sa.column("price_min", sa.Float),
    sa.column("price_max", sa.Float),
    sa.column("image_url", sa.String),
    sa.column("genre", sa.String),
    sa.column("subgenre", sa.String),
    sa.column("status", sa.String),
    sa.column("age_restriction", sa.String),
    sa.column("description", sa.Text),
    sa.column("source_url", sa.String),
    sa.column("normalized_source_url", sa.String),
    sa.column("hash", sa.String),
    sa.column("source_key", sa.String),
    sa.column("updated_at", sa.DateTime),
)
_venues = sa.table(
    "venues",
    sa.column("id", sa.Integer),
    sa.column("scraper_type", sa.String),
    sa.column("scraper_config", sa.JSON),
)

# Columns whose values transfer to the surviving row when duplicates merge.
# "Newest content wins, null falls back to older" — a rename/reschedule duplicate
# pair keeps the freshest name/date while never blanking previously-good data.
# source_key is NOT here: it is the grouping key, identical across the group.
_MERGE_COLUMNS = (
    "external_id", "name", "artist", "support_artists", "date", "doors_time",
    "show_time", "ticket_url", "price_min", "price_max", "image_url", "genre",
    "subgenre", "status", "age_restriction", "description", "source_url",
    "normalized_source_url", "hash",
)


def _clean_external_id(raw: Optional[str]) -> Optional[str]:
    """Scrub legacy junk ids: '' (absent) and 'None' (str(None) of a JSON null).

    Either value, used as an identity key, would reconcile every event at the
    venue onto one row (see venuepilot fix in part 1 of issue #8).
    """
    if raw is None or not raw.strip() or raw == "None":
        return None
    return raw


def _venue_listing_norms(conn: Connection) -> dict[int, str]:
    """Normalized form of each venue's configured listing-page URL.

    Stored rows scraped BEFORE the part-1 scraper fixes can carry the venue's
    shared listing page as source_url (the old mec/koka_booth behavior). That
    URL is shared by every event on the page — treating it as identity during
    the backfill would merge distinct events into one row. The audit verdict
    describes the FIXED scraper, not the legacy data, so the data needs its own
    guard.
    """
    norms: dict[int, str] = {}
    for row in conn.execute(select(_venues.c.id, _venues.c.scraper_config)):
        config = row.scraper_config if isinstance(row.scraper_config, dict) else {}
        norm = normalize_source_url(config.get("url"))
        if norm:
            norms[row.id] = norm
    return norms


def populate_source_keys(conn: Connection) -> int:
    """Fill source_key + normalized_source_url for ALL event rows.

    Uses the stored external_id/source_url/hash columns with the same precedence
    and per-scraper audit gate the scrape manager applies at upsert time. Must
    cover every row: any row left without a source_key would fail to match on
    the first post-deploy scrape and come back as a duplicate. Also persists the
    scrubbed external_id so legacy junk ids can't anchor future reconciliation,
    and demotes rows whose URL is the venue's own listing page (never identity —
    see _venue_listing_norms) to the hash tier with no normalized URL.

    Deliberately does not touch updated_at: gaining identity metadata is not a
    content change, and a mass bump would force every incremental-sync consumer
    into a full refetch.
    Returns the number of rows populated.
    """
    listing_norms = _venue_listing_norms(conn)
    rows = conn.execute(
        select(
            _events.c.id, _events.c.venue_id, _events.c.external_id,
            _events.c.source_url, _events.c.hash, _venues.c.scraper_type,
        ).join_from(_events, _venues, _events.c.venue_id == _venues.c.id, isouter=True)
    ).all()

    params = []
    for row in rows:
        ext = _clean_external_id(row.external_id)
        norm = normalize_source_url(row.source_url)
        if norm is not None and norm == listing_norms.get(row.venue_id):
            norm = None  # shared listing page — not identity, not a reconciliation key
        verdict = url_identity_verdict(row.scraper_type)  # None scraper_type → HASH_FALLBACK
        params.append({
            "b_id": row.id,
            "b_external_id": ext,
            "b_normalized": norm,
            "b_source_key": derive_source_key(ext, norm, row.hash, verdict),
        })

    if params:
        conn.execute(
            update(_events)
            .where(_events.c.id == bindparam("b_id"))
            .values(
                external_id=bindparam("b_external_id"),
                normalized_source_url=bindparam("b_normalized"),
                source_key=bindparam("b_source_key"),
            ),
            params,
        )
    logger.info(f"populate_source_keys: populated {len(params)} event rows")
    return len(params)


def merge_source_key_duplicates(conn: Connection, venue_id: Optional[int] = None) -> int:
    """Collapse rows sharing a (venue_id, source_key) identity: oldest id survives.

    Run AFTER populate_source_keys and BEFORE creating the composite unique
    index. The audit gate is already baked into the keys — url: keys exist only
    for TRUSTED scrapers (and never for listing-page URLs, which populate
    demotes), so shared URLs never merge; hash keys were DB-unique before this
    migration, so hash-tier rows never group. What merges: same-URL
    rename/reschedule phantom pairs at trusted venues, and same-external_id
    duplicates. Newest content wins per column, nulls fall back to older values,
    the loser rows are deleted (BEFORE the survivor update — the survivor
    inherits the newest hash, and the legacy unique constraint on hash is still
    in place at migration time). updated_at IS stamped explicitly on the
    survivor — its content actually changed, and the frozen table literal
    carries no onupdate hook. Pass venue_id to restrict a recovery re-run to one
    venue. Returns the number of rows deleted.
    """
    dupe_groups = select(_events.c.venue_id, _events.c.source_key)
    if venue_id is not None:
        dupe_groups = dupe_groups.where(_events.c.venue_id == venue_id)
    dupe_groups = dupe_groups.group_by(_events.c.venue_id, _events.c.source_key).having(func.count() > 1)

    deleted = 0
    for group in conn.execute(dupe_groups).all():
        rows = conn.execute(
            select(_events)
            .where(_events.c.venue_id == group.venue_id, _events.c.source_key == group.source_key)
            .order_by(_events.c.id)
        ).mappings().all()

        survivor, losers = rows[0], rows[1:]
        merged = {col: survivor[col] for col in _MERGE_COLUMNS}
        for row in losers:  # ascending id: later (newer) rows overlay earlier ones
            for col in _MERGE_COLUMNS:
                if row[col] is not None:
                    merged[col] = row[col]

        conn.execute(delete(_events).where(_events.c.id.in_([row["id"] for row in losers])))
        conn.execute(
            update(_events)
            .where(_events.c.id == survivor["id"])
            .values(updated_at=datetime.utcnow(), **merged)
        )
        deleted += len(losers)
        logger.info(
            f"merge_source_key_duplicates: venue {group.venue_id} key {group.source_key!r} — "
            f"kept id {survivor['id']}, deleted {[row['id'] for row in losers]}"
        )
    return deleted
