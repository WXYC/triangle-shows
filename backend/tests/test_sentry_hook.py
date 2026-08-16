"""Tests for app.sentry_hook — the only file in backend/app/** permitted to name Sentry.

Skipped as a *module*, not failed, when the two-deletion opt-out (issue #118) has
been performed — deleting app/sentry_hook.py, or backend/requirements-optional.txt
(which carries the sentry-sdk pin these tests exercise), must leave `pytest` green.

The guard is on app.sentry_hook rather than sentry_sdk alone, deliberately:
backend/requirements-dev.txt pins sentry-sdk directly (see that file's own comment
for why), so after the two deletions sentry_sdk is *still installed* — a guard on
the SDK alone would not skip this module, and `from app import sentry_hook` below
would then error at collection instead of skipping cleanly.
"""

import json

import pytest

pytest.importorskip("app.sentry_hook")
pytest.importorskip("sentry_sdk")

import sentry_sdk  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sentry_sdk.integrations.fastapi import FastApiIntegration  # noqa: E402
from sentry_sdk.integrations.httpx import HttpxIntegration  # noqa: E402
from sentry_sdk.integrations.logging import LoggingIntegration  # noqa: E402
from sentry_sdk.transport import Transport  # noqa: E402

from app import sentry_hook  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import _unhandled_exception_handler  # noqa: E402
from app.observability import report_error  # noqa: E402

FAKE_DSN = "https://public@o0.ingest.sentry.io/1"

# Shaped exactly like the URL the Ticketmaster scraper builds — same fixture data
# as tests/test_redaction.py, reused deliberately so both suites pin the same shape.
FAKE_KEY = "s3cr3tKEYvalue0123456789abcdefgh"
TM_URL = (
    "https://app.ticketmaster.com/discovery/v2/events.json"
    f"?apikey={FAKE_KEY}&venueId=KovZpZAdEEvA&size=200&page=0&sort=date%2Casc"
)


class _CapturingTransport(Transport):
    """A Sentry Transport that records error events in-memory instead of sending them."""

    def __init__(self, options=None):
        super().__init__(options)
        self.events = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.type == "event":
                self.events.append(item.payload.json)


@pytest.fixture
def sentry_transport(monkeypatch):
    """Init the real SDK with a capturing transport in place of the network, and
    tear the process-global client down afterward.

    sentry_sdk.init() installs a client that outlives the test (it's process-global,
    per xdist worker), so a later test going through report_error would otherwise
    hit a live client with a real-looking DSN and attempt actual network I/O. The
    "exactly one event" tests below assert off this transport's recorded events, not
    off a capture_exception mock — a mock would never see the duplicate that Sentry's
    own ASGI integration produces on a request path, since that capture never goes
    through app.observability at all.
    """
    monkeypatch.setattr(settings, "SENTRY_DSN", FAKE_DSN)
    sentry_hook.init_sentry()
    client = sentry_sdk.get_client()
    # init_sentry built a real HttpTransport (a urllib3 PoolManager plus a background
    # worker). Overwriting client.transport below orphans it, and client.close() would
    # then close only the capturing stand-in — so hold onto the real one and kill it in
    # teardown. Its worker thread never starts (it's lazy on first submit), so nothing
    # reaches the network either way; this is about not leaking a pool per fixture use.
    real_transport = client.transport
    cap = _CapturingTransport({"dsn": FAKE_DSN})
    client.transport = cap
    try:
        yield cap
    finally:
        client.close()
        if real_transport is not None:
            real_transport.kill()
        # init_sentry also set a service.name tag on the *global* scope, which outlives
        # the client teardown above and would otherwise ride into later tests.
        sentry_sdk.get_global_scope().clear()
        sentry_sdk.get_global_scope().set_client(None)


# --- init_sentry -----------------------------------------------------------------


class TestInitSentry:
    def test_skips_init_when_dsn_is_empty(self, monkeypatch):
        calls = []
        monkeypatch.setattr(settings, "SENTRY_DSN", "")
        monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))

        sentry_hook.init_sentry()

        assert calls == []

    def test_initializes_with_the_configured_dsn_and_environment(self, monkeypatch):
        calls = []
        monkeypatch.setattr(settings, "SENTRY_DSN", FAKE_DSN)
        monkeypatch.setattr(settings, "APP_ENV", "staging")
        monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
        monkeypatch.setattr(sentry_sdk, "set_tag", lambda *a, **k: None)

        sentry_hook.init_sentry()

        assert len(calls) == 1
        assert calls[0]["dsn"] == FAKE_DSN
        assert calls[0]["environment"] == "staging"

    def test_disables_tracing_outright(self, monkeypatch):
        """A deliberate divergence from the canonical wxyc_fastapi shape (which
        defaults traces_sample_rate to 1.0): HttpxIntegration with tracing on would
        emit a per-request span carrying the full Ticketmaster URL --- including
        the live ?apikey= --- and spans ship in transaction events, which never
        reach before_send."""
        calls = []
        monkeypatch.setattr(settings, "SENTRY_DSN", FAKE_DSN)
        monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
        monkeypatch.setattr(sentry_sdk, "set_tag", lambda *a, **k: None)

        sentry_hook.init_sentry()

        assert calls[0]["traces_sample_rate"] == 0.0

    def test_passes_event_level_none_to_the_logging_integration(self, monkeypatch):
        """LoggingIntegration defaults event_level to ERROR, which would make every
        report_error() call (which already logs at ERROR AND calls
        capture_exception explicitly) a candidate for a second, implicit event.
        event_level=None sets LoggingIntegration._handler to None; the default
        would instead construct an EventHandler."""
        calls = []
        monkeypatch.setattr(settings, "SENTRY_DSN", FAKE_DSN)
        monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
        monkeypatch.setattr(sentry_sdk, "set_tag", lambda *a, **k: None)

        sentry_hook.init_sentry()

        integrations = calls[0]["integrations"]
        logging_integrations = [i for i in integrations if isinstance(i, LoggingIntegration)]
        assert len(logging_integrations) == 1
        assert logging_integrations[0]._handler is None, "event_level was not passed as None"

    def test_includes_fastapi_and_httpx_integrations(self, monkeypatch):
        calls = []
        monkeypatch.setattr(settings, "SENTRY_DSN", FAKE_DSN)
        monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
        monkeypatch.setattr(sentry_sdk, "set_tag", lambda *a, **k: None)

        sentry_hook.init_sentry()

        integration_types = {type(i) for i in calls[0]["integrations"]}
        assert FastApiIntegration in integration_types
        assert HttpxIntegration in integration_types

    def test_passes_a_before_send_hook(self, monkeypatch):
        calls = []
        monkeypatch.setattr(settings, "SENTRY_DSN", FAKE_DSN)
        monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
        monkeypatch.setattr(sentry_sdk, "set_tag", lambda *a, **k: None)

        sentry_hook.init_sentry()

        assert calls[0]["before_send"] is sentry_hook._before_send

    def test_sets_the_service_name_tag_from_the_site_config(self, monkeypatch):
        tags = []
        monkeypatch.setattr(settings, "SENTRY_DSN", FAKE_DSN)
        monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: None)
        monkeypatch.setattr(sentry_sdk, "set_tag", lambda *a: tags.append(a))

        sentry_hook.init_sentry()

        assert ("service.name", "Triangle Shows") in tags


# --- capture_exception / flush ----------------------------------------------------


class TestCaptureExceptionAndFlush:
    def test_capture_exception_is_a_no_op_without_the_sdk(self, monkeypatch):
        monkeypatch.setattr(sentry_hook, "sentry_sdk", None)
        sentry_hook.capture_exception(ValueError("boom"), where="test")  # must not raise

    def test_capture_exception_is_a_no_op_without_a_dsn(self, monkeypatch):
        """The gate lives here, beside init_sentry's identical check, rather than in
        app.observability's caller: this is the only file allowed to know the tracker
        exists, and one file holding both checks is what stops them drifting apart.
        Without it, every error in a deployment that installed requirements-optional.txt
        (where the hook always imports) would call into an uninitialized client.
        """
        calls = []
        monkeypatch.setattr(settings, "SENTRY_DSN", "")
        monkeypatch.setattr(sentry_sdk, "capture_exception", lambda exc: calls.append(exc))

        sentry_hook.capture_exception(ValueError("boom"), where="test.no_dsn")

        assert calls == []

    def test_flush_is_a_no_op_without_the_sdk(self, monkeypatch):
        monkeypatch.setattr(sentry_hook, "sentry_sdk", None)
        sentry_hook.flush()  # must not raise


# --- before_send scrubbing --------------------------------------------------------


class TestBeforeSendScrubbing:
    def test_scrubs_a_well_formed_credential_url_in_an_exception_value(self):
        event = {
            "exception": {
                "values": [
                    {"type": "HTTPStatusError", "value": f"Client error '401' for url '{TM_URL}'"}
                ]
            }
        }

        scrubbed = sentry_hook._before_send(event, {})

        rendered = json.dumps(scrubbed)
        assert FAKE_KEY not in rendered
        assert "venueId=KovZpZAdEEvA" in rendered

    def test_scrubs_a_truncated_mid_url_credential(self):
        """The value pattern has no trailing delimiter to anchor on when the
        credential is the last query parameter — the same shape test_redaction.py
        pins for the base helper."""
        truncated_url = (
            "https://app.ticketmaster.com/discovery/v2/events.json"
            f"?venueId=KovZpZAdEEvA&apikey={FAKE_KEY}"
        )
        event = {"exception": {"values": [{"value": f"failed fetching {truncated_url}"}]}}

        scrubbed = sentry_hook._before_send(event, {})

        rendered = json.dumps(scrubbed)
        assert FAKE_KEY not in rendered
        assert "venueId=KovZpZAdEEvA" in rendered

    def test_scrubs_breadcrumb_data(self):
        """HttpxIntegration breadcrumbs (outbound-request records) ride error
        events through before_send even with tracing disabled."""
        event = {
            "breadcrumbs": {
                "values": [{"category": "httplib", "type": "http", "data": {"url": TM_URL}}]
            }
        }

        scrubbed = sentry_hook._before_send(event, {})

        assert FAKE_KEY not in json.dumps(scrubbed)

    def test_non_credential_data_survives(self):
        event = {"exception": {"values": [{"value": "ValueError: not a URL at all"}]}}

        scrubbed = sentry_hook._before_send(event, {})

        assert scrubbed["exception"]["values"][0]["value"] == "ValueError: not a URL at all"


# --- End-to-end: exactly one event -------------------------------------------------


class TestExactlyOneEvent:
    def test_report_error_produces_exactly_one_event(self, sentry_transport):
        try:
            raise ValueError("scrape blew up")
        except ValueError as exc:
            report_error(exc, where="test.exactly_one")

        sentry_sdk.flush()

        assert len(sentry_transport.events) == 1
        assert sentry_transport.events[0]["exception"]["values"][0]["value"] == "scrape blew up"

    async def test_an_asgi_500_produces_exactly_one_event(self, sentry_transport):
        """Builds its own fresh app, and registers the route *after* init_sentry()
        (which the sentry_transport fixture already called): Sentry's FastAPI
        enrichment patches fastapi.routing.get_request_handler, consumed when a
        route is registered — the shared app.main.app singleton is already built
        by conftest.py's import-time `from app.main import app`, so patching that
        happens later would land too late to enrich its routes.

        ServerErrorMiddleware re-raises after invoking a bare-Exception handler, so
        both this test's own report_error() call (via the real
        _unhandled_exception_handler) and Sentry's own ASGI capture see the
        exception — DedupeIntegration is relied on to collapse the identical
        exception instance into one event.
        """
        local_app = FastAPI()
        local_app.add_exception_handler(Exception, _unhandled_exception_handler)

        @local_app.get("/boom")
        async def _boom():
            raise RuntimeError("asgi enrichment test")

        transport = ASGITransport(app=local_app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
            response = await http_client.get("/boom")

        assert response.status_code == 500
        assert response.json() == {"detail": "Internal Server Error"}

        sentry_sdk.flush()
        assert len(sentry_transport.events) == 1


# --- Scope isolation ---------------------------------------------------------------


class TestCaptureExceptionScopeIsolation:
    def test_context_does_not_ride_along_on_later_unrelated_events(self, sentry_transport):
        """capture_exception must fork a scope rather than write to the isolation scope.

        sentry_sdk.set_context() targets the *isolation* scope, which outlives the
        capture. Requests fork their own, but startup, the background startup scrape,
        and every scheduler job share one isolation scope for the process's lifetime —
        so a `where`/`job_id` written there stays attached and misattributes later
        events, exactly when someone is triaging an incident.
        """
        sentry_hook.capture_exception(
            ValueError("the scheduled job failed"),
            where="scheduler.job_error",
            context={"job_id": "scrape_indie"},
        )
        sentry_sdk.capture_exception(ValueError("something else entirely"))
        sentry_sdk.flush()

        assert len(sentry_transport.events) == 2
        first, second = sentry_transport.events
        assert first["contexts"]["report_error"]["where"] == "scheduler.job_error"
        assert "report_error" not in second.get("contexts", {}), (
            "stale context leaked onto an unrelated later event"
        )
