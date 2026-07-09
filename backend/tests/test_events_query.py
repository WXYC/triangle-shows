"""Tests for the shared events query + cross-venue de-duplication service.

These lock in the de-duplication behavior that previously lived inline in the
FullCalendar feed handler, so it can be relied on from every consumer.
"""

from datetime import date

from app.services.events_query import query_events

D = date(2026, 8, 1)


async def test_dedup_prefers_richer_record_across_venues(session, make_venue, make_event):
    v1 = await make_venue(slug="cats-cradle", city="Carrboro")
    v2 = await make_venue(slug="local-506", city="Chapel Hill")
    # Sparse record inserted first...
    await make_event(venue=v1, artist="Juana Molina", date=D)
    # ...richer record (image + ticket + price) at a different venue.
    rich = await make_event(
        venue=v2, artist="Juana Molina", date=D,
        image_url="https://img", ticket_url="https://tix", price_min=15.0,
    )
    result = await query_events(session, start=D, end=D)
    assert [e.id for e in result] == [rich.id]


async def test_dedup_keeps_first_across_venues_when_scores_tie(session, make_venue, make_event):
    v1 = await make_venue(slug="cats-cradle")
    v2 = await make_venue(slug="local-506")
    first = await make_event(venue=v1, artist="Jessica Pratt", date=D)
    await make_event(venue=v2, artist="Jessica Pratt", date=D)
    result = await query_events(session, start=D, end=D)
    assert [e.id for e in result] == [first.id]


async def test_dedup_collapses_same_venue_same_key(session, make_venue, make_event):
    v = await make_venue()
    first = await make_event(venue=v, artist="Chuquimamani-Condori", date=D)
    # Richer, but same venue — the score-based replacement only applies across venues,
    # so the same-venue duplicate collapses to the first record regardless.
    await make_event(venue=v, artist="Chuquimamani-Condori", date=D, ticket_url="https://tix")
    result = await query_events(session, start=D, end=D)
    assert [e.id for e in result] == [first.id]


async def test_distinct_keys_are_all_kept(session, make_venue, make_event):
    v = await make_venue()
    await make_event(venue=v, artist="Juana Molina", date=D)              # distinct artist
    await make_event(venue=v, artist="Jessica Pratt", date=D)             # distinct artist
    await make_event(venue=v, artist="Juana Molina", date=date(2026, 8, 2))  # distinct date
    result = await query_events(session)
    assert len(result) == 3


async def test_dedup_false_returns_all(session, make_venue, make_event):
    v1 = await make_venue(slug="cats-cradle")
    v2 = await make_venue(slug="local-506")
    await make_event(venue=v1, artist="Duke Ellington", date=D)
    await make_event(venue=v2, artist="Duke Ellington", date=D)
    result = await query_events(session, dedup=False)
    assert len(result) == 2


async def test_filters_by_date_window(session, make_venue, make_event):
    v = await make_venue()
    await make_event(venue=v, artist="Stereolab", date=date(2026, 8, 1))
    keep = await make_event(venue=v, artist="Cat Power", date=date(2026, 8, 15))
    await make_event(venue=v, artist="Hermanos Gutiérrez", date=date(2026, 9, 1))
    result = await query_events(session, start=date(2026, 8, 10), end=date(2026, 8, 20))
    assert [e.id for e in result] == [keep.id]


async def test_filters_by_city_size_and_venue_slug(session, make_venue, make_event):
    v1 = await make_venue(slug="cats-cradle", city="Carrboro", size_category="large")
    v2 = await make_venue(slug="local-506", city="Chapel Hill", size_category="small")
    e1 = await make_event(venue=v1, artist="Juana Molina", date=D)
    e2 = await make_event(venue=v2, artist="Jessica Pratt", date=D)
    assert [e.id for e in await query_events(session, cities=["Carrboro"])] == [e1.id]
    assert [e.id for e in await query_events(session, sizes=["small"])] == [e2.id]
    assert [e.id for e in await query_events(session, venue_slugs=["local-506"])] == [e2.id]


async def test_filters_by_search_status_and_genre(session, make_venue, make_event):
    v = await make_venue()
    match = await make_event(
        venue=v, artist="Juana Molina", name="Juana Molina live",
        date=D, status="on_sale", genre="Rock",
    )
    await make_event(
        venue=v, artist="Jessica Pratt", name="Jessica Pratt",
        date=D, status="sold_out", genre="Folk",
    )
    assert [e.id for e in await query_events(session, search="molina")] == [match.id]
    assert [e.id for e in await query_events(session, status="on_sale")] == [match.id]
    assert [e.id for e in await query_events(session, genre="rock")] == [match.id]
