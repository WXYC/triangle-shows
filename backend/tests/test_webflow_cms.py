"""Pour House (webflow_cms.py) scraper: parse-layer behavior.

Pure unit tests — the parse method is fed HTML fragments modeled on the live Pour
House Webflow calendar page (https://www.pourhouseraleigh.com/calendar, fetched
2026-07-21), so there is no database and no HTTP. The scraper fetches via the
shared BaseScraper.fetch_soup path (current-Chrome browser headers); the
stale-User-Agent regression guard lives with that shared path in
tests/test_fetch_soup.py, so it isn't duplicated here.

The live page renders every show twice, in two separate Webflow CMS lists: a
"Calendar" tab (`.show-collection-item` — name/date/slug only, confirmed to carry
no image) and a "Grid" tab (`.uui-layout88_item-2`, wrapping an
`<a href="/shows/<slug>">` that contains the show-flyer `<img>`). The scraper
cross-references the two lists by slug, so the fixture below reproduces both.
"""

from bs4 import BeautifulSoup

from app.scrapers.webflow_cms import WebflowCMSScraper

# Modeled on the live markup at https://www.pourhouseraleigh.com/calendar — real
# CSS classes/structure the scraper's selectors target, with WXYC-representative
# acts swapped in for the titles. The second show has no matching entry in the
# grid-tab fragment, exercising the "image absent" path required by issue #56.
_CALENDAR_TAB_HTML = """
<div class="w-dyn-list">
  <div role="list" class="show-collection-list w-dyn-items">
    <div role="listitem" class="show-collection-item w-dyn-item">
      <div class="show-name">(18+) Nilüfer Yanya w/ Hermanos Gutiérrez</div>
      <div class="show-start-date">August 9, 2026</div>
      <div class="show-slug">18-nilufer-yanya-09-aug</div>
    </div>
    <div role="listitem" class="show-collection-item w-dyn-item">
      <div class="show-name">Chuquimamani-Condori</div>
      <div class="show-start-date">August 14, 2026</div>
      <div class="show-slug">chuquimamani-condori-14-aug</div>
    </div>
  </div>
</div>
"""

_GRID_TAB_HTML = """
<div class="uui-padding-vertical-large-3 w-dyn-list">
  <div role="list" class="uui-layout88_list w-dyn-items">
    <div role="listitem" class="uui-layout88_item-2 w-dyn-item">
      <a href="/shows/18-nilufer-yanya-09-aug" class="link-block-2 w-inline-block">
        <div class="show-image-wrapper">
          <img loading="lazy" alt="" class="image-48"
               src="https://cdn.prod.website-files.com/68f7b7271d0ad608b3ca1008/6a0f0eb8_nilufer-yanya.jpeg"/>
        </div>
      </a>
    </div>
  </div>
</div>
"""

_PAGE_HTML = f"<html><body>{_CALENDAR_TAB_HTML}{_GRID_TAB_HTML}</body></html>"


def _scraper() -> WebflowCMSScraper:
    return WebflowCMSScraper(
        "pour-house",
        {
            "url": "https://www.pourhouseraleigh.com/calendar",
            "base_url": "https://www.pourhouseraleigh.com",
        },
    )


def _soup(html: str = _PAGE_HTML) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def test_parse_soup_extracts_image_url_when_present():
    events = _scraper()._parse_soup(_soup())
    nilufer = next(e for e in events if "Nilüfer" in e.name)
    assert nilufer.image_url == (
        "https://cdn.prod.website-files.com/68f7b7271d0ad608b3ca1008/6a0f0eb8_nilufer-yanya.jpeg"
    )


def test_parse_soup_image_url_none_when_show_missing_from_grid_tab():
    events = _scraper()._parse_soup(_soup())
    chuqui = next(e for e in events if e.name == "Chuquimamani-Condori")
    assert chuqui.image_url is None


def test_parse_soup_still_strips_age_restriction_and_builds_ticket_url():
    # Image extraction must not regress the fields the scraper already derived.
    events = _scraper()._parse_soup(_soup())
    nilufer = next(e for e in events if "Nilüfer" in e.name)
    assert nilufer.name == "Nilüfer Yanya w/ Hermanos Gutiérrez"
    assert nilufer.age_restriction == "18+"
    assert nilufer.ticket_url == "https://www.pourhouseraleigh.com/shows/18-nilufer-yanya-09-aug"


def test_parse_soup_no_exception_when_grid_tab_entirely_absent():
    # Defensive: a page missing the grid tab (markup drift) must degrade every
    # event's image_url to None rather than raise.
    events = _scraper()._parse_soup(_soup(_CALENDAR_TAB_HTML))
    assert len(events) == 2
    assert all(e.image_url is None for e in events)
