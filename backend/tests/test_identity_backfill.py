"""Backfill logic for the stable-identity migration (issue #8).

The Alembic migration delegates to app/services/identity_backfill.py so the
logic is testable here via the ORM harness without running Alembic. The
functions take a sync Connection (what op.get_bind() provides); tests reach
them through AsyncConnection.run_sync.

populate_source_keys must fill source_key/normalized_source_url for EVERY
existing row with the same precedence + audit gate the scrape manager uses —
otherwise the first scrape after deploy matches nothing and duplicates every
event. merge_source_key_duplicates collapses rows that share an identity so
the composite unique index can be created.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.models import Event
from app.services.identity_backfill import merge_source_key_duplicates, populate_source_keys
from conftest import DEFAULT_EVENT_DATE as D


@pytest.fixture(autouse=True)
async def _pre_index_schema(session):
    """Simulate the mid-migration schema: the composite unique index does not
    exist yet when populate/merge run (migration 0004 creates it last), and the
    legacy unique constraint on hash is still PRESENT (it drops after the
    merge) — the merge must not transiently violate it."""
    await session.execute(text("DROP INDEX uq_events_venue_source_key"))
    await session.execute(text("ALTER TABLE events ADD CONSTRAINT uq_events_hash UNIQUE (hash)"))
    await session.commit()


async def _run_backfill(session, fn, **kwargs):
    conn = await session.connection()
    await conn.run_sync(lambda sync_conn: fn(sync_conn, **kwargs))
    await session.commit()


async def _assert_identity_index_creatable(session):
    """The migration's final step — creating the composite unique index — must
    succeed on the post-merge data or the whole migration rolls back."""
    await session.execute(
        text("CREATE UNIQUE INDEX uq_events_venue_source_key ON events (venue_id, source_key)")
    )
    await session.commit()


async def _all_events(session):
    session.expire_all()
    return (await session.execute(select(Event).order_by(Event.id))).scalars().all()


async def test_populate_derives_keys_by_precedence_and_audit_gate(session, make_venue, make_event):
    trusted = await make_venue(scraper_type="mec")
    untrusted = await make_venue(scraper_type="squarespace")

    with_ext = await make_event(venue=trusted, external_id="55", source_url="https://v.com/e/one/")
    with_url = await make_event(venue=trusted, source_url="https://v.com/e/two/?utm_source=x")
    bare = await make_event(venue=trusted)
    gated = await make_event(venue=untrusted, source_url="https://sq.com/events/three")
    # Legacy venuepilot rows minted "" and "None" external_ids; neither may
    # become an identity key during population.
    legacy = await make_event(venue=trusted, external_id="None", source_url="https://v.com/e/four/")

    await _run_backfill(session, populate_source_keys)

    events = {e.id: e for e in await _all_events(session)}
    assert events[with_ext.id].source_key == "ext:55"
    assert events[with_url.id].source_key == "url:/e/two"
    assert events[with_url.id].normalized_source_url == "/e/two"
    assert events[bare.id].source_key == f"hash:{bare.hash}"
    # Audit gate: squarespace URLs are not identity, but the normalized form is
    # still stored (it's a reconciliation column, not the winning tier).
    assert events[gated.id].source_key == f"hash:{gated.hash}"
    assert events[gated.id].normalized_source_url == "/events/three"
    assert events[legacy.id].source_key == "url:/e/four"
    assert events[legacy.id].external_id is None


async def test_merge_collapses_same_url_duplicates_keeping_oldest_id(session, make_venue, make_event):
    venue = await make_venue(scraper_type="mec")
    url = "https://v.com/event/juana-molina/"

    older = await make_event(venue=venue, name="Juana Molina", source_url=url, date=D)
    newer = await make_event(
        venue=venue, name="Juana Molina (Rescheduled)", source_url=url,
        date=D + timedelta(days=7), price_min=25.0,
    )

    await _run_backfill(session, populate_source_keys)
    await _run_backfill(session, merge_source_key_duplicates)

    events = await _all_events(session)
    assert len(events) == 1
    survivor = events[0]
    assert survivor.id == older.id  # oldest id survives
    assert survivor.name == "Juana Molina (Rescheduled)"  # newest content wins
    assert survivor.date == D + timedelta(days=7)
    assert survivor.price_min == 25.0
    assert survivor.source_key == "url:/event/juana-molina"
    await _assert_identity_index_creatable(session)


async def test_merge_clears_tombstone_when_any_duplicate_is_live(session, make_venue, make_event):
    # Cross-feature interaction with the vanished-events diff (issue #9): the
    # rename that created a phantom pair also made the OLD row vanish from
    # scrapes, so by migration time the oldest row — the merge survivor — is
    # exactly the one likely to carry a removed_at tombstone. Presence evidence
    # must win: if any row in the group is live, the merged event is live.
    from datetime import datetime

    venue = await make_venue(scraper_type="mec")
    url = "https://v.com/event/jessica-pratt/"
    older = await make_event(
        venue=venue, name="Jessica Pratt", source_url=url, date=D,
        removed_at=datetime(2026, 7, 1),
    )
    await make_event(
        venue=venue, name="Jessica Pratt (Rescheduled)", source_url=url,
        date=D + timedelta(days=7),
    )

    await _run_backfill(session, populate_source_keys)
    await _run_backfill(session, merge_source_key_duplicates)

    events = await _all_events(session)
    assert len(events) == 1
    assert events[0].id == older.id
    assert events[0].removed_at is None


async def test_merge_keeps_tombstone_when_all_duplicates_are_removed(session, make_venue, make_event):
    from datetime import datetime

    venue = await make_venue(scraper_type="mec")
    url = "https://v.com/event/chuquimamani-condori/"
    stamp = datetime(2026, 7, 1)
    older = await make_event(
        venue=venue, name="Chuquimamani-Condori", source_url=url, date=D,
        removed_at=stamp,
    )
    await make_event(
        venue=venue, name="Chuquimamani-Condori (Moved)", source_url=url,
        date=D + timedelta(days=7), removed_at=datetime(2026, 7, 3),
    )

    await _run_backfill(session, populate_source_keys)
    await _run_backfill(session, merge_source_key_duplicates)

    events = await _all_events(session)
    assert len(events) == 1
    assert events[0].id == older.id
    assert events[0].removed_at == stamp


async def test_merge_leaves_untrusted_venues_alone(session, make_venue, make_event):
    # squarespace rows share URLs legitimately-ambiguously; their keys are
    # hash-tier (distinct), so nothing merges even with identical source_urls.
    venue = await make_venue(scraper_type="squarespace")
    url = "https://sq.com/events/show"
    await make_event(venue=venue, name="Cat Power", source_url=url, date=D)
    await make_event(venue=venue, name="Nilüfer Yanya", source_url=url, date=D + timedelta(days=1))

    await _run_backfill(session, populate_source_keys)
    await _run_backfill(session, merge_source_key_duplicates)

    assert len(await _all_events(session)) == 2


async def test_merge_scopes_to_requested_venue(session, make_venue, make_event):
    venue_a = await make_venue(scraper_type="mec")
    venue_b = await make_venue(scraper_type="mec")
    venue_a_id, venue_b_id = venue_a.id, venue_b.id
    for venue in (venue_a, venue_b):
        await make_event(venue=venue, name="Stereolab", source_url="https://v.com/event/stereolab/", date=D)
        await make_event(venue=venue, name="Stereolab (Late Show)", source_url="https://v.com/event/stereolab/", date=D)

    await _run_backfill(session, populate_source_keys)
    await _run_backfill(session, merge_source_key_duplicates, venue_id=venue_a_id)

    events = await _all_events(session)
    assert len([e for e in events if e.venue_id == venue_a_id]) == 1
    assert len([e for e in events if e.venue_id == venue_b_id]) == 2


async def test_listing_page_urls_never_become_identity_in_backfill(session, make_venue, make_event):
    # Stored rows scraped before the part-1 fixes can carry the venue's shared
    # listing page as source_url. The audit verdict describes the FIXED scraper,
    # not this legacy data — treating the listing URL as identity would merge
    # every event on the page into one row (explicit issue #8 constraint).
    venue = await make_venue(
        scraper_type="mec", scraper_config={"url": "https://venue.com/events/"}
    )
    a = await make_event(venue=venue, name="Juana Molina", source_url="https://venue.com/events/", date=D)
    b = await make_event(
        venue=venue, name="Hermanos Gutiérrez", source_url="https://venue.com/events/",
        date=D + timedelta(days=1),
    )

    await _run_backfill(session, populate_source_keys)
    await _run_backfill(session, merge_source_key_duplicates)

    events = await _all_events(session)
    assert len(events) == 2  # distinct events survive
    assert all(e.source_key == f"hash:{e.hash}" for e in events)
    assert all(e.normalized_source_url is None for e in events)
    await _assert_identity_index_creatable(session)


async def test_populate_does_not_stamp_updated_at(session, make_venue, make_event):
    # Gaining identity metadata is not a content change; a mass updated_at bump
    # would force every incremental-sync consumer into a full refetch.
    event = await make_event(source_url="https://v.com/event/one/")
    before = event.updated_at

    await _run_backfill(session, populate_source_keys)

    events = await _all_events(session)
    assert events[0].updated_at == before
