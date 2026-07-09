"""Regression tests for the scrape manager's upsert change-detection.

The API documents updated_at as "changes only when a scrape actually modifies the
row" (an incremental-sync cursor), and ScrapeLog.events_updated as the count of rows
that really changed. Both rest on SQLAlchemy skipping the UPDATE when re-assigned
values equal the stored ones — these tests enforce that contract so a future rewrite
of the upsert (e.g. to Core update() or ON CONFLICT) can't silently break it.
"""

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

    from app.models import Event
    from sqlalchemy import select
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

    from app.models import Event
    from sqlalchemy import select
    event = (await session.execute(select(Event))).scalar_one()
    first_updated_at = event.updated_at

    created, updated = await manager._upsert_events(venue.id, [_scraped(venue.slug, price_min=15.0)])
    await session.commit()
    assert (created, updated) == (0, 1)
    await session.refresh(event)
    assert event.price_min == 15.0
    assert event.updated_at > first_updated_at
