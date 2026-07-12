"""Unit tests for scraped-description HTML sanitization.

Pure-function tests — no database or HTTP client. Venue feeds hand scrapers
rich-text HTML in the description field: Squarespace's RTE wraps blurbs in
``<p style="white-space:pre-wrap;" data-rte-preserve-empty="true">…</p>`` and the
JSON-LD scrapers (tribe_events, mec) copy whatever markup the venue authored.

The web modal renders this one field as HTML (``frontend/js/modal.js``) so blurbs
keep their paragraphs, emphasis, and links — which makes ``clean_description`` a
security boundary. It must keep a safe formatting subset and strip everything that
could script or restyle the page (``style``/``class``/``data-*`` attributes, event
handlers, ``<script>``/``<style>``/``<img>``/``<iframe>``, ``javascript:`` URLs).
``ScrapedEvent.__post_init__`` applies it to every scraper's output.

The Boom Club sample below is the real shape that surfaced the bug; the injected
``<script>`` and inline ``style`` stand in for hostile markup a feed could carry.
"""

from datetime import date

import pytest
from bs4 import BeautifulSoup

from app.scrapers.base import ScrapedEvent, clean_description


def _text(html: str) -> str:
    """Visible text of a fragment — asserts on what the reader actually sees."""
    return BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)


# The real Boom Club (Squarespace) body, plus a <script> a feed could smuggle in.
BOOM_CLUB_RTE = (
    '<p style="white-space:pre-wrap;" data-rte-preserve-empty="true">'
    "Two of electronic music’s most distinctive voices share a bill at BOOM "
    "Club. Foodman and DJ Python each push dance music into strange and beautiful "
    "territory while remaining deeply connected to its roots."
    "<script>alert(document.cookie)</script></p>"
    '<p style="white-space:pre-wrap;" data-rte-preserve-empty="true">'
    "Foodman is the project of Japanese producer and DJ Takahide Higuchi.</p>"
)


class TestKeepsSafeFormatting:
    """The readable formatting subset survives sanitization."""

    def test_keeps_paragraphs(self):
        out = clean_description(BOOM_CLUB_RTE)
        assert out.count("<p>") == 2  # both paragraphs preserved as structure
        assert _text(out).startswith("Two of electronic music")
        assert "Foodman is the project" in _text(out)

    def test_keeps_emphasis(self):
        out = clean_description("<p><strong>Foodman</strong> and <em>DJ Python</em></p>")
        assert "<strong>" in out and "<em>" in out

    def test_keeps_links_and_adds_rel(self):
        out = clean_description('<p><a href="https://boom-club.org">tickets</a></p>')
        assert 'href="https://boom-club.org"' in out
        # Sanitizer hardens outbound links against tab-nabbing.
        assert 'rel="noopener noreferrer"' in out

    def test_keeps_lists_and_line_breaks(self):
        out = clean_description("<ul><li>Doors 8</li><li>Show 9</li></ul><p>a<br>b</p>")
        assert "<ul>" in out and "<li>" in out and "<br>" in out


class TestStripsUnsafeMarkup:
    """Anything that could script or restyle the page is removed."""

    def test_strips_squarespace_style_and_data_attributes(self):
        out = clean_description(BOOM_CLUB_RTE)
        assert "style=" not in out
        assert "data-rte-preserve-empty" not in out
        assert "white-space" not in out

    def test_drops_script_content_not_just_the_tag(self):
        out = clean_description("<p>Real blurb<script>alert(document.cookie)</script></p>")
        assert "<script" not in out
        assert "alert" not in out  # content removed, not leaked as visible text
        assert "Real blurb" in _text(out)

    def test_strips_inline_event_handlers(self):
        out = clean_description('<p onclick="steal()">Chuquimamani-Condori</p>')
        assert "onclick" not in out and "steal" not in out
        assert "Chuquimamani-Condori" in _text(out)

    def test_neutralizes_javascript_url_but_keeps_link_text(self):
        out = clean_description('<p><a href="javascript:alert(1)">click</a></p>')
        assert "javascript:" not in out
        assert "click" in _text(out)

    def test_drops_images_and_iframes(self):
        out = clean_description(
            '<p>Jessica Pratt<img src="x" onerror="alert(1)"><iframe src="//evil"></iframe></p>'
        )
        assert "<img" not in out and "<iframe" not in out and "onerror" not in out
        assert "Jessica Pratt" in _text(out)

    def test_unwraps_unknown_tags_keeping_their_text(self):
        out = clean_description('<div class="wrap"><span style="color:red">Stereolab</span></div>')
        assert "<div" not in out and "<span" not in out and "class=" not in out
        assert _text(out) == "Stereolab"


class TestVisibleText:
    """Entity and plain-text handling."""

    def test_entities_render_as_their_characters(self):
        out = clean_description("<p>Tosca &amp; Kruder &amp; Dorfmeister</p>")
        assert _text(out) == "Tosca & Kruder & Dorfmeister"

    def test_plain_text_is_preserved(self):
        assert _text(clean_description("Jessica Pratt live at Cat's Cradle")) == (
            "Jessica Pratt live at Cat's Cradle"
        )


class TestEmptyAndNonString:
    @pytest.mark.parametrize(
        "value",
        [None, "", "   ", "<p></p>", "<p>   </p>", "<script>alert(1)</script>"],
    )
    def test_no_readable_text_becomes_none(self, value):
        assert clean_description(value) is None

    def test_non_string_becomes_none(self):
        # A JSON feed can hand back a dict/list where a body string belongs.
        assert clean_description({"html": "<p>x</p>"}) is None


class TestScrapedEventAppliesSanitizing:
    """Sanitizing happens for every scraper via __post_init__ — the choke point."""

    def _event(self, description):
        return ScrapedEvent(
            name="DJ Python and Foodman",
            date=date(2026, 7, 11),
            venue_slug="boom-club",
            source="squarespace",
            description=description,
        )

    def test_post_init_sanitizes_html(self):
        ev = self._event(BOOM_CLUB_RTE)
        assert "style=" not in ev.description and "<script" not in ev.description
        assert ev.description.count("<p>") == 2

    def test_post_init_blank_markup_becomes_none(self):
        assert self._event("<p></p>").description is None

    def test_post_init_leaves_plain_text_readable(self):
        assert _text(self._event("Jessica Pratt").description) == "Jessica Pratt"
