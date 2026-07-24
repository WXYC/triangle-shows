"""Tests for the region-pack venue config loader (region-pack epic, issue #62/#63).

Covers the shipped Triangle pack (parses to exactly 22 valid VenueConfig, every
observed scraper_config shape round-trips) and the Pydantic validation contract
(unknown scraper_type, duplicate slug, malformed color, ticketmaster-without-id all
raise precise errors) — the two things Phase 1's acceptance criteria call out.
"""

import pytest
from pydantic import ValidationError

from app import site_config
from app.scrapers.identity import SCRAPER_REGISTRY


# --- The shipped Triangle pack ---


def test_shipped_triangle_pack_parses_to_exactly_22_venues():
    config = site_config.load_venue_config()
    assert len(config.venue) == 22
    assert config.removed_slugs == []


def test_shipped_triangle_pack_venue_slugs_are_unique():
    config = site_config.load_venue_config()
    slugs = [v.slug for v in config.venue]
    assert len(slugs) == len(set(slugs))


def test_shipped_triangle_pack_scraper_types_are_all_registered():
    config = site_config.load_venue_config()
    for venue in config.venue:
        assert venue.scraper_type in SCRAPER_REGISTRY


@pytest.mark.parametrize(
    "slug,expected_scraper_config",
    [
        ("lincoln-theatre", {"url": "https://www.lincolntheatre.com/events/"}),
        (
            "cats-cradle",
            {
                "url": "https://catscradle.com/events/",
                "venue_filter": "Cat's Cradle",
                "venue_filter_not": "Back Room",
            },
        ),
        (
            "boom-club",
            {
                "url": "https://www.boom-club.org/events?format=json",
                "exclude_titles": ["Synth Library open", "Synth Library closed"],
            },
        ),
        ("haw-river-ballroom", {"account_id": 477}),
        ("chapel-of-bones", {"organizer_id": "chapel-of-bones"}),
        (
            "pour-house",
            {
                "url": "https://www.pourhouseraleigh.com/calendar",
                "base_url": "https://www.pourhouseraleigh.com",
                "image_selector": "img",
            },
        ),
    ],
)
def test_shipped_pack_scraper_config_shapes_round_trip(slug, expected_scraper_config):
    # Covers every observed scraper_config key type from the region-pack epic's
    # inventory: url (str), venue_filter/venue_filter_not (str), exclude_titles
    # (list[str]), account_id (int), organizer_id (str), base_url (str),
    # image_selector (str).
    config = site_config.load_venue_config()
    by_slug = {v.slug: v for v in config.venue}
    assert by_slug[slug].scraper_config == expected_scraper_config


def test_shipped_pack_ticketmaster_venues_carry_their_venue_id():
    config = site_config.load_venue_config()
    by_slug = {v.slug: v for v in config.venue}
    assert by_slug["koka-booth"].ticketmaster_venue_id == "KovZpZAIAnkA"


def test_shipped_pack_venues_without_a_known_capacity_omit_it():
    config = site_config.load_venue_config()
    by_slug = {v.slug: v for v in config.venue}
    assert by_slug["pour-house"].capacity is None


# --- Validation ---


def _write_pack(tmp_path, body: str):
    path = tmp_path / "venues.toml"
    path.write_text(body)
    return path


def test_unknown_scraper_type_raises(tmp_path, venue_config_env):
    path = _write_pack(
        tmp_path,
        """
        removed_slugs = []
        [[venue]]
        name = "Test Venue"
        slug = "test-venue"
        city = "Testville"
        size_category = "small"
        scraper_type = "not_a_real_scraper"
        color = "#123456"
        """,
    )
    venue_config_env(path)
    with pytest.raises(ValidationError, match="not_a_real_scraper"):
        site_config.load_venue_config()


def test_duplicate_slug_raises(tmp_path, venue_config_env):
    path = _write_pack(
        tmp_path,
        """
        removed_slugs = []
        [[venue]]
        name = "First"
        slug = "dupe"
        city = "Testville"
        size_category = "small"
        scraper_type = "mec"
        color = "#123456"
        [venue.scraper_config]
        url = "https://example.com/first/"

        [[venue]]
        name = "Second"
        slug = "dupe"
        city = "Testville"
        size_category = "small"
        scraper_type = "mec"
        color = "#654321"
        [venue.scraper_config]
        url = "https://example.com/second/"
        """,
    )
    venue_config_env(path)
    with pytest.raises(ValidationError, match="duplicate venue slug"):
        site_config.load_venue_config()


def test_malformed_color_raises(tmp_path, venue_config_env):
    path = _write_pack(
        tmp_path,
        """
        removed_slugs = []
        [[venue]]
        name = "Test Venue"
        slug = "test-venue"
        city = "Testville"
        size_category = "small"
        scraper_type = "mec"
        color = "not-a-hex-color"
        [venue.scraper_config]
        url = "https://example.com/"
        """,
    )
    venue_config_env(path)
    with pytest.raises(ValidationError):
        site_config.load_venue_config()


def test_ticketmaster_without_venue_id_raises(tmp_path, venue_config_env):
    path = _write_pack(
        tmp_path,
        """
        removed_slugs = []
        [[venue]]
        name = "Test Venue"
        slug = "test-venue"
        city = "Testville"
        size_category = "small"
        scraper_type = "ticketmaster"
        color = "#123456"
        """,
    )
    venue_config_env(path)
    with pytest.raises(ValidationError, match="ticketmaster_venue_id"):
        site_config.load_venue_config()


def test_missing_pack_raises_os_error(tmp_path, venue_config_env):
    venue_config_env(tmp_path / "does-not-exist.toml")
    with pytest.raises(OSError):
        site_config.load_venue_config()


# --- Path resolution ---


def test_region_dir_is_anchored_on_the_backend_root_not_cwd(monkeypatch):
    from pathlib import Path

    monkeypatch.delenv("REGION", raising=False)
    resolved = site_config.region_dir()
    assert resolved.parts[-3:] == ("config", "regions", "triangle")
    # Anchored on the backend package root (parents[1] of app/site_config.py), not CWD.
    expected = Path(site_config.__file__).resolve().parents[1] / "config" / "regions" / "triangle"
    assert resolved == expected


def test_region_dir_honors_the_region_env_var(monkeypatch):
    monkeypatch.setenv("REGION", "some-other-region")
    assert site_config.region_dir().name == "some-other-region"
