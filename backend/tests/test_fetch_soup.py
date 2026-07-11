"""Unit tests for BaseScraper's shared HTML fetch path (issue #22).

fetch_soup() centralizes the httpx client config + GET + raise_for_status +
BeautifulSoup(..., "lxml") that every HTML scraper used to repeat inline. These
tests exercise it against an in-memory httpx.MockTransport so no network is hit,
and pin the two inconsistencies the centralization fixed:

  * the shared browser User-Agent must stay on a current Chrome (a stale
    Chrome/122 was WAF-blocked and took Carolina Theatre offline), and
  * every scraper — including koka_booth, which used to omit them — sends the
    shared browser headers.
"""

import httpx
import pytest
from bs4 import BeautifulSoup

from app.scrapers import base
from app.scrapers.base import BROWSER_HEADERS, HTTP_TIMEOUT, BaseScraper


# A concrete scraper so we can instantiate the ABC. scrape() is never called here;
# the tests drive fetch_soup()/http_client() directly.
class _Scraper(BaseScraper):
    async def scrape(self):  # pragma: no cover - not exercised
        return []


def _scraper() -> _Scraper:
    return _Scraper("test-venue", {})


_HTML = "<html><body><h1 class='title'>Juana Molina</h1></body></html>"


def _record_handler(recorder: list) -> "callable":
    """A MockTransport handler that records each request and returns _HTML."""

    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        return httpx.Response(200, text=_HTML)

    return handler


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- fetch_soup with a passed-in client (multi-page reuse path) -----------------


async def test_fetch_soup_parses_response_via_passed_client():
    requests: list[httpx.Request] = []
    async with _mock_client(_record_handler(requests)) as client:
        soup = await _scraper().fetch_soup("https://venue.example/events/", client=client)

    assert isinstance(soup, BeautifulSoup)
    assert soup.select_one("h1.title").get_text() == "Juana Molina"
    assert len(requests) == 1
    assert str(requests[0].url) == "https://venue.example/events/"


async def test_fetch_soup_reuses_one_passed_client_across_calls():
    # The multi-page scrapers (mec, tribe_events, koka_booth) fetch a listing page
    # and then several detail pages through the SAME client — prove one client
    # serves multiple fetch_soup calls.
    requests: list[httpx.Request] = []
    async with _mock_client(_record_handler(requests)) as client:
        scraper = _scraper()
        await scraper.fetch_soup("https://venue.example/events/", client=client)
        await scraper.fetch_soup("https://venue.example/event/1/", client=client)
        await scraper.fetch_soup("https://venue.example/event/2/", client=client)

    assert [str(r.url) for r in requests] == [
        "https://venue.example/events/",
        "https://venue.example/event/1/",
        "https://venue.example/event/2/",
    ]


async def test_fetch_soup_passed_client_header_override_is_per_request():
    requests: list[httpx.Request] = []
    async with _mock_client(_record_handler(requests)) as client:
        await _scraper().fetch_soup(
            "https://venue.example/events/",
            client=client,
            headers={"User-Agent": "CustomVenueBot/1.0"},
        )

    assert requests[0].headers["user-agent"] == "CustomVenueBot/1.0"


async def test_fetch_soup_raises_on_non_2xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    async with _mock_client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await _scraper().fetch_soup("https://venue.example/gone/", client=client)


# --- fetch_soup with an owned client (single-page path) -------------------------


async def test_fetch_soup_owns_a_client_when_none_passed(monkeypatch):
    # No client passed: fetch_soup must build one itself, issue the GET, and parse.
    # Inject a MockTransport into whatever client base builds so no socket opens,
    # and capture the kwargs it was constructed with.
    requests: list[httpx.Request] = []
    built_kwargs: list[dict] = []
    real_async_client = httpx.AsyncClient

    def fake_async_client(**kwargs):
        built_kwargs.append(kwargs)
        return real_async_client(transport=httpx.MockTransport(_record_handler(requests)), **kwargs)

    monkeypatch.setattr(base.httpx, "AsyncClient", fake_async_client)

    soup = await _scraper().fetch_soup("https://venue.example/events/")

    assert soup.select_one("h1.title").get_text() == "Juana Molina"
    assert len(requests) == 1
    # The owned client carries the shared config in one place.
    assert built_kwargs[0]["timeout"] == HTTP_TIMEOUT
    assert built_kwargs[0]["follow_redirects"] is True
    assert built_kwargs[0]["headers"] == BROWSER_HEADERS


async def test_fetch_soup_owned_client_sends_browser_headers(monkeypatch):
    # The request actually leaves with the browser User-Agent (koka_booth used to
    # omit headers entirely; routing through fetch_soup fixes that).
    requests: list[httpx.Request] = []
    real_async_client = httpx.AsyncClient

    def fake_async_client(**kwargs):
        return real_async_client(transport=httpx.MockTransport(_record_handler(requests)), **kwargs)

    monkeypatch.setattr(base.httpx, "AsyncClient", fake_async_client)

    await _scraper().fetch_soup("https://venue.example/events/")

    assert requests[0].headers["user-agent"] == BROWSER_HEADERS["User-Agent"]


async def test_fetch_soup_owned_client_header_override(monkeypatch):
    requests: list[httpx.Request] = []
    built_kwargs: list[dict] = []
    real_async_client = httpx.AsyncClient

    def fake_async_client(**kwargs):
        built_kwargs.append(kwargs)
        return real_async_client(transport=httpx.MockTransport(_record_handler(requests)), **kwargs)

    monkeypatch.setattr(base.httpx, "AsyncClient", fake_async_client)

    await _scraper().fetch_soup(
        "https://venue.example/events/", headers={"User-Agent": "CustomVenueBot/1.0"}
    )

    # An owned client applies the override as its client-level headers.
    assert built_kwargs[0]["headers"] == {"User-Agent": "CustomVenueBot/1.0"}
    assert requests[0].headers["user-agent"] == "CustomVenueBot/1.0"


# --- http_client() factory ------------------------------------------------------


async def test_http_client_carries_shared_config():
    async with BaseScraper.http_client() as client:
        assert client.timeout.connect == HTTP_TIMEOUT
        assert client.follow_redirects is True
        # Browser headers are applied at the client level.
        assert client.headers["user-agent"] == BROWSER_HEADERS["User-Agent"]


async def test_http_client_header_override_replaces_defaults():
    async with BaseScraper.http_client(headers={"User-Agent": "CustomVenueBot/1.0"}) as client:
        assert client.headers["user-agent"] == "CustomVenueBot/1.0"


# --- Stale-User-Agent guard -----------------------------------------------------


def test_browser_user_agent_is_not_the_stale_chrome_122():
    # Regression guard for the Carolina Theatre outage: a stale Chrome/122 UA was
    # hard-blocked by the venue's WAF. Keep BROWSER_HEADERS on a current Chrome —
    # if this fails because Chrome moved on, bump the UA rather than deleting the
    # guard, and update the pinned floor below.
    ua = BROWSER_HEADERS["User-Agent"]
    assert "Chrome/122" not in ua
    assert "Chrome/" in ua

    # The UA must name a Chrome major at or above a known-good floor, so a future
    # edit can't quietly regress to an ancient (block-prone) version.
    import re

    match = re.search(r"Chrome/(\d+)", ua)
    assert match is not None, f"no Chrome major in User-Agent: {ua!r}"
    assert int(match.group(1)) >= 130
