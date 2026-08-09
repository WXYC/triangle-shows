"""Scrub credential-bearing query parameters out of text before it leaves the process.

Role: shared by the three sinks a request URL can escape through — the logging
formatter installed in ``app.main.configure_logging`` (covering both messages and
exception tracebacks), and the two non-logging sinks in
``app.scrapers.manager.scrape_venue``: the ``scrape_logs.error_message`` column and
the error body returned by ``POST /api/scrape``, which is unauthenticated.

Why this exists: the Ticketmaster Discovery API authenticates with a query parameter
rather than a header, so the scraper's request URL *is* a credential. ``httpx`` logs
that URL in full at INFO on every request, and ``httpx.HTTPStatusError`` embeds it in
its ``str()`` — which the scrape manager interpolates into a log line, persists to the
database, and returns to the caller. Redacting in one place would have closed one of
three routes.

This is the backstop, not the primary defense. ``configure_logging`` also pins the
``httpx`` logger to WARNING so the high-volume emitter is switched off outright: a
denylist of parameter names should not be the only thing standing between a live
credential and a log store.

Requires: nothing outside the standard library — deliberately import-light so any
layer can call it without a dependency cycle.
"""

import logging
import re

# --- Redaction ---

REDACTED = "<redacted>"

# Parameter names whose values are credentials. Matched as a *suffix* behind an optional
# `[\w-]*` prefix, so `csrf_token`, `x-api-key` and `client_secret` are all caught rather
# than requiring an exact spelling. `sig` is the one exact-only entry: as a suffix it
# would fire on innocuous names, and the lookbehind is what keeps it from matching inside
# a longer word.
#
# This is a denylist, and a denylist is never complete. It covers what this codebase
# actually sends; when a new scraper authenticates with an unlisted parameter name, add
# it here and to the parametrized test rather than relying on the entry that happens to
# be closest.
_CREDENTIAL_QUERY_RE = re.compile(
    r"(?i)"
    r"(?<![\w-])"
    r"((?:[\w-]*(?:api[-_]?key|access[-_]?token|token|secret|password|signature)|sig)=)"
    # A value runs to the next parameter separator or to whatever delimiter the
    # surrounding text uses — a quote in an exception message, an angle bracket in
    # rendered markup, or the end of the string.
    r"([^&\s\"'<>]+)"
)


def redact_credentials(text):
    """Replace credential query-parameter values in `text`, leaving everything else intact.

    Non-string input is returned unchanged so call sites can pass an exception object or
    ``None`` without guarding first.

    Idempotent by construction: the placeholder opens with ``<``, which the value pattern
    excludes, so a second pass finds nothing left to match. That matters because the
    formatter and the manager both apply this, and a failed Ticketmaster scrape goes
    through both.

    Only the *value* is removed — the parameter name and every non-credential parameter
    survive, so a redacted URL still says which venue was being fetched and with what
    paging. A log line that has been scrubbed into uselessness gets replaced by one that
    isn't.
    """
    if not isinstance(text, str):
        return text
    return _CREDENTIAL_QUERY_RE.sub(rf"\g<1>{REDACTED}", text)


# --- Logging installation point ---


class RedactingFormatter(logging.Formatter):
    """A ``Formatter`` that scrubs credentials from everything it renders.

    Deliberately a formatter rather than a ``logging.Filter``. A filter sees
    ``record.msg`` and ``record.args`` before they are combined, and never sees the
    rendered exception traceback at all — so an ``exc_info=True`` call site would walk
    straight past it carrying the URL in the traceback text. Formatting is the single
    point where message, arguments, and traceback have all become one string.

    Attach to *handlers*, not to loggers: a formatter belongs to a handler by design, and
    the handler is what every propagated record from every child logger passes through.
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact_credentials(super().format(record))
