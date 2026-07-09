"""Tests for the shared events query + cross-venue de-duplication service.

These lock in the de-duplication behavior that previously lived inline in the
FullCalendar feed handler, so it can be relied on from every consumer.
"""

from datetime import timedelta

from app.services.events_query import query_events
from conftest import DEFAULT_EVENT_DATE as D  # shared with the make_event factory default


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


async def test_dedup_richer_same_venue_record_never_displaces_cross_venue_winner(session, make_venue, make_event):
    v1 = await make_venue(slug="cats-cradle")
    v2 = await make_venue(slug="local-506")
    # First venue's sparse record, then a richer cross-venue record wins the key...
    await make_event(venue=v1, artist="Juana Molina", date=D)
    winner = await make_event(venue=v2, artist="Juana Molina", date=D, ticket_url="https://tix")
    # ...and an even richer record from the FIRST venue must not chain-replace it:
    # replacement compares against the first-seen venue, so first-venue rows can
    # never displace a cross-venue winner.
    await make_event(
        venue=v1, artist="Juana Molina", date=D,
        ticket_url="https://tix2", image_url="https://img", price_min=12.0,
    )
    result = await query_events(session, start=D, end=D)
    assert [e.id for e in result] == [winner.id]


async def test_dedup_matches_apostrophe_variants_across_venues(session, make_venue, make_event):
    v1 = await make_venue(slug="cats-cradle")
    v2 = await make_venue(slug="local-506")
    # U+02BC modifier apostrophe vs ASCII apostrophe — same act, two sources.
    first = await make_event(venue=v1, artist="LʼRain", date=D)
    await make_event(venue=v2, artist="L'Rain", date=D)
    result = await query_events(session, start=D, end=D)
    assert [e.id for e in result] == [first.id]


async def test_dedup_matches_compatibility_character_variants(session, make_venue, make_event):
    v1 = await make_venue(slug="cats-cradle")
    v2 = await make_venue(slug="local-506")
    # "№" decomposes to cased "No" under NFKD; normalization must still fold it.
    first = await make_event(venue=v1, artist="Stereolab № 1", date=D)
    await make_event(venue=v2, artist="Stereolab No 1", date=D)
    result = await query_events(session, start=D, end=D)
    assert [e.id for e in result] == [first.id]


async def test_dedup_matches_diacritic_variants_across_venues(session, make_venue, make_event):
    v1 = await make_venue(slug="cats-cradle")
    v2 = await make_venue(slug="local-506")
    first = await make_event(venue=v1, artist="Hermanos Gutiérrez", date=D)
    # The same act listed by a source that drops the accent is still a duplicate.
    await make_event(venue=v2, artist="Hermanos Gutierrez", date=D)
    result = await query_events(session, start=D, end=D)
    assert [e.id for e in result] == [first.id]


async def test_non_latin_names_do_not_collapse(session, make_venue, make_event):
    v1 = await make_venue(slug="cats-cradle")
    v2 = await make_venue(slug="local-506")
    # Distinct non-Latin names on the same date: normalization keeps their characters,
    # so they must not be treated as duplicates of each other.
    await make_event(venue=v1, artist="Молчат Дома", date=D)
    await make_event(venue=v2, artist="坂本龍一", date=D)
    result = await query_events(session, start=D, end=D)
    assert len(result) == 2


async def test_symbol_only_names_are_each_unique(session, make_venue, make_event):
    v1 = await make_venue(slug="cats-cradle")
    v2 = await make_venue(slug="local-506")
    # Labels with no letters/digits normalize to nothing comparable; each event is
    # treated as unique rather than all collapsing into one empty-string key.
    await make_event(venue=v1, artist="!!!", date=D)
    await make_event(venue=v2, artist="†††", date=D)
    result = await query_events(session, start=D, end=D)
    assert len(result) == 2


async def test_distinct_keys_are_all_kept(session, make_venue, make_event):
    v = await make_venue()
    await make_event(venue=v, artist="Juana Molina", date=D)              # distinct artist
    await make_event(venue=v, artist="Jessica Pratt", date=D)             # distinct artist
    await make_event(venue=v, artist="Juana Molina", date=D + timedelta(days=1))  # distinct date
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
    await make_event(venue=v, artist="Stereolab", date=D)
    keep = await make_event(venue=v, artist="Cat Power", date=D + timedelta(days=14))
    await make_event(venue=v, artist="Hermanos Gutiérrez", date=D + timedelta(days=45))
    result = await query_events(session, start=D + timedelta(days=9), end=D + timedelta(days=19))
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


async def test_search_treats_like_wildcards_literally(session, make_venue, make_event):
    v = await make_venue()
    silk = await make_event(venue=v, artist="100% Silk", date=D)
    await make_event(venue=v, artist="100 Proof", date=D)
    # "%" must match the literal percent sign, not act as a LIKE wildcard that
    # would also match "100 Proof".
    assert [e.id for e in await query_events(session, search="100%")] == [silk.id]
    # A bare wildcard matches nothing rather than everything.
    assert await query_events(session, search="_") == []
