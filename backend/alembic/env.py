"""
Alembic migration environment — imports SQLAlchemy models and runs schema migrations.

Role: Runs on two paths, and the difference matters. In production it executes
      *inside the app process* — app.main's lifespan calls alembic upgrade head at
      startup, and nothing else applies migrations (railway.json's startCommand and
      the Dockerfile CMD are uvicorn only; no workflow invokes alembic). It also runs
      from a shell when someone upgrades a database by hand.

      Because the production path shares a process with the running server, anything
      here that mutates process-global state — logging config, sys.path, os.environ —
      lands on the server for the life of the container. That is not hypothetical: an
      unconditional logging.config.fileConfig() call in this file silenced every app.*
      logger in production (issue #101). See the configure_logger note below.
Requires: DATABASE_URL env var (falls back to alembic.ini), app.database.Base,
          app.models (Venue, Event, EventMissState, ScrapeLog, FeedFetch).
"""

# --- Imports ---

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Add parent directory to path so we can import app modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import Base
from app.models import Venue, Event, EventMissState, ScrapeLog, FeedFetch  # noqa: F401 - ensure models are imported

# --- Alembic Config & URL Setup ---

config = context.config

# Override sqlalchemy.url with env var if available
db_url = os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url"))
# Alembic needs sync driver; also convert asyncpg-style SSL param to psycopg2-style
if db_url:
    db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    db_url = db_url.replace("ssl=require", "sslmode=require")
    config.set_main_option("sqlalchemy.url", db_url)

# Configure logging from alembic.ini if a config file is present.
#
# fileConfig() reconfigures logging for the whole process: it defaults to
# disable_existing_loggers=True, and alembic.ini pins the root logger at WARN. That
# is what you want from `alembic upgrade head` in a shell, and emphatically not what
# you want when app.main runs migrations inside the server process at startup — there
# it silences every app.* logger for the life of the container.
#
# So the programmatic caller opts out via config.attributes, alembic's documented
# channel for exactly this. attributes is empty when alembic is driven from the CLI,
# so the default keeps the readable hand-run output.
if config.attributes.get("configure_logger", True) and config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Point Alembic at the full set of ORM models so it can diff against the live schema
target_metadata = Base.metadata


# --- Migration Runners ---

def run_migrations_offline():
    """Run migrations without a live DB connection, emitting SQL to stdout or a file."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # No connection pooling needed for one-shot migration runs
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


# --- Entry Point ---

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
