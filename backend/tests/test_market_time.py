"""Tests for app.market_time's config-driven zone (region-pack epic Phase 2, issue #64).

market_tz()/today_in_market() replace the former TRIANGLE_TZ constant /
today_in_triangle() — the zone now comes from the active region's site.toml
rather than a hardcoded "America/New_York" literal.
"""

import zoneinfo
from datetime import datetime

from app import market_time


def test_market_tz_matches_the_shipped_triangle_pack():
    assert market_time.market_tz() == zoneinfo.ZoneInfo("America/New_York")


def test_today_in_market_uses_the_configured_zone():
    expected = datetime.now(market_time.market_tz()).date()
    assert market_time.today_in_market() == expected


def test_market_tz_reflects_an_overridden_pack(tmp_path, site_config_env):
    path = tmp_path / "site.toml"
    path.write_text(
        """
        [site]
        name = "Fixture Region"
        domain = "fixture.example"
        title = "fixture-shows"
        tagline = "fixture tagline"
        description = "fixture description"
        calendar_description = "fixture calendar description"
        timezone = "America/Los_Angeles"
        region_code = "WA"
        favicon_color = "#123456"
        default_palette = "amber"
        ascii_art = "fixture"

        [credit]
        label = "fixture"
        url = "https://example.com"
        repo_url = "https://example.com/repo"
        """
    )
    site_config_env(path)
    assert market_time.market_tz() == zoneinfo.ZoneInfo("America/Los_Angeles")
