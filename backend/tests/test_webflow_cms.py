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


# --- The join must be by slug, never by list position ---
# The two lists are independent Webflow collections with no guaranteed shared
# order or 1:1 cardinality (a show can appear in one tab but not the other).
# Both shows below appear in the grid tab, but in the REVERSE order of the
# calendar tab, with distinct flyer images. A positional/zip pairing would cross
# the two images; the slug keys keep them straight.
_CALENDAR_TWO_HTML = """
<div class="w-dyn-list">
  <div role="list" class="show-collection-list w-dyn-items">
    <div role="listitem" class="show-collection-item w-dyn-item">
      <div class="show-name">Juana Molina</div>
      <div class="show-start-date">September 3, 2026</div>
      <div class="show-slug">juana-molina-03-sep</div>
    </div>
    <div role="listitem" class="show-collection-item w-dyn-item">
      <div class="show-name">Jessica Pratt</div>
      <div class="show-start-date">September 5, 2026</div>
      <div class="show-slug">jessica-pratt-05-sep</div>
    </div>
  </div>
</div>
"""

_JUANA_IMG = "https://cdn.prod.website-files.com/68f7b7271d0ad608b3ca1008/aaa_juana-molina.jpeg"
_JESSICA_IMG = "https://cdn.prod.website-files.com/68f7b7271d0ad608b3ca1008/bbb_jessica-pratt.jpeg"

_GRID_TWO_REVERSED_HTML = f"""
<div class="uui-padding-vertical-large-3 w-dyn-list">
  <div role="list" class="uui-layout88_list w-dyn-items">
    <div role="listitem" class="uui-layout88_item-2 w-dyn-item">
      <a href="/shows/jessica-pratt-05-sep" class="link-block-2 w-inline-block">
        <div class="show-image-wrapper">
          <img loading="lazy" alt="" class="image-48" src="{_JESSICA_IMG}"/>
        </div>
      </a>
    </div>
    <div role="listitem" class="uui-layout88_item-2 w-dyn-item">
      <a href="/shows/juana-molina-03-sep" class="link-block-2 w-inline-block">
        <div class="show-image-wrapper">
          <img loading="lazy" alt="" class="image-48" src="{_JUANA_IMG}"/>
        </div>
      </a>
    </div>
  </div>
</div>
"""


def test_parse_soup_joins_by_slug_not_by_position():
    html = f"<html><body>{_CALENDAR_TWO_HTML}{_GRID_TWO_REVERSED_HTML}</body></html>"
    events = _scraper()._parse_soup(_soup(html))
    by_name = {e.name: e for e in events}
    # If the join were positional (zip), the reversed grid would swap these two.
    assert by_name["Juana Molina"].image_url == _JUANA_IMG
    assert by_name["Jessica Pratt"].image_url == _JESSICA_IMG


def test_parse_soup_reads_lazy_loaded_flyer_from_data_src():
    # Webflow lazy-loads: some renders carry the real flyer URL in data-src with
    # no plain src attribute. Extraction must fall back to data-src, not drop it.
    data_src = "https://cdn.prod.website-files.com/68f7b7271d0ad608b3ca1008/ccc_lazy.jpeg"
    grid = f"""
    <div class="uui-padding-vertical-large-3 w-dyn-list">
      <div role="list" class="uui-layout88_list w-dyn-items">
        <div role="listitem" class="uui-layout88_item-2 w-dyn-item">
          <a href="/shows/chuquimamani-condori-14-aug" class="link-block-2 w-inline-block">
            <img loading="lazy" alt="" class="image-48" data-src="{data_src}"/>
          </a>
        </div>
      </div>
    </div>
    """
    html = f"<html><body>{_CALENDAR_TAB_HTML}{grid}</body></html>"
    events = _scraper()._parse_soup(_soup(html))
    chuqui = next(e for e in events if e.name == "Chuquimamani-Condori")
    assert chuqui.image_url == data_src


def test_parse_soup_join_tolerates_trailing_slash_on_grid_href():
    # A detail-page link rendered with a trailing slash (/shows/<slug>/) must
    # still join to the calendar slug, which carries no slash.
    img = "https://cdn.prod.website-files.com/68f7b7271d0ad608b3ca1008/ddd_slash.jpeg"
    grid = f"""
    <div class="uui-padding-vertical-large-3 w-dyn-list">
      <div role="list" class="uui-layout88_list w-dyn-items">
        <div role="listitem" class="uui-layout88_item-2 w-dyn-item">
          <a href="/shows/chuquimamani-condori-14-aug/" class="link-block-2 w-inline-block">
            <img loading="lazy" alt="" class="image-48" src="{img}"/>
          </a>
        </div>
      </div>
    </div>
    """
    html = f"<html><body>{_CALENDAR_TAB_HTML}{grid}</body></html>"
    events = _scraper()._parse_soup(_soup(html))
    chuqui = next(e for e in events if e.name == "Chuquimamani-Condori")
    assert chuqui.image_url == img


def test_build_image_map_keeps_first_flyer_on_duplicate_slug():
    # The grid can render a show twice (e.g. a "featured" duplicate). First entry
    # in document order wins, deterministically — never the later one.
    first = "https://cdn.prod.website-files.com/68f7b7271d0ad608b3ca1008/eee_first.jpeg"
    second = "https://cdn.prod.website-files.com/68f7b7271d0ad608b3ca1008/fff_second.jpeg"
    grid = f"""
    <div class="uui-padding-vertical-large-3 w-dyn-list">
      <div role="list" class="uui-layout88_list w-dyn-items">
        <div role="listitem" class="uui-layout88_item-2 w-dyn-item">
          <a href="/shows/chuquimamani-condori-14-aug" class="link-block-2 w-inline-block">
            <img loading="lazy" alt="" class="image-48" src="{first}"/>
          </a>
        </div>
        <div role="listitem" class="uui-layout88_item-2 w-dyn-item">
          <a href="/shows/chuquimamani-condori-14-aug" class="link-block-2 w-inline-block">
            <img loading="lazy" alt="" class="image-48" src="{second}"/>
          </a>
        </div>
      </div>
    </div>
    """
    soup = _soup(f"<html><body>{grid}</body></html>")
    image_by_slug = WebflowCMSScraper._build_image_map(soup, "/shows/", "img")
    assert image_by_slug["chuquimamani-condori-14-aug"] == first
