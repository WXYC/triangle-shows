"""Regression tests for the scrape manager's upsert change-detection.

The API documents updated_at as "changes only when a scrape actually modifies the
row" (an incremental-sync cursor), and ScrapeLog.events_updated as the count of rows
that really changed. Both rest on SQLAlchemy skipping the UPDATE when re-assigned
values equal the stored ones — these tests enforce that contract so a future rewrite
of the upsert (e.g. to Core update() or ON CONFLICT) can't silently break it.
"""

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
        source="manual",
        price_min=10.0,
    )
    fields.update(overrides)
    return ScrapedEvent(**fields)


async def test_rescrape_with_identical_data_updates_nothing(session, make_venue):
    venue = await make_venue()
    manager = ScrapeManager(session)

    created, updated = await manager._upsert_events(venue.id, [_scraped(venue.slug)])
    await session.commit()
    assert (created, updated) == (1, 0)

    event = (await session.execute(select(Event))).scalar_one()
    first_updated_at = event.updated_at

    # Identical re-scrape: no UPDATE, no updated_at movement, updated count stays 0.
    created, updated = await manager._upsert_events(venue.id, [_scraped(venue.slug)])
    await session.commit()
    assert (created, updated) == (0, 0)
    await session.refresh(event)
    assert event.updated_at == first_updated_at


async def test_rescrape_with_changed_data_bumps_updated_at_and_counter(session, make_venue):
    venue = await make_venue()
    manager = ScrapeManager(session)

    await manager._upsert_events(venue.id, [_scraped(venue.slug)])
    await session.commit()

    event = (await session.execute(select(Event))).scalar_one()
    first_updated_at = event.updated_at

    created, updated = await manager._upsert_events(venue.id, [_scraped(venue.slug, price_min=15.0)])
    await session.commit()
    assert (created, updated) == (0, 1)
    await session.refresh(event)
    assert event.price_min == 15.0
    assert event.updated_at > first_updated_at


# --- headliner derivation (issue #18) ---
# The upsert derives Event.headliner on every pass: from the scraper's structured
# performer when supplied, else heuristically from the billing string. Unlike the
# merge-preserved optional fields, it is assigned unconditionally so it tracks the
# current name — including recomputing to NULL on a rename to a non-performance
# billing.


async def test_upsert_derives_headliner_from_billing_string(session, make_venue):
    venue = await make_venue()
    manager = ScrapeManager(session)

    billing = "Juana Molina w/ Truth Club"
    await manager._upsert_events(venue.id, [_scraped(venue.slug, name=billing, artist=billing)])
    await session.commit()

    event = (await session.execute(select(Event))).scalar_one()
    # name and artist keep the full billing — headliner is additive (issue #18).
    assert event.name == billing
    assert event.artist == billing
    assert event.headliner == "Juana Molina"


async def test_upsert_prefers_scraper_supplied_performer_over_name(session, make_venue):
    venue = await make_venue()
    manager = ScrapeManager(session)

    # A structured performer (JSON-LD Event.performer, TM attractions) beats the
    # name heuristic — the name here would heuristically yield the full title.
    scraped = _scraped(
        venue.slug,
        name="Mdou Moctar: Village Tour Kickoff",
        headliner="Mdou Moctar",
    )
    await manager._upsert_events(venue.id, [scraped])
    await session.commit()

    event = (await session.execute(select(Event))).scalar_one()
    assert event.headliner == "Mdou Moctar"


async def test_structured_performer_is_stored_verbatim_not_heuristically_mangled(session, make_venue):
    venue = await make_venue()
    manager = ScrapeManager(session)

    # A real band whose name matches a null/strip heuristic pattern. Because the
    # scraper supplied it as a STRUCTURED performer, it is authoritative and must
    # be stored verbatim — running extract_headliner over it would null it (the
    # "karaoke" non-performance rule), fabricating a missing headliner.
    scraped = _scraped(
        venue.slug,
        name="Some Event Title",
        headliner="Karaoke From Hell",
    )
    await manager._upsert_events(venue.id, [scraped])
    await session.commit()

    event = (await session.execute(select(Event))).scalar_one()
    assert event.headliner == "Karaoke From Hell"


async def test_rename_recomputes_headliner_and_can_null_it(session, make_venue):
    venue = await make_venue()
    manager = ScrapeManager(session)

    # ext-keyed identity so the rename reconciles onto the same row.
    await manager._upsert_events(
        venue.id,
        [_scraped(venue.slug, external_id="tm-1", name="Jessica Pratt w/ Weak Signal", artist=None)],
    )
    await session.commit()
    event = (await session.execute(select(Event))).scalar_one()
    assert event.headliner == "Jessica Pratt"

    # The venue repurposes the listing: derived headliner must follow the new
    # name, not merge-preserve the stale artist.
    await manager._upsert_events(
        venue.id,
        [_scraped(venue.slug, external_id="tm-1", name="WEDNESDAY KARAOKE!", artist=None)],
    )
    await session.commit()
    await session.refresh(event)
    assert event.name == "WEDNESDAY KARAOKE!"
    assert event.headliner is None
