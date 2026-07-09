"""
Exposes the GET /api/venues endpoint, returning all venues for the frontend filter UI.

Role: Deprecated alias for GET /api/v1/venues; both routes register the same shared
handler (app.api.common.list_venues) so the two surfaces cannot drift. Called by the
frontend on page load to populate the venue filter sidebar.
Requires: the shared handler's dependencies (async PostgreSQL session, app.models.Venue,
app.schemas.VenueResponse).
"""
from fastapi import APIRouter

from app.api.common import list_venues
from app.schemas import VenueResponse

# --- Router ---

router = APIRouter(prefix="/api/venues", tags=["venues"], deprecated=True)  # superseded by /api/v1/venues

router.add_api_route(
    "",
    list_venues,
    methods=["GET"],
    response_model=list[VenueResponse],
    summary="List all venues (deprecated alias of /api/v1/venues)",
)
