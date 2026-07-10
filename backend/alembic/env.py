"""
Alembic migration environment — imports SQLAlchemy models and runs schema migrations.

Role: Invoked by `alembic upgrade head` (manually or during deploy); not part of the
      runtime request path. Must run before the app starts if the schema is out of date.
Requires: DATABASE_URL env var (falls back to alembic.ini), app.database.Base,
          app.models (Venue, Event, ScrapeLog).
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
from app.models import Venue, Event, EventMissState, ScrapeLog  # noqa: F401 - ensure models are imported

# --- Alembic Config & URL Setup ---

config = context.config

# Override sqlalchemy.url with env var if available
db_url = os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url"))
# Alembic needs sync driver; also convert asyncpg-style SSL param to psycopg2-style
if db_url:
    db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    db_url = db_url.replace("ssl=require", "sslmode=require")
    config.set_main_option("sqlalchemy.url", db_url)

# Configure logging from alembic.ini if a config file is present
if config.config_file_name is not None:
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
