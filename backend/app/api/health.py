"""
GET /api/health — returns current status, event/venue counts, last scrape time, and git SHA.

Role: Deprecated alias for GET /api/v1/health; both routes register the same shared
handler (app.api.common.health_check) so the two surfaces cannot drift. Consumed by
Cloud Run, uptime monitors, and the /deploy skill to confirm a new revision is live.
Requires: the shared handler's dependencies (async DB session, ORM models,
HealthResponse schema, GIT_COMMIT env var).
"""
from fastapi import APIRouter

from app.api.common import health_check
from app.schemas import HealthResponse

# --- Router ---
router = APIRouter(prefix="/api/health", tags=["health"], deprecated=True)  # superseded by /api/v1/health (same handler)

router.add_api_route(
    "",
    health_check,
    methods=["GET"],
    response_model=HealthResponse,
    summary="Health check (deprecated alias of /api/v1/health)",
)
