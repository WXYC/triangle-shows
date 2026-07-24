"""Contract guard: the generated OpenAPI schema exposes the v1 surface and neutral schemas.

Locks the published contract so an accidental route/schema rename is caught in CI rather
than by a downstream client. Asserts against app.openapi() directly — the schema is a
pure in-memory document, so no HTTP client or database is needed.
"""

from app.main import app
from app.site_config import load_site_config


def test_openapi_exposes_v1_paths_and_neutral_schemas():
    spec = app.openapi()

    paths = spec["paths"]
    for expected in ("/api/v1/events", "/api/v1/events/{event_id}", "/api/v1/venues", "/api/v1/health", "/api/v1/site"):
        assert expected in paths, f"missing path {expected}"

    schemas = spec["components"]["schemas"]
    for expected in ("EventResponse", "VenueResponse", "HealthResponse", "SiteConfig"):
        assert expected in schemas, f"missing schema {expected}"

    # updated_at is part of the neutral event contract (used by an incremental sync).
    assert "updated_at" in schemas["EventResponse"]["properties"]

    # removed_at (soft tombstone) is part of the contract too, and the opt-in to see
    # tombstoned rows exists on the v1 list only — deprecated surfaces don't grow it.
    assert "removed_at" in schemas["EventResponse"]["properties"]
    v1_params = {p["name"] for p in paths["/api/v1/events"]["get"]["parameters"]}
    assert "include_removed" in v1_params
    deprecated_params = {p["name"] for p in paths["/api/events"]["get"]["parameters"]}
    assert "include_removed" not in deprecated_params

    # source_key is the stable per-event identity contract (issue #8).
    assert "source_key" in schemas["EventResponse"]["properties"]

    # headliner is the best-effort cleaned performer, additive next to the
    # untouched name/artist (issue #18) — downstream resolvers key on it.
    assert "headliner" in schemas["EventResponse"]["properties"]

    # Internal scraping machinery stays out of the public venue contract.
    assert "scraper_type" not in schemas["VenueResponse"]["properties"]


def test_openapi_title_is_pinned():
    # Characterization pin (region-pack epic, issue #62/#63/#64): the FastAPI title
    # is now manifest-driven (Phase 2, issue #64) — this re-derives the same
    # assertion from the shipped Triangle site.toml alongside the original literal,
    # proving the manifest reproduces today's exact bytes.
    spec = app.openapi()
    site = load_site_config().site
    assert spec["info"]["title"] == "Triangle Shows API"
    assert spec["info"]["title"] == f"{site.name} API"


def test_openapi_marks_legacy_aliases_deprecated():
    spec = app.openapi()
    paths = spec["paths"]
    # The unversioned events + venues + health routes are deprecated aliases...
    assert paths["/api/events"]["get"]["deprecated"] is True
    assert paths["/api/venues"]["get"]["deprecated"] is True
    assert paths["/api/health"]["get"]["deprecated"] is True
    # ...while the v1 surface (including the new site manifest) is not deprecated.
    for v1_path in ("/api/v1/events", "/api/v1/venues", "/api/v1/health", "/api/v1/site"):
        assert paths[v1_path]["get"].get("deprecated", False) is False
