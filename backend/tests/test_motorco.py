"""Motorco Music Hall scraper: parse-layer behavior (issue #57).

Pure unit tests — the parse method is fed a JS fragment modeled on the live
FullCalendar init array embedded in https://motorcomusic.com/calendar/, so there
is no database and no HTTP. The scraper fetches via its own httpx.AsyncClient
(not BaseScraper.fetch_soup), so nothing here exercises the network path.
"""

from datetime import date, timedelta

from app.scrapers.motorco import MotorcoScraper

# Two JS event objects modeled on the live FullCalendar init array at
# https://motorcomusic.com/calendar/ (fetched 2026-07-21) — real key shapes
# (`title`, `start`, `end`, `url`, `classNames`, `backgroundImage`) with
# WXYC-representative acts swapped in for the titles. Every event on the live
# page carried a `backgroundImage` key; the second object here omits it to
# exercise the image-absent path, which the live page never showed but the
# scraper must still degrade gracefully for (a WordPress admin can always post
# an event with no featured image).
_CALENDAR_JS = """
                        events: [
                            {
                                        title: 'Hermanos Gutiérrez',
                                        start: '2026-09-12 20:00',
                                        end: '2026-09-12 21:00',
                                        url: 'https://motorcomusic.com/event/hermanos-gutierrez/',
                                        classNames: 'tcec-event-',
                                        backgroundImage: 'https://motorcomusic.com/wp-content/uploads/2026/07/hermanos-gutierrez.jpg'
                                        },{
                                        title: 'Chuquimamani-Condori',
                                        start: '2026-09-20 21:00',
                                        end: '2026-09-20 22:00',
                                        url: 'https://motorcomusic.com/event/chuquimamani-condori/',
                                        classNames: 'tcec-event-'
                                        },
                        ],
"""


def _scraper() -> MotorcoScraper:
    return MotorcoScraper("motorco", {"url": "https://motorcomusic.com/calendar/"})


def _future_calendar_js() -> str:
    # The scraper drops past events, so anchor both dates in the future relative
    # to whenever the suite runs.
    d1 = (date.today() + timedelta(days=60)).isoformat()
    d2 = (date.today() + timedelta(days=68)).isoformat()
    return _CALENDAR_JS.replace("2026-09-12", d1).replace("2026-09-20", d2)


def test_extract_events_captures_image_when_present():
    events = _scraper()._extract_events(_future_calendar_js(), date.today())
    hermanos = next(e for e in events if e.name == "Hermanos Gutiérrez")
    assert hermanos.image_url == (
        "https://motorcomusic.com/wp-content/uploads/2026/07/hermanos-gutierrez.jpg"
    )


def test_extract_events_image_absent_degrades_to_none_without_dropping_event():
    events = _scraper()._extract_events(_future_calendar_js(), date.today())
    chuqui = next(e for e in events if e.name == "Chuquimamani-Condori")
    assert chuqui.image_url is None


def test_extract_events_still_captures_title_start_url():
    events = _scraper()._extract_events(_future_calendar_js(), date.today())
    hermanos = next(e for e in events if e.name == "Hermanos Gutiérrez")
    assert hermanos.artist == "Hermanos Gutiérrez"
    assert hermanos.ticket_url == "https://motorcomusic.com/event/hermanos-gutierrez/"
    assert hermanos.source_url == "https://motorcomusic.com/event/hermanos-gutierrez/"
    assert hermanos.show_time is not None
    assert (hermanos.show_time.hour, hermanos.show_time.minute) == (20, 0)


def test_parse_event_passes_through_image_url():
    parsed = _scraper()._parse_event(
        "Cat Power",
        (date.today() + timedelta(days=10)).strftime("%Y-%m-%d %H:%M"),
        "https://motorcomusic.com/event/cat-power/",
        date.today(),
        image_url="https://motorcomusic.com/wp-content/uploads/2026/07/cat-power.jpg",
    )
    assert parsed is not None
    assert parsed.image_url == (
        "https://motorcomusic.com/wp-content/uploads/2026/07/cat-power.jpg"
    )


def test_parse_event_defaults_image_url_to_none():
    parsed = _scraper()._parse_event(
        "Cat Power",
        (date.today() + timedelta(days=10)).strftime("%Y-%m-%d %H:%M"),
        "https://motorcomusic.com/event/cat-power/",
        date.today(),
    )
    assert parsed is not None
    assert parsed.image_url is None


# A single event whose title carries a JS string escape, exactly as WordPress
# `esc_js` emits it inside the single-quoted `title:` value: a literal apostrophe
# becomes a backslash-apostrophe (`\'`). On the live calendar (fetched 2026-07-21)
# 61 of 625 titles carried such an escape — e.g. "This Tour Won't Save You". The
# `\\'` below is one backslash + one apostrophe in the JS source. The extractor
# must capture the whole title (not truncate at the backslash, which would break
# artist matching downstream) and collapse the escape back to a real apostrophe.
def _escaped_apostrophe_js() -> str:
    d = (date.today() + timedelta(days=45)).isoformat()
    return (
        "events: [{"
        "title: 'Wednesday : It\\'s Fine',"
        f"start: '{d} 20:00',"
        "url: 'https://motorcomusic.com/event/wednesday/',"
        "classNames: 'tcec-event-',"
        "backgroundImage: 'https://motorcomusic.com/wp-content/uploads/wednesday.jpg'"
        "}],"
    )


def test_extract_events_preserves_escaped_apostrophe_in_title():
    events = _scraper()._extract_events(_escaped_apostrophe_js(), date.today())
    assert len(events) == 1
    event = events[0]
    # Full title, apostrophe restored, no truncation and no stray backslash.
    assert event.name == "Wednesday : It's Fine"
    assert event.artist == "Wednesday : It's Fine"
    assert "\\" not in event.name
    # The rest of the block still parses (the escape did not derail start/url/image).
    assert event.ticket_url == "https://motorcomusic.com/event/wednesday/"
    assert event.image_url == (
        "https://motorcomusic.com/wp-content/uploads/wednesday.jpg"
    )


def test_parse_event_unescapes_js_string_escapes():
    parsed = _scraper()._parse_event(
        # Raw string as the extractor hands it over: backslash escapes intact.
        'This Tour Won\\\'t Save \\"You\\"',
        (date.today() + timedelta(days=10)).strftime("%Y-%m-%d %H:%M"),
        "https://motorcomusic.com/event/anthony-green/",
        date.today(),
    )
    assert parsed is not None
    assert parsed.name == 'This Tour Won\'t Save "You"'
