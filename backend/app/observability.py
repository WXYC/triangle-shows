"""
Vendor-free error-capture funnel (issue #118).

Role: the single path every internal-error capture point in the codebase flows
through. app.main and app.scheduler call ONLY the four functions below — never
sentry_sdk and never app.sentry_hook directly — so that deleting
app/sentry_hook.py plus backend/requirements-optional.txt is a complete,
two-file opt-out that leaves the funnel (and the app) working.

Three tiers, cheapest first (see the "mechanism in the engine, identity in the
deployment" design principle this repeats from TELEMETRY_SALT and
site.toml's [integrations].google_analytics_id):

  1. Every report_error() call logs at ERROR with a stack trace on stderr —
     always on, no vendor, no dependency. RedactingFormatter, installed in
     app.main.configure_logging, already scrubs credentials out of it.
  2. send_alert() posts to an operator-configured ALERT_WEBHOOK_URL, or logs
     instead when it's unset — opt-in, no vendor named (a bare {"text": ...}
     JSON POST is what Slack and Mattermost accept, and Discord accepts it too
     when the operator appends "/slack" to the webhook URL). Inert until the
     scrape-health digest (issue #86) lands — nothing calls this yet.
  3. report_error() additionally forwards to app.sentry_hook when it is
     importable and SENTRY_DSN is set — opt-in, isolated, deletable.

Requires: app.config.settings (SENTRY_DSN, ALERT_WEBHOOK_URL), app.redaction (the
same credential scrubber applied at every other sink in this codebase).
"""

import logging
from typing import Optional

import httpx

from app.config import settings
from app.redaction import redact_credentials

try:
    from app import sentry_hook
except ImportError:  # pragma: no cover - exercised by the two-deletion opt-out
    sentry_hook = None

logger = logging.getLogger(__name__)

# A short, fixed timeout: an alert POST must never hang the caller (a scrape, a
# scheduler job) waiting on a slow or dead webhook endpoint.
_ALERT_TIMEOUT = httpx.Timeout(10.0)


def report_error(exc: BaseException, *, where: str, context: Optional[dict] = None) -> None:
    """Log exc at ERROR with a stack trace, then forward it to the optional tracker.

    A plain `def`, not async: both logger.error and sentry_hook.capture_exception
    are synchronous, and the callers span both worlds — APScheduler's add_listener
    takes a synchronous callback, while the FastAPI exception handler is async (and
    can call this directly without awaiting it).

    `where` identifies the capture point (e.g. "main.lifespan.startup",
    "scheduler.job_error") for triage; `context` is optional structured detail
    (e.g. {"job_id": ...}) attached to the tracker event when present.

    The forward is gated on SENTRY_DSN as well as the hook's presence. The hook
    imports whenever the file exists — which, in any deployment that installed
    requirements-optional.txt, is always — so without the DSN check every error would
    call into an uninitialized client. That is a no-op today, but it makes the error
    path depend on a third party's no-op staying a no-op; tier 1 must not.
    """
    logger.error("[%s] %s", where, exc, exc_info=exc)
    if sentry_hook is not None and settings.SENTRY_DSN:
        sentry_hook.capture_exception(exc, where=where, context=context)


def _alert_client() -> httpx.AsyncClient:
    """Factory for send_alert's HTTP client, isolated so tests can substitute a
    MockTransport without touching the shared httpx module.

    Deliberately its own short-lived plain client — not the scrapers'
    app.scrapers.base.BaseScraper.http_client() factory, whose browser headers
    are meant for fetching venue listing pages, not posting to a webhook.
    """
    return httpx.AsyncClient(timeout=_ALERT_TIMEOUT)


async def send_alert(text: str) -> None:
    """POST {"text": text} to settings.ALERT_WEBHOOK_URL; log instead when it's unset.

    Never raises — an alerting failure must not break whatever triggered the
    alert (a scrape, a digest job). redact_credentials runs on `text` even though
    callers are expected to have already scrubbed it: this is a fourth credential
    sink (a future scrape-health digest embeds ScrapeLog.error_message) and
    redact_credentials is idempotent, so double-scrubbing here is free.
    """
    scrubbed = redact_credentials(text)
    if not settings.ALERT_WEBHOOK_URL:
        logger.info("[alert] %s", scrubbed)
        return
    try:
        async with _alert_client() as client:
            response = await client.post(settings.ALERT_WEBHOOK_URL, json={"text": scrubbed})
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        # Never str(e), never the URL, and never routed through report_error: the
        # webhook's secret lives in the URL PATH
        # (hooks.slack.com/services/T.../B.../<secret>), which redact_credentials
        # — a query-*parameter* denylist — cannot scrub, and
        # HTTPStatusError.__str__ embeds the full URL. A failed webhook post is a
        # WARNING log line with the exception type and status, full stop.
        logger.warning(
            "[alert] webhook post failed: %s (status=%s)",
            type(e).__name__,
            e.response.status_code,
        )
    except Exception as e:
        logger.warning("[alert] webhook post failed: %s", type(e).__name__)


def flush_errors() -> None:
    """Block until any buffered tracker events are sent. A vendor-free delegate:
    calling sentry_sdk.flush() directly from app.main would hard-fail for an
    operator who deleted requirements-optional.txt but not sentry_hook.py's
    guarded import — this stays a no-op instead when the hook is absent."""
    if sentry_hook is not None:
        sentry_hook.flush()


def init_error_tracking() -> None:
    """Initialize the optional tracker. A vendor-free delegate, same shape as
    flush_errors() above — a no-op when sentry_hook is deleted or SENTRY_DSN is
    unset. app.main reaches Sentry init only through this function, never
    app.sentry_hook.init_sentry() directly, so deleting the vendor module can
    never turn into an ImportError at boot."""
    if sentry_hook is not None:
        sentry_hook.init_sentry()
