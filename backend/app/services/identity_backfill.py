"""
One-time backfill logic for the stable-identity migration (issue #8).

Role: Called by Alembic migration 0004 (and testable without Alembic). Populates
source_key + normalized_source_url for every pre-existing event row using the
same precedence and audit gate the scrape manager applies, then merges rows that
turn out to share an identity so the composite (venue_id, source_key) unique
index can be created. Kept out of the migration file so the harness can exercise
it against the ORM schema directly.
Requires: a sync SQLAlchemy Connection (what op.get_bind() provides inside a
migration; tests adapt via AsyncConnection.run_sync).
"""
import logging
from typing import Optional

from sqlalchemy import bindparam, delete, func, select, update
from sqlalchemy.engine import Connection

from app.models import Event, Venue
from app.scrapers.identity import derive_source_key, normalize_source_url, url_identity_verdict

logger = logging.getLogger(__name__)

_events = Event.__table__
_venues = Venue.__table__

# Columns whose values transfer to the surviving row when duplicates merge.
# "Newest content wins, null falls back to older" — a rename/reschedule duplicate
# pair keeps the freshest name/date while never blanking previously-good data.
_MERGE_COLUMNS = (
    "external_id", "name", "artist", "support_artists", "date", "doors_time",
    "show_time", "ticket_url", "price_min", "price_max", "image_url", "genre",
    "subgenre", "status", "age_restriction", "description", "source_url",
    "normalized_source_url", "hash", "source_key",
)


def _clean_external_id(raw: Optional[str]) -> Optional[str]:
    """Scrub legacy junk ids: '' (absent) and 'None' (str(None) of a JSON null).

    Either value, used as an identity key, would reconcile every event at the
    venue onto one row (see venuepilot fix in part 1 of issue #8).
    """
    if raw is None or not raw.strip() or raw == "None":
        return None
    return raw


def populate_source_keys(conn: Connection) -> int:
    """Fill source_key + normalized_source_url for ALL event rows.

    Uses the stored external_id/source_url/hash columns with the same precedence
    and per-scraper audit gate the scrape manager applies at upsert time. Must
    cover every row: any row left without a source_key would fail to match on
    the first post-deploy scrape and come back as a duplicate. Also persists the
    scrubbed external_id so legacy junk ids can't anchor future reconciliation.
    Returns the number of rows populated.
    """
    rows = conn.execute(
        select(
            _events.c.id, _events.c.external_id, _events.c.source_url,
            _events.c.hash, _venues.c.scraper_type,
        ).join_from(_events, _venues, _events.c.venue_id == _venues.c.id)
    ).all()

    params = []
    for row in rows:
        ext = _clean_external_id(row.external_id)
        norm = normalize_source_url(row.source_url)
        verdict = url_identity_verdict(row.scraper_type)
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
    for TRUSTED scrapers, so untrusted venues' shared URLs never merge; hash keys
    were DB-unique before this migration, so hash-tier rows never group. What
    merges: same-URL rename/reschedule phantom pairs at trusted venues, and
    same-external_id duplicates. Newest content wins per column, nulls fall back
    to older values, the loser rows are deleted. Pass venue_id to restrict a
    recovery re-run to one venue. Returns the number of rows deleted.
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

        # Delete losers BEFORE updating the survivor: the survivor inherits the
        # newest row's hash, and at migration time the legacy unique constraint
        # on hash is still in place (it drops after this merge runs).
        conn.execute(delete(_events).where(_events.c.id.in_([row["id"] for row in losers])))
        conn.execute(update(_events).where(_events.c.id == survivor["id"]).values(**merged))
        deleted += len(losers)
        logger.info(
            f"merge_source_key_duplicates: venue {group.venue_id} key {group.source_key!r} — "
            f"kept id {survivor['id']}, deleted {[row['id'] for row in losers]}"
        )
    return deleted
