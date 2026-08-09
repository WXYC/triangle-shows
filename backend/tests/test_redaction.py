"""Regression tests for credential leakage out of the application.

The Ticketmaster Discovery API authenticates with a query parameter (``?apikey=``),
so every request URL the scraper builds carries a live credential. That URL escapes
the process by three independent routes, and a fix that closes one leaves the others
open:

1. ``httpx`` logs every request at INFO with the full query string, so a *healthy*
   scrape writes the key to the log store four times per cycle (once per Ticketmaster
   venue). This is the route that actually fired: the key was readable in Railway's
   log store within a minute of being set.
2. ``httpx.HTTPStatusError`` embeds the request URL in its ``str()``, and
   ``manager.scrape_venue`` interpolates that into ``logger.error`` — so a *failed*
   scrape logs the key even with httpx itself silenced.
3. That same exception string is persisted to ``scrape_logs.error_message`` and
   returned in the body of ``POST /api/scrape``, which is unauthenticated. A
   Ticketmaster 429 would hand the key to an anonymous caller.

Route 3 is why redaction cannot live in the logging layer alone: two of the three
sinks are not logs. The shared helper is the contract; the formatter and the
manager are its two installation points.
"""

import io
import logging

import httpx
import pytest

from app.redaction import RedactingFormatter, redact_credentials

# Shaped exactly like the URL the Ticketmaster scraper builds (app/scrapers/ticketmaster.py),
# with a placeholder standing in for the live key.
FAKE_KEY = "s3cr3tKEYvalue0123456789abcdefgh"
TM_URL = (
    "https://app.ticketmaster.com/discovery/v2/events.json"
    f"?apikey={FAKE_KEY}&venueId=KovZpZAdEEvA&size=200&page=0&sort=date%2Casc"
)


# --- The shared helper ---


@pytest.mark.parametrize(
    "param",
    ["apikey", "api_key", "access_token", "token", "secret", "password", "signature", "sig"],
)
def test_credential_query_params_are_redacted(param):
    """Every parameter name we treat as a credential loses its value."""
    redacted = redact_credentials(f"https://example.test/x?{param}={FAKE_KEY}&venueId=1")
    assert FAKE_KEY not in redacted
    assert f"{param}=" in redacted, "the parameter name is kept — only the value is scrubbed"
    assert "venueId=1" in redacted


@pytest.mark.parametrize("param", ["APIKEY", "ApiKey", "Api_Key", "Access_Token"])
def test_redaction_is_case_insensitive(param):
    """Query-parameter casing varies across APIs; a case-sensitive match would miss them."""
    assert FAKE_KEY not in redact_credentials(f"https://example.test/x?{param}={FAKE_KEY}")


def test_non_credential_params_survive():
    """Redaction must not destroy the diagnostic value of a logged URL."""
    redacted = redact_credentials(TM_URL)
    assert FAKE_KEY not in redacted
    for kept in ("venueId=KovZpZAdEEvA", "size=200", "page=0", "sort=date%2Casc"):
        assert kept in redacted, f"{kept} is diagnostic, not secret, and should survive"
    assert "app.ticketmaster.com/discovery/v2/events.json" in redacted


def test_value_at_end_of_string_is_redacted():
    """No trailing ampersand to anchor on — the common shape when the key is the last param."""
    assert FAKE_KEY not in redact_credentials(f"https://example.test/x?venueId=1&apikey={FAKE_KEY}")


def test_redaction_stops_at_the_end_of_the_value_not_the_end_of_the_line():
    """A credential in the *last* query parameter must not swallow the text after the URL.

    This is httpx's exact log shape, and the case that separates a correct value pattern
    from a lazy one: with the key last, a pattern that only stops at `&` runs to the end
    of the record and eats the HTTP status — redacting far more than the secret. Nothing
    leaks either way, so only an assertion on the *surviving* text catches it.
    """
    line = (
        f"HTTP Request: GET https://app.ticketmaster.com/x?venueId=KovZpZAdEEvA&apikey={FAKE_KEY} "
        '"HTTP/1.1 200 OK"'
    )
    redacted = redact_credentials(line)

    assert FAKE_KEY not in redacted
    assert '"HTTP/1.1 200 OK"' in redacted, "redaction ran past the value and ate the status"
    assert "venueId=KovZpZAdEEvA" in redacted


def test_redaction_stops_at_a_closing_quote():
    """httpx.HTTPStatusError renders the URL inside single quotes; the closing quote is
    the only boundary when the credential is the final parameter."""
    line = f"Client error '401 Unauthorized' for url 'https://x.test/y?apikey={FAKE_KEY}'"
    redacted = redact_credentials(line)

    assert FAKE_KEY not in redacted
    assert redacted.endswith("'"), "the closing quote was consumed as part of the value"


def test_redaction_is_idempotent():
    """Applied at more than one layer, so double-application must not corrupt the text."""
    once = redact_credentials(TM_URL)
    assert redact_credentials(once) == once


def test_non_string_input_is_returned_unchanged():
    """Call sites pass exception objects and None; the helper must not raise on them."""
    assert redact_credentials(None) is None
    assert redact_credentials(42) == 42


# --- The logging installation point ---


def _capture(formatter, record):
    """Format one record through a handler carrying `formatter`, returning the output."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    handler.handle(record)
    return stream.getvalue()


def test_formatter_redacts_the_message():
    record = logging.LogRecord(
        name="httpx", level=logging.INFO, pathname=__file__, lineno=1,
        msg='HTTP Request: GET %s "HTTP/1.1 200 OK"', args=(TM_URL,), exc_info=None,
    )
    out = _capture(RedactingFormatter("%(message)s"), record)
    assert FAKE_KEY not in out
    assert "venueId=KovZpZAdEEvA" in out


def test_formatter_redacts_the_exception_traceback():
    """The manager logs failures by interpolation, but any exc_info=True call site would
    put the URL in the traceback text instead — where a message-only filter never looks."""
    try:
        raise httpx.HTTPStatusError(
            f"Client error '401 Unauthorized' for url '{TM_URL}'",
            request=httpx.Request("GET", TM_URL),
            response=httpx.Response(401, request=httpx.Request("GET", TM_URL)),
        )
    except httpx.HTTPStatusError:
        import sys

        record = logging.LogRecord(
            name="app.scrapers.manager", level=logging.ERROR, pathname=__file__,
            lineno=1, msg="scrape failed", args=(), exc_info=sys.exc_info(),
        )

    out = _capture(RedactingFormatter("%(message)s"), record)
    assert "Traceback" in out, "the test is only meaningful if the traceback was rendered"
    assert FAKE_KEY not in out


def test_configure_logging_installs_the_formatter_on_every_root_handler(preserved_logging):
    """A handler without the formatter is an open sink; there is usually only one, but
    the assertion is over all of them so an added handler cannot silently bypass this."""
    from app.main import configure_logging

    configure_logging()
    root = logging.getLogger()
    assert root.handlers, "expected basicConfig to have installed at least one handler"
    for handler in root.handlers:
        assert isinstance(handler.formatter, RedactingFormatter), (
            f"{handler!r} would emit un-redacted output"
        )


def test_configure_logging_silences_httpx_request_logging(preserved_logging):
    """The primary emitter is switched off outright rather than left to the formatter.

    No regex should be the only thing between a live credential and a log store: httpx
    logs the full URL on every successful request, so the volume alone makes it the one
    source worth removing alongside redacting. Our own scrapers already log a
    per-request line ('[TM] Fetching page 0 for red-hat') carrying the venue, which is
    the part with diagnostic value.
    """
    from app.main import configure_logging

    logging.getLogger("httpx").setLevel(logging.NOTSET)
    configure_logging()

    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
    assert logging.getLogger("httpx").isEnabledFor(logging.WARNING), (
        "a genuine httpx failure must still reach the log"
    )


# --- The non-logging installation point ---


@pytest.mark.asyncio
async def test_scrape_failure_does_not_persist_or_return_the_credential(
    session, make_venue, monkeypatch
):
    """A failing Ticketmaster scrape must not write the key to scrape_logs, nor hand it
    back through POST /api/scrape — which is unauthenticated (app/main.py)."""
    from sqlalchemy import select

    from app.models import ScrapeLog
    from app.scrapers.manager import ScrapeManager

    venue = await make_venue(slug="red-hat", scraper_type="ticketmaster")
    # Read the id up front: the failure path rolls the session back, which expires every
    # ORM object, and touching an expired attribute afterwards triggers a sync lazy-load
    # (MissingGreenlet) rather than the assertion we came here for.
    venue_id = venue.id

    class ExplodingScraper:
        async def scrape(self):
            request = httpx.Request("GET", TM_URL)
            raise httpx.HTTPStatusError(
                f"Client error '401 Unauthorized' for url '{TM_URL}'",
                request=request,
                response=httpx.Response(401, request=request),
            )

    manager = ScrapeManager(session)
    monkeypatch.setattr(manager, "_get_scraper", lambda venue: ExplodingScraper())

    result = await manager.scrape_venue(venue)

    assert result["status"] == "failed"
    assert FAKE_KEY not in result["error"], "the API response body leaked the credential"
    assert "401 Unauthorized" in result["error"], "the diagnosis itself must survive"

    rows = (
        await session.execute(select(ScrapeLog).where(ScrapeLog.venue_id == venue_id))
    ).scalars().all()
    assert rows, "expected the failure to be recorded"
    assert all(FAKE_KEY not in (row.error_message or "") for row in rows), (
        "the credential was persisted to scrape_logs.error_message"
    )
