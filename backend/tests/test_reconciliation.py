"""Reconciliation behavior for stable event identity (issue #8).

The scrape manager matches incoming events to existing rows per-venue by
precedence — external_id, then normalized source_url (audit-trusted scrapers
only), then content hash — so renames and reschedules become in-place updates
instead of new-row-plus-orphan. These tests exercise the public upsert path
against real PostgreSQL via the conftest factories.

Venue scraper_type choices are load-bearing: "mec" carries a TRUSTED URL
verdict, "manual" is unregistered and therefore HASH_FALLBACK.
"""

from datetime import timedelta

from sqlalchemy import select

from app.models import Event
from app.scrapers.base import ScrapedEvent
from app.scrapers.manager import ScrapeManager
from conftest import DEFAULT_EVENT_DATE as D


def _scraped(venue_slug: str, **overrides) -> ScrapedEvent:
    fields = dict(
        name="Juana Molina",
        artist="Juana Molina",
        date=D,
        venue_slug=venue_slug,
        source="test",
        price_min=10.0,
    )
    fields.update(overrides)
    return ScrapedEvent(**fields)


async def _all_events(session):
    return (await session.execute(select(Event).order_by(Event.id))).scalars().all()


async def test_reschedule_updates_row_in_place_for_url_trusted_venue(session, make_venue):
    venue = await make_venue(scraper_type="mec")
    manager = ScrapeManager(session)
    url = "https://venue.com/event/juana-molina/"

    await manager._upsert_events(venue.id, [_scraped(venue.slug, source_url=url)])
    await session.commit()

    # The venue reschedules the show: same event page, new date.
    created, updated = await manager._upsert_events(
        venue.id, [_scraped(venue.slug, source_url=url, date=D + timedelta(days=7))]
    )
    await session.commit()

    assert (created, updated) == (0, 1)
    events = await _all_events(session)
    assert len(events) == 1  # no new row, no orphan
    assert events[0].date == D + timedelta(days=7)


async def test_rename_updates_row_and_keeps_source_key(session, make_venue):
    venue = await make_venue(scraper_type="mec")
    manager = ScrapeManager(session)
    url = "https://venue.com/event/juana-molina/"

    await manager._upsert_events(venue.id, [_scraped(venue.slug, source_url=url)])
    await session.commit()
    original_key = (await _all_events(session))[0].source_key

    created, updated = await manager._upsert_events(
        venue.id, [_scraped(venue.slug, source_url=url, name="Juana Molina y Amigos")]
    )
    await session.commit()

    assert (created, updated) == (0, 1)
    events = await _all_events(session)
    assert len(events) == 1
    assert events[0].name == "Juana Molina y Amigos"
    # The url: key is rename-stable — that's the external contract.
    assert events[0].source_key == original_key == "url:/event/juana-molina"


async def test_external_id_reconciles_even_when_url_changes(session, make_venue):
    venue = await make_venue(scraper_type="venuepilot")
    manager = ScrapeManager(session)

    await manager._upsert_events(
        venue.id,
        [_scraped(venue.slug, external_id="39482", source_url="https://tix.com/old")],
    )
    await session.commit()

    created, updated = await manager._upsert_events(
        venue.id,
        [_scraped(venue.slug, external_id="39482", source_url="https://tix.com/new", price_min=15.0)],
    )
    await session.commit()

    assert (created, updated) == (0, 1)
    events = await _all_events(session)
    assert len(events) == 1
    assert events[0].source_key == "ext:39482"
    assert events[0].source_url == "https://tix.com/new"


async def test_tier_transition_url_to_external_id_keeps_row(session, make_venue):
    venue = await make_venue(scraper_type="mec")
    manager = ScrapeManager(session)
    url = "https://venue.com/event/jessica-pratt/"

    await manager._upsert_events(venue.id, [_scraped(venue.slug, name="Jessica Pratt", source_url=url)])
    await session.commit()
    row_id = (await _all_events(session))[0].id

    # The scraper starts supplying an external_id for an event previously keyed
    # by URL. Matching goes through the per-tier columns, so the same row is
    # found and its source_key migrates to the ext: tier.
    created, updated = await manager._upsert_events(
        venue.id,
        [_scraped(venue.slug, name="Jessica Pratt", source_url=url, external_id="777")],
    )
    await session.commit()

    assert (created, updated) == (0, 1)
    events = await _all_events(session)
    assert len(events) == 1
    assert events[0].id == row_id
    assert events[0].source_key == "ext:777"


async def test_hash_fallback_venue_keeps_content_hash_identity(session, make_venue):
    # "manual" is not in the scraper registry → HASH_FALLBACK. The URL must not
    # anchor identity: a rename produces a new row (today's behavior, documented
    # in the source_key contract as hash-tier churn), never an in-place merge.
    venue = await make_venue(scraper_type="manual")
    manager = ScrapeManager(session)
    url = "https://venue.com/event/chuquimamani-condori/"

    await manager._upsert_events(venue.id, [_scraped(venue.slug, source_url=url)])
    await session.commit()
    first = (await _all_events(session))[0]
    assert first.source_key == f"hash:{first.hash}"

    created, updated = await manager._upsert_events(
        venue.id, [_scraped(venue.slug, source_url=url, name="Completely Different Name")]
    )
    await session.commit()

    assert (created, updated) == (1, 0)
    assert len(await _all_events(session)) == 2


async def test_batch_same_identity_same_date_collapses(session, make_venue):
    # A featured section + main listing (or an old/new time pair) is one event.
    venue = await make_venue(scraper_type="mec")
    manager = ScrapeManager(session)
    url = "https://venue.com/event/duke-ellington/"

    created, updated = await manager._upsert_events(
        venue.id,
        [
            _scraped(venue.slug, source_url=url, name="Duke Ellington Orchestra"),
            _scraped(venue.slug, source_url=url, name="Duke Ellington Orchestra (Evening)"),
        ],
    )
    await session.commit()

    assert (created, updated) == (1, 0)
    events = await _all_events(session)
    assert len(events) == 1
    assert events[0].name == "Duke Ellington Orchestra"  # first occurrence wins


async def test_batch_same_identity_different_dates_demotes_to_hash(session, make_venue):
    # A recurring series can share one URL across genuinely distinct occurrences;
    # collapsing (or URL-matching) them would drop or overwrite a real show.
    venue = await make_venue(scraper_type="mec")
    manager = ScrapeManager(session)
    url = "https://venue.com/event/csillagrablok/"

    created, updated = await manager._upsert_events(
        venue.id,
        [
            _scraped(venue.slug, source_url=url, name="Csillagrablók", date=D),
            _scraped(venue.slug, source_url=url, name="Csillagrablók", date=D + timedelta(days=1)),
        ],
    )
    await session.commit()

    assert (created, updated) == (2, 0)
    events = await _all_events(session)
    assert len(events) == 2
    # Neither row may claim the shared URL as identity.
    assert all(e.source_key.startswith("hash:") for e in events)


async def test_same_external_id_at_two_venues_stays_two_rows(session, make_venue):
    # VenuePilot ids are small integers that collide across venues running the
    # same platform — identity is per-venue, never global.
    venue_a = await make_venue(scraper_type="venuepilot")
    venue_b = await make_venue(scraper_type="venuepilot")
    manager = ScrapeManager(session)

    await manager._upsert_events(
        venue_a.id, [_scraped(venue_a.slug, external_id="42", name="Jessica Pratt")]
    )
    created, _ = await manager._upsert_events(
        venue_b.id, [_scraped(venue_b.slug, external_id="42", name="Jockstrap")]
    )
    await session.commit()

    assert created == 1
    events = await _all_events(session)
    assert len(events) == 2
    assert {e.source_key for e in events} == {"ext:42"}
    assert {e.venue_id for e in events} == {venue_a.id, venue_b.id}


async def test_duplicate_hashes_match_oldest_row_without_crashing(session, make_venue, make_event):
    # Duplicate hashes are legal now (uniqueness moved to (venue_id, source_key)).
    # When two rows share a hash — e.g. duplicates predating the migration — the
    # oldest row wins deterministically and the scrape does not crash.
    venue = await make_venue(scraper_type="manual")
    manager = ScrapeManager(session)
    se = _scraped(venue.slug)
    older = await make_event(venue=venue, name=se.name, date=se.date, hash=se.hash, source_key=f"hash:{se.hash}")
    newer = await make_event(venue=venue, name=se.name, date=se.date, hash=se.hash, source_key="url:/stale-dupe")

    created, updated = await manager._upsert_events(venue.id, [_scraped(venue.slug, price_min=99.0)])
    await session.commit()

    assert created == 0
    await session.refresh(older)
    await session.refresh(newer)
    assert older.price_min == 99.0
    assert newer.price_min != 99.0


async def test_cross_batch_shared_url_does_not_hijack_wrong_occurrence(session, make_venue):
    # Two occurrences sharing one URL are demoted in-batch. A LATER scrape that
    # lists only ONE occurrence must not url-match the oldest same-URL row and
    # rewrite the other occurrence in place — the shared URL stays non-identity
    # cross-batch, matching goes through the hash.
    venue = await make_venue(scraper_type="mec")
    manager = ScrapeManager(session)
    url = "https://venue.com/event/csillagrablok-run/"

    await manager._upsert_events(
        venue.id,
        [
            _scraped(venue.slug, source_url=url, name="Csillagrablók", date=D),
            _scraped(venue.slug, source_url=url, name="Csillagrablók", date=D + timedelta(days=1)),
        ],
    )
    await session.commit()

    created, updated = await manager._upsert_events(
        venue.id,
        [_scraped(venue.slug, source_url=url, name="Csillagrablók", date=D + timedelta(days=1), price_min=20.0)],
    )
    await session.commit()

    assert created == 0
    events = await _all_events(session)
    assert len(events) == 2
    by_date = {e.date: e for e in events}
    assert by_date[D].price_min != 20.0  # the D occurrence was not touched
    assert by_date[D + timedelta(days=1)].price_min == 20.0


async def test_forced_hash_key_prefers_row_already_holding_it(session, make_venue, make_event):
    # Duplicate hashes with mixed source_keys can exist transiently. Forcing
    # hash:<h> onto the OLDER row while a newer row already holds that key would
    # violate (venue_id, source_key) and fail the whole venue's scrape — hash
    # matching must prefer the row that already holds the hash-tier key.
    venue = await make_venue(scraper_type="mec")
    manager = ScrapeManager(session)
    se = _scraped(venue.slug, name="Jessica Pratt")
    older = await make_event(
        venue=venue, name=se.name, date=se.date, hash=se.hash,
        source_url="https://venue.com/event/jp/", normalized_source_url="/event/jp",
        source_key="url:/event/jp",
    )
    holder = await make_event(venue=venue, name=se.name, date=se.date, hash=se.hash, source_key=f"hash:{se.hash}")

    # A demotion batch (same URL, two dates) whose first member carries hash h.
    created, updated = await manager._upsert_events(
        venue.id,
        [
            _scraped(venue.slug, name="Jessica Pratt", source_url="https://venue.com/event/other/", price_min=42.0),
            _scraped(
                venue.slug, name="Jessica Pratt", date=D + timedelta(days=1),
                source_url="https://venue.com/event/other/",
            ),
        ],
    )
    await session.commit()  # must not raise UniqueViolation

    await session.refresh(holder)
    await session.refresh(older)
    assert holder.price_min == 42.0  # the hash-keyed row took the update
    assert older.source_key == "url:/event/jp"  # untouched
