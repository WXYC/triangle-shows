"""Tests for GET /api/v1/site — the region site manifest (region-pack epic,
issue #62/#64).

Additive endpoint: serves the active region's branding/identity/presentation
config as declared in site.toml, so a frontend (or another client) can learn
everything region-specific without hardcoding it. Covers both the shipped
Triangle pack and a minimal fixture pack (via the SITE_CONFIG_PATH override),
proving the response actually reflects the loaded config rather than a
module-level constant.
"""

from app.site_config import load_site_config


async def test_get_site_returns_triangle_manifest(client):
    resp = await client.get("/api/v1/site")
    assert resp.status_code == 200
    body = resp.json()

    assert body["site"]["name"] == "Triangle Shows"
    assert body["site"]["domain"] == "triangle-shows.net"
    assert body["site"]["uid_host"] == "triangle-shows.org"
    assert body["site"]["timezone"] == "America/New_York"
    assert body["site"]["region_code"] == "NC"
    assert body["city_groups"] == {"Chapel Hill-Carrboro": ["Chapel Hill", "Carrboro"]}
    assert "amber" in body["palettes"]
    assert body["subdomains"][0]["host_prefix"] == "durm"
    assert len(body["links"]) == 8


async def test_get_site_matches_load_site_config(client):
    # The endpoint must not drift from the same singleton every other backend
    # module reads — no separate hand-maintained response shape.
    resp = await client.get("/api/v1/site")
    site = load_site_config()
    assert resp.json()["site"]["name"] == site.site.name
    assert resp.json()["site"]["domain"] == site.site.domain


async def test_get_site_reflects_an_overridden_pack(tmp_path, site_config_env, client):
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

        [city_groups]
        "Fixtureville" = ["Fixture East", "Fixture West"]

        [palettes.amber]
        label = "Amber"
        accent = "#c87941"
        [palettes.amber.vars]
        "--bg" = "#000000"
        """
    )
    site_config_env(path)

    resp = await client.get("/api/v1/site")
    assert resp.status_code == 200
    body = resp.json()
    assert body["site"]["name"] == "Fixture Region"
    assert body["site"]["domain"] == "fixture.example"
    # uid_host wasn't given, so it defaults to domain (decision 8).
    assert body["site"]["uid_host"] == "fixture.example"
    assert body["city_groups"] == {"Fixtureville": ["Fixture East", "Fixture West"]}
    assert body["links"] == []
