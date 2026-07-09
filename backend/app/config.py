"""
Pydantic Settings configuration — loads environment variables for the application.

Role: Imported at startup by main.py and any module that needs runtime config (database URL,
scheduler toggle, API keys). The `settings` singleton is created at import time, so .env
must be present (or env vars set) before any module imports this file.
Requires: .env file (or environment variables) providing DATABASE_URL, TICKETMASTER_API_KEY,
ENABLE_SCHEDULER, RUN_STARTUP_SCRAPE, APP_ENV, and LOG_LEVEL.
"""

# --- Imports ---
from pydantic_settings import BaseSettings


# --- Settings ---

class Settings(BaseSettings):
    """Application-wide configuration, populated from environment variables or .env."""

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/triangle_shows"
    TICKETMASTER_API_KEY: str = ""
    ENABLE_SCHEDULER: bool = False  # Set to True in production to run scrapes on a cron schedule
    RUN_STARTUP_SCRAPE: bool = True  # Run a full scrape in the background at startup; set False in tests / manual-seed contexts
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


# --- Singleton ---

# Instantiated once at import time; all modules import this object directly.
settings = Settings()
