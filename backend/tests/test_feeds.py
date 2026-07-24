"""Tests for the iCal subscription feed (GET /feeds/events.ics).

Focus: the human-readable description packs support acts as "w/ A, B". Since
support_artists is now a lossless text[] (issue #40), the feed joins the list for
display — an empty list renders no "w/" line at all.

Note on wire form: RFC 5545 requires commas inside a property VALUE to be
backslash-escaped ("A\\, B"); calendar clients un-escape on read. The tests assert
the on-the-wire escaped form, which is the correct serialization of the joined string.
"""

from app.site_config import load_site_config
from conftest import DEFAULT_EVENT_DATE as D  # a month out, so it's in the feed window


def _unfold(ical_text: str) -> str:
    """Undo RFC 5545 line folding (a CRLF followed by a leading space/tab continues
    the previous line) so a long DESCRIPTION can be substring-matched as one line."""
    return ical_text.replace("\r\n ", "").replace("\r\n\t", "").replace("\n ", "").replace("\n\t", "")


async def test_ical_renders_support_artists_joined(client, make_event):
    await make_event(
        artist="Juana Molina", date=D, show_time=None,
        support_artists=["Truth Club", "Weak Signal"],
    )
    resp = await client.get("/feeds/events.ics")
    assert resp.status_code == 200
    # The join produces "Truth Club, Weak Signal"; icalendar then backslash-escapes
    # the separating comma per RFC 5545, so the wire carries "w/ Truth Club\, Weak Signal".
    assert "w/ Truth Club\\, Weak Signal" in _unfold(resp.text)


async def test_ical_single_support_name_with_internal_comma_is_not_split(client, make_event):
    # The array is lossless: a one-element list whose name contains a comma renders
    # as that single name (comma escaped on the wire), never split into two acts.
    await make_event(
        artist="Hermanos Gutiérrez", date=D, show_time=None,
        support_artists=["Earth, Wind & Fire"],
    )
    assert "w/ Earth\\, Wind & Fire" in _unfold((await client.get("/feeds/events.ics")).text)


async def test_ical_no_support_renders_no_with_line(client, make_event):
    await make_event(artist="Cat Power", date=D, show_time=None)
    assert "w/ " not in (await client.get("/feeds/events.ics")).text


# --- Characterization pins (region-pack epic, issue #62/#63/#64) -------------------
#
# These pin today's exact calendar/branding bytes so the region-pack extraction can't
# silently change them for the Triangle deployment. Phase 2 (issue #64) re-derives
# each assertion from the shipped Triangle site.toml alongside the original literal
# — proof the manifest reproduces the exact old bytes, not just a claim of it.


async def test_ical_calendar_headers_are_pinned(client, make_event):
    await make_event(artist="Juana Molina", date=D, show_time=None)
    resp = await client.get("/feeds/events.ics")
    text = _unfold(resp.text)

    site = load_site_config().site
    assert "PRODID:-//triangle-shows.net//EN" in text
    assert f"PRODID:-//{site.domain}//EN" in text

    assert "X-WR-CALNAME:Triangle Shows" in text
    assert f"X-WR-CALNAME:{site.name}" in text

    assert "X-WR-CALDESC:Live music across the Triangle" in text
    assert f"X-WR-CALDESC:{site.calendar_description}" in text

    assert "X-WR-TIMEZONE:America/New_York" in text
    assert f"X-WR-TIMEZONE:{site.timezone}" in text


async def test_ical_event_uid_host_is_pinned(client, make_event):
    event = await make_event(artist="Duke Ellington & John Coltrane", date=D, show_time=None)
    text = _unfold((await client.get("/feeds/events.ics")).text)

    site = load_site_config().site
    assert f"UID:{event.id}@triangle-shows.org" in text
    assert f"UID:{event.id}@{site.uid_host}" in text
    # decision 8: uid_host is pinned to a value historically distinct from domain.
    assert site.uid_host != site.domain


async def test_ical_location_state_suffix_is_pinned(client, make_venue, make_event):
    venue = await make_venue(name="The Pinhook", city="Durham")
    await make_event(venue=venue, artist="Chuquimamani-Condori", date=D, show_time=None)
    text = _unfold((await client.get("/feeds/events.ics")).text)

    site = load_site_config().site
    assert "LOCATION:The Pinhook\\, Durham\\, NC" in text
    assert f"LOCATION:The Pinhook\\, Durham\\, {site.region_code}" in text


async def test_ical_content_disposition_filename_is_pinned(client, make_event):
    await make_event(artist="Jessica Pratt", date=D, show_time=None)
    resp = await client.get("/feeds/events.ics")

    site = load_site_config().site
    assert resp.headers["content-disposition"] == 'attachment; filename="triangle-shows.ics"'
    assert resp.headers["content-disposition"] == f'attachment; filename="{site.title}.ics"'
