"""
Pydantic Settings configuration — loads environment variables for the application.

Role: Imported at startup by main.py and any module that needs runtime config (database URL,
scheduler toggle, API keys). The `settings` singleton is created at import time, so .env
must be present (or env vars set) before any module imports this file.
Requires: .env file (or environment variables) providing DATABASE_URL, TICKETMASTER_API_KEY,
ENABLE_SCHEDULER, RUN_STARTUP_SCRAPE, APP_ENV, LOG_LEVEL, TELEMETRY_SALT, SENTRY_DSN, and
ALERT_WEBHOOK_URL.
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
    # Per-deployment secret salting feed_fetches.client_hash (app.api.feeds.record_feed_fetch).
    # Empty (the default) disables feed telemetry entirely, on every deployment: an
    # unsalted hash of ip|ua is brute-forceable back to a source IP, so no salt means
    # no rows rather than privacy-degraded rows. Set it to any random secret to opt in.
    TELEMETRY_SALT: str = ""
    # Opt-in exception tracker (Sentry). Empty (the default) means the feature does
    # not exist, same policy as TELEMETRY_SALT above: no events, not degraded ones.
    # Read only by app/sentry_hook.py — app.main and app.scheduler never see it.
    SENTRY_DSN: str = ""
    # Opt-in push delivery for the scrape-health digest (issue #86, not this PR).
    # Empty means observability.send_alert() logs instead of posting. A generic
    # {"text": ...} JSON POST — no vendor named — is what Slack and Mattermost
    # accept, and Discord accepts it too when the operator appends "/slack".
    ALERT_WEBHOOK_URL: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


# --- Singleton ---

# Instantiated once at import time; all modules import this object directly.
settings = Settings()
