"""Upstream's query-API gate (#62), carried into the WXYC fork switched off.

Upstream closes `GET /api/events` and `GET /api/events/{id}` while a registration process is
designed. In the fork those are deprecated aliases of /api/v1, and the decision for the
upstream merge (WXYC/triangle-shows#129) was that the gate comes in as code but stays off:
PUBLIC_QUERY_API_ENABLED defaults to true here. /api/v1 is the contract WXYC
Backend-Service imports from and is never gated.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


# raise_server_exceptions=False so an endpoint that reaches the database in this
# no-database harness surfaces as a 500 response rather than an exception — that
# distinction is what several assertions below rest on.
@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def closed(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_QUERY_API_ENABLED", False)


class TestOpenByDefault:
    def test_open_by_default(self):
        from app.config import Settings

        assert Settings().PUBLIC_QUERY_API_ENABLED is True

    @pytest.mark.parametrize("path", ["/api/events", "/api/events/1"])
    def test_not_refused_by_default(self, client, path):
        assert client.get(path).status_code != 403


class TestWhenSwitchedOn:
    @pytest.mark.parametrize("path", ["/api/events", "/api/events/1"])
    def test_refused(self, client, closed, path):
        assert client.get(path).status_code == 403

    @pytest.mark.parametrize("path", ["/api/events", "/api/events/1"])
    def test_refused_before_touching_the_database(self, client, closed, path):
        """403 rather than 500 in a harness with no database: the gate short-circuits."""
        assert client.get(path).status_code != 500

    def test_the_refusal_names_what_is_still_open(self, client, closed):
        detail = client.get("/api/events").json()["detail"]
        assert "/api/v1/events" in detail
        assert "/feeds/events.ics" in detail

    @pytest.mark.parametrize("path", ["/api/v1/events", "/api/v1/events/1", "/feeds/events.ics"])
    def test_v1_and_the_feed_are_never_gated(self, client, closed, path):
        # 500/404 here is the no-database harness, not a refusal.
        assert client.get(path).status_code != 403

    def test_the_routes_still_exist(self):
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/api/events" in paths
        assert "/api/events/{event_id}" in paths
