"""One-time ticket_url/image_url sanitization backfill (Alembic migration 0009).

Mirrors tests/test_description_backfill.py: the migration delegates to
app/services/url_backfill.py so the logic is testable here through the ORM harness
without running Alembic. The function takes a sync Connection (what op.get_bind()
provides); the test reaches it through AsyncConnection.run_sync.

Legacy rows (written before ScrapedEvent.__post_init__ validated these two fields)
are simulated by inserting Event rows via the ORM, which — unlike the scrape path —
stores whatever URL it is handed verbatim.
"""

from sqlalchemy import select

from app.models import Event
from app.services.url_backfill import sanitize_existing_urls


async def _run_backfill(session) -> int:
    """Invoke the sync backfill on the session's connection; return rows changed."""
    conn = await session.connection()
    changed = await conn.run_sync(sanitize_existing_urls)
    await session.commit()
    return changed


async def _urls_by_id(session) -> dict[int, tuple]:
    # Column-select reads fresh DB state (the backfill's UPDATEs bypass the ORM
    # identity map), so no expire_all — which would expire the caller's Event
    # objects and trigger a sync lazy-load in this async context (MissingGreenlet).
    rows = (await session.execute(select(Event.id, Event.ticket_url, Event.image_url))).all()
    return {row.id: (row.ticket_url, row.image_url) for row in rows}


async def test_backfill_nulls_a_javascript_ticket_url(session, make_event):
    ev_id = (await make_event(ticket_url="javascript:alert(1)")).id

    changed = await _run_backfill(session)

    assert changed == 1
    assert (await _urls_by_id(session))[ev_id] == (None, None)


async def test_backfill_nulls_a_relative_image_url(session, make_event):
    ev_id = (await make_event(image_url="/img/poster.jpg")).id

    changed = await _run_backfill(session)

    assert changed == 1
    assert (await _urls_by_id(session))[ev_id] == (None, None)


async def test_backfill_nulls_a_scheme_relative_image_url(session, make_event):
    ev_id = (await make_event(image_url="//cdn.evil.example/poster.jpg")).id

    changed = await _run_backfill(session)

    assert changed == 1
    assert (await _urls_by_id(session))[ev_id] == (None, None)


async def test_backfill_leaves_well_formed_urls_untouched(session, make_event):
    ticket = "https://tickets.example.com/42?ref=abc&utm_source=site"
    image = "http://cdn.example.com/poster.jpg"
    ev_id = (await make_event(ticket_url=ticket, image_url=image)).id

    changed = await _run_backfill(session)

    assert changed == 0
    assert (await _urls_by_id(session))[ev_id] == (ticket, image)


async def test_backfill_ignores_rows_with_both_urls_null(session, make_event):
    ev_id = (await make_event()).id

    changed = await _run_backfill(session)

    assert changed == 0
    assert (await _urls_by_id(session))[ev_id] == (None, None)


async def test_backfill_rewrites_only_the_bad_column_on_a_mixed_row(session, make_event):
    ticket = "https://tickets.example.com/42"
    ev_id = (await make_event(ticket_url=ticket, image_url="javascript:alert(1)")).id

    changed = await _run_backfill(session)

    # One row rewritten, not one per bad column.
    assert changed == 1
    assert (await _urls_by_id(session))[ev_id] == (ticket, None)


async def test_backfill_counts_changed_rows_only(session, make_event):
    await make_event(ticket_url="javascript:alert(1)")
    await make_event(image_url="/relative.jpg")
    await make_event(ticket_url="https://tickets.example.com/ok")

    changed = await _run_backfill(session)

    assert changed == 2


async def test_backfill_keeps_the_event_row(session, make_event):
    # A rejected URL must never drop the event from the calendar — same invariant
    # ScrapedEvent.__post_init__ holds at ingestion time.
    ev_id = (await make_event(name="Chuquimamani-Condori", ticket_url="javascript:alert(1)")).id

    await _run_backfill(session)

    names = dict((await session.execute(select(Event.id, Event.name))).all())
    assert names[ev_id] == "Chuquimamani-Condori"


async def test_backfill_is_idempotent(session, make_event):
    await make_event(ticket_url="javascript:alert(1)", image_url="/relative.jpg")

    first = await _run_backfill(session)
    second = await _run_backfill(session)

    assert first == 1
    assert second == 0  # already cleared -> nothing to rewrite


async def test_backfill_clears_a_crlf_bearing_ticket_url(session, make_event):
    # The iCal feed (app/api/feeds.py) reads this column directly and emits it as a
    # URL property; a CRLF in the stored value would inject sibling properties into
    # the served .ics. It fails the absolute-http(s) test on the trailing garbage
    # only if the whole value is rejected — assert that it is.
    ev_id = (
        await make_event(ticket_url="https://x.example/1\r\nATTENDEE:mailto:evil@example.com")
    ).id

    changed = await _run_backfill(session)

    assert changed == 1
    assert (await _urls_by_id(session))[ev_id] == (None, None)
