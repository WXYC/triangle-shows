"""Contract guard: the generated OpenAPI schema exposes the v1 surface and neutral schemas.

Locks the published contract so an accidental route/schema rename is caught in CI rather
than by a downstream client. Asserts against app.openapi() directly — the schema is a
pure in-memory document, so no HTTP client or database is needed.
"""

from app.main import app


def test_openapi_exposes_v1_paths_and_neutral_schemas():
    spec = app.openapi()

    paths = spec["paths"]
    for expected in ("/api/v1/events", "/api/v1/events/{event_id}", "/api/v1/venues", "/api/v1/health"):
        assert expected in paths, f"missing path {expected}"

    schemas = spec["components"]["schemas"]
    for expected in ("EventResponse", "VenueResponse", "HealthResponse"):
        assert expected in schemas, f"missing schema {expected}"

    # updated_at is part of the neutral event contract (used by an incremental sync).
    assert "updated_at" in schemas["EventResponse"]["properties"]

    # Internal scraping machinery stays out of the public venue contract.
    assert "scraper_type" not in schemas["VenueResponse"]["properties"]


def test_openapi_marks_legacy_aliases_deprecated():
    spec = app.openapi()
    paths = spec["paths"]
    # The unversioned events + venues + health routes are deprecated aliases...
    assert paths["/api/events"]["get"]["deprecated"] is True
    assert paths["/api/venues"]["get"]["deprecated"] is True
    assert paths["/api/health"]["get"]["deprecated"] is True
    # ...while the v1 surface is not deprecated.
    for v1_path in ("/api/v1/events", "/api/v1/venues", "/api/v1/health"):
        assert paths[v1_path]["get"].get("deprecated", False) is False
