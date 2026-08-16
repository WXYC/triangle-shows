"""
The only file under backend/app/** permitted to name a monitoring vendor.

Role: a guarded, deletable Sentry (https://sentry.io) integration. app.observability
imports this module behind a ``try/except ImportError`` so a fork can delete this
file outright — the region-pack DoD grep forbids operator-specific literals in
backend/app/**, and mechanism belongs in the engine while identity belongs in the
deployment — and app.observability.report_error/flush_errors/init_error_tracking
keep working with the tracker simply absent. app.main and app.scheduler must never
import this module (or sentry_sdk) directly; only app.observability's four entry
points reference it, so deleting this file plus requirements-optional.txt is a
complete, two-file opt-out.

This deliberately *reimplements* wxyc_fastapi.observability.sentry.init_sentry,
which is already almost exactly this shape, rather than depending on it: pulling a
WXYC-org package into backend/app/** would put operator identity inside
region-agnostic engine code — precisely what the DoD grep exists to prevent. Don't
"fix" this duplication into a dependency.

Requires (only when SENTRY_DSN is set): the optional ``sentry-sdk`` package
(backend/requirements-optional.txt). The import below is guarded, so installing
only requirements.txt leaves this module import-safe with every function a no-op.
"""

import logging

try:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.httpx import HttpxIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
except ImportError:  # pragma: no cover - exercised by the two-deletion opt-out
    sentry_sdk = None

from app.config import settings
from app.redaction import redact_credentials

logger = logging.getLogger(__name__)


# --- Redaction of tracker events ---


def _scrub(value):
    """Recursively apply redact_credentials to every string in a Sentry event.

    TICKETMASTER_API_KEY travels as a query parameter, so httpx.HTTPStatusError
    embeds a live credential in its own str() — which lands in
    exception.values[*].value, logentry, and breadcrumb data. Walking the whole
    event dict, rather than naming those paths individually, means a future SDK
    version that reshapes the payload doesn't silently reopen a hole.
    """
    if isinstance(value, str):
        return redact_credentials(value)
    if isinstance(value, dict):
        return {key: _scrub(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _before_send(event, hint):
    """Sentry's before_send hook: scrub credentials before an event leaves the process.

    Required, not optional — see backend/README.md's redaction sink table. This
    hook sees *error* events only; transaction events route through the separate
    before_send_transaction hook, which is exactly why init_sentry disables tracing
    outright below rather than trusting this scrubber to cover a path it never sees.
    """
    return _scrub(event)


# --- Initialization ---


def init_sentry() -> None:
    """Initialize the Sentry SDK. A no-op when the SDK is missing or SENTRY_DSN is empty.

    Call only through app.observability.init_error_tracking() — never directly —
    so deleting this module can never turn into an ImportError at boot.
    """
    if sentry_sdk is None or not settings.SENTRY_DSN:
        return

    # Imported here, not at module level: avoids a needless site_config load (and
    # its own failure mode) on every import of this module when the tracker is off.
    from app.site_config import load_site_config

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.APP_ENV,
        integrations=[
            FastApiIntegration(),
            HttpxIntegration(),
            # event_level=None: report_error() both logs at ERROR (exc_info=...) and
            # calls capture_exception() explicitly, so the default event_level=ERROR
            # would make every funneled error a candidate for a second, implicit
            # event. Breadcrumbs (the separate, unaffected `level=` default) still
            # ride along on error events.
            LoggingIntegration(event_level=None),
        ],
        # A deliberate divergence from the canonical wxyc_fastapi shape this
        # otherwise mirrors (which defaults to 1.0): this deployment wants errors,
        # not APM. With tracing on, HttpxIntegration emits a span per outbound
        # request carrying the full URL — ?apikey=<live key> on every Ticketmaster
        # call, at 100% sampling — and spans ship inside *transaction* events, which
        # never reach _before_send (that's before_send_transaction, a separate
        # hook the scrubber above does not cover). Tracing off outright closes that
        # path rather than trying to scrub it. HttpxIntegration itself stays
        # enabled: with tracing off it still records outbound-request breadcrumbs,
        # which do ride error events through before_send.
        traces_sample_rate=0.0,
        before_send=_before_send,
    )
    # service.name tag, mirroring the wxyc_fastapi shape, so multiple regions
    # sharing one Sentry project stay distinguishable.
    sentry_sdk.set_tag("service.name", load_site_config().site.name)
    logger.info("Sentry initialized (environment=%s)", settings.APP_ENV)


# --- Delegate operations (called only from app.observability) ---


def capture_exception(exc: BaseException, *, where: str, context: dict | None = None) -> None:
    """Send exc to Sentry, tagged with where it originated. A no-op without the SDK."""
    if sentry_sdk is None:
        return
    report_context = {"where": where}
    if context:
        report_context.update(context)
    sentry_sdk.set_context("report_error", report_context)
    sentry_sdk.capture_exception(exc)


def flush() -> None:
    """Block until any buffered events are sent. A no-op without the SDK."""
    if sentry_sdk is not None:
        sentry_sdk.flush()
