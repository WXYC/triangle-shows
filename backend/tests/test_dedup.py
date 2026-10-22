"""
Tests for title normalization (upstream clean_title).

Upstream's file also covered its run-level dedup and plan_upsert reconcile. The WXYC fork
reconciles through the tiered identity match and the tombstoning snapshot diff instead
(tests/test_reconciliation.py, tests/test_vanished_events.py), so only the clean_title
cases carry over, plus the fork's per-element cleaning of the support_artists list.

Every case here is drawn from a real pair found in the production data.
"""

from datetime import date

from app.scrapers.base import ScrapedEvent, clean_title


TODAY = date(2026, 9, 1)


# --- Test helpers ---


def scraped(name: str, on: date = TODAY, external_id=None, **kwargs) -> ScrapedEvent:
    return ScrapedEvent(
        name=name,
        date=on,
        venue_slug="the-pinhook",
        source="venuepilot",
        external_id=external_id,
        **kwargs,
    )


# --- clean_title ---


class TestCleanTitle:
    def test_decodes_html_entities(self):
        assert clean_title("JoVia Armstrong &#038; alana amore colvin") == (
            "JoVia Armstrong & alana amore colvin"
        )

    def test_decodes_percent_escapes(self):
        assert clean_title("JoVia Armstrong %26 alana amore colvin") == (
            "JoVia Armstrong & alana amore colvin"
        )

    def test_both_encodings_converge(self):
        """The Shadowbox bug: one event, two pages, two encodings, two database rows."""
        entity = clean_title("Ben Copperhead &#038; David Menestres")
        percent = clean_title("Ben Copperhead %26 David Menestres")
        assert entity == percent

    def test_decodes_double_escaped_entities(self):
        assert clean_title("Cool Boy&amp;#039;s Tapes") == "Cool Boy's Tapes"

    def test_decodes_multibyte_percent_escapes(self):
        """Decoding the whole string, not each escape, is what makes this come out right."""
        assert clean_title("Cool Boy%E2%80%99s Tapes") == "Cool Boy’s Tapes"

    def test_collapses_whitespace(self):
        assert clean_title("Alex Cameron /  Josh De Costa") == "Alex Cameron / Josh De Costa"

    def test_leaves_a_bare_percent_alone(self):
        assert clean_title("Get 50% Off Before Doors") == "Get 50% Off Before Doors"

    def test_passes_through_empty_values(self):
        assert clean_title(None) is None
        assert clean_title("") == ""

    def test_applied_on_construction(self):
        assert scraped("A &#038; B").name == "A & B"

    def test_encodings_hash_identically(self):
        assert scraped("A &#038; B").hash == scraped("A %26 B").hash


class TestSupportArtistsAreCleanedPerElement:
    """The fork stores support acts as a list (text[]); upstream's clean_title runs over
    each element, so an entity-bearing opener is stored decoded, not as `&#8217;`."""

    def test_each_element_is_decoded(self):
        se = scraped("Juana Molina", support_artists=["Cat Power &#038; Friends", "Stereolab%E2%80%99s Tim"])
        assert se.support_artists == ["Cat Power & Friends", "Stereolab’s Tim"]

    def test_an_element_that_cleans_to_nothing_is_dropped(self):
        assert scraped("Juana Molina", support_artists=["  ", "Jessica Pratt"]).support_artists == [
            "Jessica Pratt"
        ]

    def test_a_comma_joined_string_is_split_into_the_list_shape(self):
        """Upstream's admin and submission paths still hand ScrapedEvent one string."""
        assert scraped("Juana Molina", support_artists="Cat Power, Jessica Pratt").support_artists == [
            "Cat Power",
            "Jessica Pratt",
        ]

    def test_the_artist_and_headliner_are_decoded_too(self):
        se = scraped("x", artist="Curtis Eller&#8217;s American Circus", headliner="A &amp; B")
        assert se.artist == "Curtis Eller’s American Circus"
        assert se.headliner == "A & B"
