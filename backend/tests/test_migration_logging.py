"""
Regression tests for the in-process migration path's effect on application logging.

Migrations run *inside* the FastAPI process at startup (``app.main.lifespan`` ->
``asyncio.to_thread(_run_migrations)``), so whatever ``alembic/env.py`` does to the
``logging`` module happens to the whole server. ``logging.config.fileConfig``
defaults to ``disable_existing_loggers=True`` and ``alembic.ini`` pins the root
logger at WARN, so calling it unconditionally silenced every ``app.*`` logger for
the life of the container — at every level, WARNING and ERROR included.

Two halves of the contract are pinned here, and they pull in opposite directions:

* the **in-process** path must leave logging exactly as ``app.main`` configured it;
* the **CLI** path (``alembic upgrade head`` in a shell) must still apply
  ``alembic.ini``, which is what makes a hand-run migration readable.

A fix that satisfies only the first — deleting the ``fileConfig`` call outright —
fails the second.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

import app.main
from app.database import Base
from app.main import _run_migrations

BACKEND_DIR = Path(app.main.__file__).resolve().parent.parent

# A data migration's own accounting line, emitted by app.services.url_backfill from
# inside migration 0009. Both paths assert on the same string deliberately: it is the
# output whose loss motivated the fix, and asserting it on only one path is how the CLI
# half stayed broken while its test passed.
BACKFILL_COUNT_MARKER = "sanitize_existing_urls:"


def _sync_database_url() -> str:
    """The psycopg2-flavored test URL, derived exactly as alembic/env.py derives it."""
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "ssl=require", "sslmode=require"
    )


@pytest.fixture
def migrated_database(_ensure_test_database):
    """Let a test run the real migration chain from scratch, then undo it.

    Cleanup runs on the way in as well as out, so the chain actually executes
    rather than short-circuiting on an ``alembic_version`` row left behind by an
    aborted run — a test that silently migrates nothing would assert nothing.

    Teardown is surgical rather than a schema drop: alembic creates exactly the
    ORM tables plus its own ``alembic_version`` bookkeeping table, so those are
    what get removed. The target is the harness's dedicated ``*_test`` database
    (conftest refuses any other name), never a database holding real data.
    """
    engine = create_engine(_sync_database_url())

    def _reset():
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        Base.metadata.drop_all(engine)

    _reset()
    try:
        yield
    finally:
        try:
            _reset()
        finally:
            engine.dispose()


@pytest.fixture
def preserved_logging():
    """Snapshot and restore global logging state around a test that reconfigures it.

    ``fileConfig`` mutates process-wide state — root's level and handlers, and the
    ``disabled`` flag plus handlers of every pre-existing logger. Without this, a
    test that trips the bug takes the rest of the session down with it.
    """
    root = logging.getLogger()
    saved_root_level = root.level
    saved_root_handlers = root.handlers[:]
    saved_loggers = [
        (logger, logger.level, logger.disabled, logger.handlers[:], logger.propagate)
        for logger in logging.Logger.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    ]
    try:
        yield
    finally:
        root.setLevel(saved_root_level)
        root.handlers[:] = saved_root_handlers
        for logger, level, disabled, handlers, propagate in saved_loggers:
            logger.setLevel(level)
            logger.disabled = disabled
            logger.handlers[:] = handlers
            logger.propagate = propagate


def test_in_process_migrations_leave_app_logging_intact(
    migrated_database, preserved_logging, caplog
):
    """The startup migration run must not silence the application's own loggers."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Created *before* the migration run: disable_existing_loggers is precisely what
    # takes out loggers that already exist, and every app.* module is imported long
    # before lifespan runs.
    probe = logging.getLogger("app.tests.migration_logging_probe")

    _run_migrations()

    assert probe.disabled is False, "the migration run disabled a pre-existing app.* logger"
    assert root.level == logging.INFO, "the migration run changed the root logger's level"

    # The data migrations report what they touched through app.services.* loggers, and
    # those reports are the only witness to work that is destructive by design — 0009
    # cleared malformed URLs in production and how many was never recoverable, because
    # this line went nowhere. The count is 0 against a fresh database; what is being
    # pinned is that the record escapes the migration run at all.
    assert BACKFILL_COUNT_MARKER in caplog.text, (
        "a data migration's own count line did not reach the log during the startup run"
    )

    caplog.clear()
    probe.info("still speaking after migrations")
    assert "still speaking after migrations" in caplog.text


def _run_alembic_cli(cwd, config_path=None):
    """``alembic upgrade head`` as a subprocess, optionally with an explicit ``-c``.

    A subprocess because that is literally the path under test, and because
    ``fileConfig`` would otherwise tear down pytest's own log handlers.

    DATABASE_URL stays asyncpg-flavored: env.py converts it to a sync driver for
    alembic's own engine, but it also imports app.database, which needs the async form.
    Handing it the converted URL is what production never does.
    """
    argv = [sys.executable, "-m", "alembic"]
    if config_path is not None:
        argv += ["-c", str(config_path)]
    argv += ["upgrade", "head"]
    return subprocess.run(
        argv, cwd=cwd, env=os.environ.copy(), capture_output=True, text=True
    )


def test_cli_migrations_still_print_alembic_progress(migrated_database):
    """``alembic upgrade head`` in a shell keeps the readable output alembic.ini is for."""
    result = _run_alembic_cli(BACKEND_DIR)

    assert result.returncode == 0, result.stderr
    # alembic.ini puts the alembic logger at INFO on a stderr console handler; these
    # lines are emitted on every run, whether or not there is work to do.
    assert "alembic.runtime.migration" in result.stderr, result.stderr
    assert "INFO" in result.stderr, result.stderr


def test_cli_migrations_print_the_data_migrations_own_counts(migrated_database):
    """A hand-run migration must report what the data migrations actually touched.

    This is the output whose loss motivated the whole fix, and for a while it was
    restored on the startup path only. The previous version of this test asserted on
    ``alembic.runtime.migration`` — a logger ``alembic.ini`` explicitly declares — so it
    passed for a reason unrelated to the failure and the CLI half stayed broken beneath
    a green suite. Asserting the same marker both paths use is what closes that gap.

    Matters most on the recovery path: an operator upgrading a restored database by hand
    runs 0004's duplicate-key merge, 0006's description rewrite, and 0009's URL clearing,
    all destructive by design, and the count is the only record of what they did.
    """
    result = _run_alembic_cli(BACKEND_DIR)

    assert result.returncode == 0, result.stderr
    assert BACKFILL_COUNT_MARKER in result.stderr, result.stderr


def test_migrations_do_not_depend_on_the_callers_working_directory(
    migrated_database, preserved_logging, tmp_path, monkeypatch
):
    """Both entry points must resolve the migration *scripts* independently of cwd.

    ``alembic.ini``'s ``script_location`` is resolved relative to the process working
    directory unless it is anchored, so an unanchored value makes the startup migration
    depend on where the container happened to be launched from — it worked only because
    the Dockerfile sets WORKDIR. Running the suite from the repo root instead of
    ``backend/`` surfaced the same fragility as a failure pointing at a missing
    directory rather than at cwd.

    The two paths are held to different standards on purpose. ``_run_migrations`` builds
    an absolute ini path from ``__file__``, so it must work from anywhere — that is the
    production case. The CLI is *expected* to need ``-c`` when run outside ``backend/``,
    since ``alembic`` looks for ``alembic.ini`` in cwd by design; what must not happen is
    finding the ini and then failing to locate the scripts beside it.
    """
    monkeypatch.chdir(tmp_path)

    # CLI first, while there is still work to do — a run against an already-migrated
    # database emits no count line, so asserting the marker after the in-process run
    # would only ever pass by accident.
    result = _run_alembic_cli(tmp_path, config_path=BACKEND_DIR / "alembic.ini")
    assert result.returncode == 0, result.stderr
    assert BACKFILL_COUNT_MARKER in result.stderr, result.stderr

    # In-process, now a no-op upgrade. Still has to load the script directory to resolve
    # "head", which is exactly what failed before script_location was anchored.
    _run_migrations()
