"""One-time description-sanitization backfill (Alembic migration 0006).

The migration delegates to app/services/description_backfill.py so the logic is
testable here through the ORM harness without running Alembic. The function takes
a sync Connection (what op.get_bind() provides); the test reaches it through
AsyncConnection.run_sync, mirroring tests/test_identity_backfill.py.

Legacy rows (written before scrape-time sanitization existed) are simulated by
inserting Event rows via the ORM, which — unlike the ScrapedEvent scrape path —
stores whatever description it is handed verbatim.
"""

from sqlalchemy import select

from app.models import Event
from app.services.description_backfill import sanitize_existing_descriptions

# Raw Squarespace RTE shape with an injected <script> — the pre-fix stored form.
_RAW_RTE = (
    '<p style="white-space:pre-wrap;" data-rte-preserve-empty="true">'
    "DJ Python<script>steal()</script></p>"
)


async def _run_backfill(session) -> int:
    """Invoke the sync backfill on the session's connection; return rows changed."""
    conn = await session.connection()
    changed = await conn.run_sync(sanitize_existing_descriptions)
    await session.commit()
    return changed


async def _descriptions_by_id(session) -> dict[int, str]:
    # Column-select reads fresh DB state (the backfill's UPDATEs bypass the ORM
    # identity map), so no expire_all — which would expire the caller's Event
    # objects and trigger a sync lazy-load in this async context (MissingGreenlet).
    rows = (await session.execute(select(Event.id, Event.description))).all()
    return {row.id: row.description for row in rows}


async def test_backfill_sanitizes_legacy_html(session, make_event):
    ev_id = (await make_event(description=_RAW_RTE)).id

    changed = await _run_backfill(session)

    after = (await _descriptions_by_id(session))[ev_id]
    assert changed == 1
    assert "style=" not in after and "<script" not in after.lower()
    assert "data-rte-preserve-empty" not in after
    assert after == "<p>DJ Python</p>"


async def test_backfill_leaves_already_clean_rows_untouched(session, make_event):
    ev_id = (await make_event(description="<p>Already clean</p>")).id

    changed = await _run_backfill(session)

    assert changed == 0
    assert (await _descriptions_by_id(session))[ev_id] == "<p>Already clean</p>"


async def test_backfill_nulls_empty_markup(session, make_event):
    ev_id = (await make_event(description="<p></p>")).id

    changed = await _run_backfill(session)

    assert changed == 1
    assert (await _descriptions_by_id(session))[ev_id] is None


async def test_backfill_ignores_null_descriptions(session, make_event):
    ev_id = (await make_event(description=None)).id

    changed = await _run_backfill(session)

    assert changed == 0
    assert (await _descriptions_by_id(session))[ev_id] is None


async def test_backfill_only_counts_changed_rows(session, make_event):
    # Two legacy rows to rewrite, one already-clean row to skip.
    await make_event(description=_RAW_RTE)
    await make_event(description='<p style="x">Foodman</p>')
    await make_event(description="<p>Clean</p>")

    changed = await _run_backfill(session)

    assert changed == 2


async def test_backfill_is_idempotent(session, make_event):
    await make_event(description=_RAW_RTE)

    first = await _run_backfill(session)
    second = await _run_backfill(session)

    assert first == 1
    assert second == 0  # already sanitized -> nothing to rewrite
