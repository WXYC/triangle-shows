"""
GET /api/health — returns current status, event/venue counts, last scrape time, and git SHA.

Role: Deprecated alias for GET /api/v1/health; both routes register the same shared
handler (app.api.common.health_check) so the two surfaces cannot drift. Consumed by
Railway's deploy healthcheck (railway.json), uptime monitors, and tools/wait_for_deploy.py
to confirm a new revision is live.

Upstream's 503-when-stale behaviour is deliberately not adopted: this path is Railway's
deploy healthcheck, so a stale ScrapeLog would block every deploy, including the one that
fixes the scrape.

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
    # HEAD as well as GET: UptimeRobot sends HEAD, and a GET-only route answers it with
    # 404. Starlette drops the body for HEAD.
    methods=["GET", "HEAD"],
    response_model=HealthResponse,
    summary="Health check (deprecated alias of /api/v1/health)",
)
