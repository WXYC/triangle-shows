"""Tests for app.observability — the vendor-free error-capture funnel (issue #118).

These tests must all pass with app/sentry_hook.py and backend/requirements-optional.txt
deleted (the "two-deletion opt-out"): every test here either exercises behavior that
has nothing to do with the optional tracker, or explicitly simulates the tracker's
absence by monkeypatching ``observability.sentry_hook`` to ``None``. Tests that need
the real SDK live in ``test_sentry_hook.py``, guarded by
``pytest.importorskip("app.sentry_hook")``.
"""

import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app import main as app_main
from app import observability
from app.config import settings

FAKE_KEY = "s3cr3tKEYvalue0123456789abcdefgh"
WEBHOOK_URL = "https://hooks.slack.com/services/T000/B000/s3cr3twebhooksecret"


# --- report_error ------------------------------------------------------------


class TestReportError:
    def test_logs_at_error_with_a_stack_trace(self, caplog):
        caplog.set_level(logging.ERROR, logger="app.observability")
        try:
            raise ValueError("scrape blew up")
        except ValueError as exc:
            observability.report_error(exc, where="test.report_error")

        assert "test.report_error" in caplog.text
        assert "scrape blew up" in caplog.text
        assert "Traceback" in caplog.text

    def test_forwards_to_the_tracker_hook_when_present(self, monkeypatch):
        """Unconditionally, whenever the hook imports — whether the tracker is actually
        switched on is the hook's own decision (it gates on SENTRY_DSN), deliberately
        not re-checked here, which would put a vendor-named setting back into this
        vendor-free module. See test_sentry_hook.py for the gate itself."""
        calls = []
        monkeypatch.setattr(
            observability,
            "sentry_hook",
            SimpleNamespace(capture_exception=lambda exc, **kw: calls.append((exc, kw))),
        )
        exc = ValueError("boom")

        observability.report_error(exc, where="test.forward", context={"venue": "red-hat"})

        assert len(calls) == 1
        forwarded_exc, kwargs = calls[0]
        assert forwarded_exc is exc
        assert kwargs["where"] == "test.forward"
        assert kwargs["context"] == {"venue": "red-hat"}

    def test_works_when_the_tracker_hook_is_absent(self, monkeypatch, caplog):
        """Simulates the two-deletion opt-out: app/sentry_hook.py deleted, so
        app.observability's guarded import bound sentry_hook to None."""
        caplog.set_level(logging.ERROR, logger="app.observability")
        monkeypatch.setattr(observability, "sentry_hook", None)

        observability.report_error(ValueError("boom"), where="test.absent")

        assert "test.absent" in caplog.text


class TestFlushAndInitDelegateOverTheGuardedHook:
    def test_flush_errors_delegates_when_the_hook_is_present(self, monkeypatch):
        calls = []
        monkeypatch.setattr(observability, "sentry_hook", SimpleNamespace(flush=lambda: calls.append(True)))
        observability.flush_errors()
        assert calls == [True]

    def test_flush_errors_is_a_no_op_when_the_hook_is_absent(self, monkeypatch):
        monkeypatch.setattr(observability, "sentry_hook", None)
        observability.flush_errors()  # must not raise

    def test_init_error_tracking_delegates_when_the_hook_is_present(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            observability, "sentry_hook", SimpleNamespace(init_sentry=lambda: calls.append(True))
        )
        observability.init_error_tracking()
        assert calls == [True]

    def test_init_error_tracking_is_a_no_op_when_the_hook_is_absent(self, monkeypatch):
        monkeypatch.setattr(observability, "sentry_hook", None)
        observability.init_error_tracking()  # must not raise


# --- send_alert ----------------------------------------------------------------


def _mock_client_factory(handler):
    """A drop-in replacement for observability._alert_client backed by MockTransport."""

    def _factory():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return _factory


class TestSendAlert:
    async def test_posts_the_text_payload_when_the_webhook_url_is_set(self, monkeypatch):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"ok": True})

        monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", WEBHOOK_URL)
        monkeypatch.setattr(observability, "_alert_client", _mock_client_factory(handler))

        await observability.send_alert("scrape health: red-hat is broken")

        assert len(requests) == 1
        assert str(requests[0].url) == WEBHOOK_URL
        assert json.loads(requests[0].content) == {"text": "scrape health: red-hat is broken"}

    async def test_logs_instead_of_posting_when_the_webhook_url_is_empty(self, monkeypatch, caplog):
        caplog.set_level(logging.INFO, logger="app.observability")
        monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", "")

        def _unexpected_client():
            raise AssertionError("send_alert must not build a client when the URL is unset")

        monkeypatch.setattr(observability, "_alert_client", _unexpected_client)

        await observability.send_alert("nothing configured")

        assert "nothing configured" in caplog.text

    async def test_swallows_a_failed_post_without_raising(self, monkeypatch):
        def handler(request):
            return httpx.Response(500, text="server error")

        monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", WEBHOOK_URL)
        monkeypatch.setattr(observability, "_alert_client", _mock_client_factory(handler))

        await observability.send_alert("alert during an outage")  # must not raise

    async def test_a_failed_post_logs_only_the_exception_type_and_status(self, monkeypatch, caplog):
        caplog.set_level(logging.WARNING, logger="app.observability")

        def handler(request):
            return httpx.Response(503, text="unavailable")

        monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", WEBHOOK_URL)
        monkeypatch.setattr(observability, "_alert_client", _mock_client_factory(handler))

        await observability.send_alert("alert text")

        assert "HTTPStatusError" in caplog.text
        assert "503" in caplog.text

    async def test_a_failed_post_never_writes_the_webhook_url_to_the_log(self, monkeypatch, caplog):
        """The webhook URL's secret lives in the URL PATH
        (hooks.slack.com/services/T.../B.../<secret>), which redact_credentials — a
        query-*parameter* denylist — cannot scrub. This must never reach caplog."""
        caplog.set_level(logging.WARNING, logger="app.observability")

        def handler(request):
            return httpx.Response(500, text="server error")

        monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", WEBHOOK_URL)
        monkeypatch.setattr(observability, "_alert_client", _mock_client_factory(handler))

        await observability.send_alert("alert text")

        assert WEBHOOK_URL not in caplog.text
        assert "s3cr3twebhooksecret" not in caplog.text

    async def test_a_non_http_failure_also_never_leaks_the_webhook_url(self, monkeypatch, caplog):
        caplog.set_level(logging.WARNING, logger="app.observability")

        def handler(request):
            raise httpx.ConnectError("connection refused", request=request)

        monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", WEBHOOK_URL)
        monkeypatch.setattr(observability, "_alert_client", _mock_client_factory(handler))

        await observability.send_alert("alert text")

        assert "ConnectError" in caplog.text
        assert WEBHOOK_URL not in caplog.text
        assert "s3cr3twebhooksecret" not in caplog.text

    async def test_redacts_a_credential_bearing_url_in_the_payload(self, monkeypatch):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200)

        monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", WEBHOOK_URL)
        monkeypatch.setattr(observability, "_alert_client", _mock_client_factory(handler))

        text = (
            "Ticketmaster scrape failed: https://app.ticketmaster.com/discovery/v2/"
            f"events.json?apikey={FAKE_KEY}&venueId=1"
        )

        await observability.send_alert(text)

        posted_text = json.loads(requests[0].content)["text"]
        assert FAKE_KEY not in posted_text
        assert "apikey=" in posted_text
        assert "venueId=1" in posted_text


# --- Capture point: main.py::_startup_scrape ------------------------------------


class TestStartupScrapeCapturePoint:
    async def test_a_startup_scrape_failure_is_reported_not_swallowed(self, monkeypatch):
        reported = []
        monkeypatch.setattr(app_main, "report_error", lambda exc, **kw: reported.append((exc, kw)))

        def _raising_session_factory():
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(app_main, "async_session", _raising_session_factory)

        await app_main._startup_scrape()  # must not raise: non-fatal by design

        assert len(reported) == 1
        exc, kwargs = reported[0]
        assert isinstance(exc, RuntimeError)
        assert kwargs["where"] == "main._startup_scrape"


# --- Capture point: main.py's lifespan (pre-yield startup sequence) -------------


class TestLifespanCapturePoint:
    """httpx's ASGITransport never drives the app lifespan (conftest.py:164), so
    these tests enter the context manager directly."""

    async def test_a_failing_migration_is_reported_flushed_and_reraised(self, monkeypatch):
        reported = []
        flushed = []
        monkeypatch.setattr(app_main, "report_error", lambda exc, **kw: reported.append((exc, kw)))
        monkeypatch.setattr(app_main, "flush_errors", lambda: flushed.append(True))

        def _boom():
            raise RuntimeError("migration exploded")

        monkeypatch.setattr(app_main, "_run_migrations", _boom)

        dummy_app = SimpleNamespace(state=SimpleNamespace())

        with pytest.raises(RuntimeError, match="migration exploded"):
            async with app_main.lifespan(dummy_app):
                pass  # unreachable: the context manager raises before yielding

        assert len(reported) == 1
        assert flushed == [True]

    async def test_a_failing_seed_is_reported_flushed_and_reraised(self, monkeypatch):
        """main.py:112's `await seed_venues()` is adjacent to migrations and just as
        unguarded and fatal. A seed failure propagates out of the lifespan where an
        ASGI-level tracker integration cannot see it, crash-looping the container
        with exactly the no-signal symptom this issue closes — pinned separately so
        a future edit that narrows the try block to migrations alone regresses
        silently rather than loudly.
        """
        reported = []
        flushed = []
        monkeypatch.setattr(app_main, "report_error", lambda exc, **kw: reported.append((exc, kw)))
        monkeypatch.setattr(app_main, "flush_errors", lambda: flushed.append(True))
        monkeypatch.setattr(app_main, "_run_migrations", lambda: None)

        async def _boom():
            raise RuntimeError("seed exploded")

        monkeypatch.setattr(app_main, "seed_venues", _boom)

        dummy_app = SimpleNamespace(state=SimpleNamespace())

        with pytest.raises(RuntimeError, match="seed exploded"):
            async with app_main.lifespan(dummy_app):
                pass  # unreachable: the context manager raises before yielding

        assert len(reported) == 1
        assert flushed == [True]

    async def test_a_failure_after_the_scrape_task_starts_still_cancels_it(self, monkeypatch):
        """The startup scrape task is created *inside* the guarded block, so a later
        failure (a bad cron expression, a scheduler that won't start) re-raises without
        ever yielding — and the shutdown branch that normally cancels the task only runs
        after a yield. Without an explicit cancel on the failure path the process dies
        with a live scrape pending, holding a session nobody will close.
        """
        monkeypatch.setattr(app_main, "report_error", lambda exc, **kw: None)
        monkeypatch.setattr(app_main, "flush_errors", lambda: None)
        monkeypatch.setattr(app_main, "_run_migrations", lambda: None)
        monkeypatch.setattr(settings, "RUN_STARTUP_SCRAPE", True)
        monkeypatch.setattr(settings, "ENABLE_SCHEDULER", True)

        async def _noop_seed():
            return None

        async def _long_scrape():
            await asyncio.sleep(3600)

        def _boom():
            raise RuntimeError("bad cron expression")

        monkeypatch.setattr(app_main, "seed_venues", _noop_seed)
        monkeypatch.setattr(app_main, "_startup_scrape", _long_scrape)
        monkeypatch.setattr(app_main, "configure_scheduler", _boom)

        dummy_app = SimpleNamespace(state=SimpleNamespace())

        with pytest.raises(RuntimeError, match="bad cron expression"):
            async with app_main.lifespan(dummy_app):
                pass  # unreachable: the context manager raises before yielding

        task = dummy_app.state.startup_scrape_task
        assert task.done(), "the startup scrape was left pending when startup failed"
        assert task.cancelled()


# --- Capture point: scrapers/manager.py:176-179 (nested log-write fallback) ----


class TestManagerLogWriteFallbackCapturePoint:
    async def test_a_failure_to_write_the_scrape_log_row_is_reported(self, session, make_venue, monkeypatch):
        """The 'could not write the failure row' fallback: a scrape that fails
        *and* can't record that failure leaves no trace anywhere else, so this is
        the last chance to make it visible."""
        from app.scrapers.manager import ScrapeManager

        venue = await make_venue(slug="test-fallback", scraper_type="ticketmaster")

        class ExplodingScraper:
            async def scrape(self):
                raise RuntimeError("scraper exploded")

        async def _failing_commit():
            raise RuntimeError("could not write scrape_logs row")

        manager = ScrapeManager(session)
        monkeypatch.setattr(manager, "_get_scraper", lambda v: ExplodingScraper())
        monkeypatch.setattr(session, "commit", _failing_commit)

        reported = []
        monkeypatch.setattr(
            "app.scrapers.manager.report_error", lambda exc, **kw: reported.append((exc, kw))
        )

        result = await manager.scrape_venue(venue)

        assert result["status"] == "failed"
        assert len(reported) == 1
        exc, kwargs = reported[0]
        assert isinstance(exc, RuntimeError)
        assert kwargs["where"] == "scrapers.manager.scrape_venue.log_write"
        assert kwargs["context"] == {"venue": "test-fallback"}


# --- Capture point: main.py's generic Exception handler -------------------------


class TestUnhandledExceptionCapturePoint:
    async def test_returns_a_fixed_opaque_body_and_reports_the_exception(self, monkeypatch):
        """conftest.py's shared `client` fixture wraps ASGITransport with the
        default raise_app_exceptions=True, and Starlette's ServerErrorMiddleware
        re-raises after invoking a bare-Exception handler — so the shared fixture
        would see the exception propagate rather than a 500. This needs its own
        transport, and its own transient route on the shared app (removed in
        `finally`) so it exercises the real registered handler end to end.

        The route is inserted at the *front* of app.router.routes, not appended:
        the app mounts frontend/ as a StaticFiles catch-all at "/" as its very last
        route (main.py's bottom section), and that Mount prefix-matches every path
        — appending after it would never be reached, and Starlette would 404 out of
        the static handler before ever calling this test's route.
        """
        reported = []
        monkeypatch.setattr(app_main, "report_error", lambda exc, **kw: reported.append((exc, kw)))

        async def _boom():
            raise RuntimeError("kaboom")

        route = APIRoute("/__test_unhandled_exception__", _boom, methods=["GET"])
        app_main.app.router.routes.insert(0, route)

        try:
            transport = ASGITransport(app=app_main.app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
                response = await http_client.get("/__test_unhandled_exception__")
        finally:
            app_main.app.router.routes.remove(route)

        assert response.status_code == 500
        assert response.json() == {"detail": "Internal Server Error"}
        assert len(reported) == 1
        assert isinstance(reported[0][0], RuntimeError)

    async def test_the_client_still_gets_its_500_when_the_funnel_itself_raises(self, monkeypatch):
        """Starlette invokes this handler *before* sending the response, so a raising
        report_error would cost the client its 500 entirely (a dropped connection)
        and replace the original exception in the log with the reporting one.
        Capturing an error must never be the reason a response is lost.
        """

        def _exploding_report_error(exc, **kw):
            raise RuntimeError("the tracker transport is wedged")

        monkeypatch.setattr(app_main, "report_error", _exploding_report_error)

        async def _boom():
            raise RuntimeError("kaboom")

        route = APIRoute("/__test_funnel_itself_raises__", _boom, methods=["GET"])
        app_main.app.router.routes.insert(0, route)

        try:
            transport = ASGITransport(app=app_main.app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
                response = await http_client.get("/__test_funnel_itself_raises__")
        finally:
            app_main.app.router.routes.remove(route)

        assert response.status_code == 500
        assert response.json() == {"detail": "Internal Server Error"}


# --- Capture point: api/feeds.py::record_feed_fetch ------------------------------


class TestFeedTelemetryCapturePoint:
    async def test_a_telemetry_write_failure_is_reported_not_swallowed(
        self, session, monkeypatch
    ):
        """record_feed_fetch used to swallow into a bare logger.warning with no stack
        trace — the same shape as the six points this issue consolidated. Telemetry
        failing silently is how you end up trusting an empty table. Still non-fatal:
        the caller's feed response must be unaffected.
        """
        from app.api import feeds
        from fastapi import Request

        monkeypatch.setattr(settings, "TELEMETRY_SALT", "a-salt")

        async def _failing_commit():
            raise RuntimeError("feed_fetches insert failed")

        monkeypatch.setattr(session, "commit", _failing_commit)

        reported = []
        monkeypatch.setattr(
            "app.api.feeds.report_error", lambda exc, **kw: reported.append((exc, kw))
        )

        request = Request(
            {"type": "http", "headers": [(b"user-agent", b"test-agent")], "client": ("1.2.3.4", 0)}
        )

        await feeds.record_feed_fetch(session, request, None)  # must not raise

        assert len(reported) == 1
        exc, kwargs = reported[0]
        assert isinstance(exc, RuntimeError)
        assert kwargs["where"] == "api.feeds.record_feed_fetch"
