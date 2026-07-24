"""
Region-pack configuration loading (region-pack epic, issue #62). Phase 1 (issue
#63) landed venue-roster loading; this module now also carries Phase 2 (issue
#64) — the site-identity manifest served at GET /api/v1/site.

Role: Externalizes both the venue roster and the site's branding/identity from
Python literals into declarative TOML files per region, so an operator can stand
up a new region by writing config, not code. app.seed sources its
VENUES/REMOVED_SLUGS from load_venue_config() here; app.market_time,
app.api.feeds, app.api.v1, app.scheduler, and app.main source timezone/branding
from load_site_config(). Nothing else should read venues.toml/site.toml directly.
Requires: tomllib (stdlib, Python >= 3.11), zoneinfo (stdlib) for IANA timezone
validation, the SCRAPER_REGISTRY in app.scrapers.identity — the
VenueConfig.scraper_type validator reads that registry so the config validator
and the scrape manager's dispatch table can never drift apart (they already
share one source; see app.scrapers.manager._get_scraper).
"""

# --- Imports ---
import os
import tomllib
import zoneinfo
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.scrapers.identity import SCRAPER_REGISTRY

# --- Constants ---

_COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"
DEFAULT_REGION = "triangle"


# --- Pydantic models ---


class VenueConfig(BaseModel):
    """Mirrors the columns app.models.Venue sets. Validated at load time so a
    malformed pack fails loudly (region-pack epic decision 5) instead of seeding
    bad data."""

    model_config = ConfigDict(extra="forbid")

    name: str
    slug: str
    city: str
    size_category: Literal["small", "medium", "large"]
    scraper_type: str
    color: str = Field(pattern=_COLOR_PATTERN)
    capacity: Optional[int] = None
    website: Optional[str] = None
    ticketmaster_venue_id: Optional[str] = None
    scraper_config: Optional[dict] = None

    @field_validator("scraper_type")
    @classmethod
    def _scraper_type_is_registered(cls, v: str) -> str:
        # Reads the existing SCRAPER_REGISTRY (app.scrapers.identity) rather than
        # maintaining a second list of valid scraper types, so this validator and
        # the manager's dispatch table share one source of truth.
        if v not in SCRAPER_REGISTRY:
            raise ValueError(
                f"unknown scraper_type {v!r}; must be one of "
                f"{sorted(SCRAPER_REGISTRY)} (app.scrapers.identity.SCRAPER_REGISTRY)"
            )
        return v

    @model_validator(mode="after")
    def _ticketmaster_requires_venue_id(self) -> "VenueConfig":
        if self.scraper_type == "ticketmaster" and not self.ticketmaster_venue_id:
            raise ValueError(
                f"venue {self.slug!r}: scraper_type 'ticketmaster' requires "
                "ticketmaster_venue_id"
            )
        return self

    def to_venue_dict(self) -> dict:
        """The dict shape app.seed's upsert loop expects — one key per Venue column,
        matching the historical VENUES literal (scraper_config omitted entirely
        when the venue doesn't have one, exactly as the old literal did)."""
        data = {
            "name": self.name,
            "slug": self.slug,
            "city": self.city,
            "capacity": self.capacity,
            "size_category": self.size_category,
            "website": self.website,
            "ticketmaster_venue_id": self.ticketmaster_venue_id,
            "scraper_type": self.scraper_type,
            "color": self.color,
        }
        if self.scraper_config is not None:
            data["scraper_config"] = self.scraper_config
        return data


class VenueRegionConfig(BaseModel):
    """The parsed, validated contents of a region's venues.toml."""

    model_config = ConfigDict(extra="forbid")

    removed_slugs: list[str] = Field(default_factory=list)
    venue: list[VenueConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _slugs_are_unique(self) -> "VenueRegionConfig":
        slugs = [v.slug for v in self.venue]
        seen: set[str] = set()
        dupes: set[str] = set()
        for slug in slugs:
            (dupes if slug in seen else seen).add(slug)
        if dupes:
            raise ValueError(f"duplicate venue slug(s) in venues.toml: {sorted(dupes)}")
        return self


# --- Site manifest models (Phase 2, issue #64) ---


class SiteIdentity(BaseModel):
    """Core branding/identity — FastAPI title, iCal headers, head tags."""

    model_config = ConfigDict(extra="forbid")

    name: str
    domain: str
    uid_host: Optional[str] = None
    title: str
    tagline: str
    description: str
    calendar_description: str
    timezone: str
    region_code: str
    favicon_color: str = Field(pattern=_COLOR_PATTERN)
    default_palette: str
    ascii_art: str

    @field_validator("timezone")
    @classmethod
    def _timezone_is_canonical_iana(cls, v: str) -> str:
        if v not in zoneinfo.available_timezones():
            raise ValueError(f"timezone {v!r} is not a canonical IANA zone name")
        return v

    @model_validator(mode="after")
    def _uid_host_defaults_to_domain(self) -> "SiteIdentity":
        # decision 8: uid_host is its own key so a region whose iCal UID host has
        # historically diverged from its domain (Triangle: .org UIDs, .net domain)
        # can pin the old value forever; new regions just inherit domain.
        if not self.uid_host:
            self.uid_host = self.domain
        return self


class IntegrationsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    google_analytics_id: str = ""
    spotify_client_id: str = ""
    kofi_url: str = ""
    kofi_pitches: list[str] = Field(default_factory=list)


class CreditConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    url: str
    repo_url: str


class LinkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    url: str


class CityColorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    border: str
    active_bg: str


class PaletteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    accent: str
    vars: dict[str, str]


class SubdomainConfig(BaseModel):
    """Generalizes the hardcoded durm.* block in the former frontend config.js."""

    model_config = ConfigDict(extra="forbid")

    host_prefix: str
    city: str
    title: str
    subtitle: str
    palette: str
    ascii_art: str


class SiteConfig(BaseModel):
    """The parsed, validated contents of a region's site.toml.

    Field names mirror the TOML tables one-to-one (`site`, `integrations`,
    `credit`, `link` -> `links`, `city_groups`, `cities`, `palettes`,
    `subdomain` -> `subdomains`) so GET /api/v1/site can serve the model with a
    thin by_alias dump rather than a hand-maintained response schema.
    """

    model_config = ConfigDict(extra="forbid")

    site: SiteIdentity
    integrations: IntegrationsConfig = Field(default_factory=IntegrationsConfig)
    credit: CreditConfig
    links: list[LinkConfig] = Field(default_factory=list, alias="link")
    city_groups: dict[str, list[str]] = Field(default_factory=dict)
    cities: dict[str, CityColorConfig] = Field(default_factory=dict)
    palettes: dict[str, PaletteConfig] = Field(default_factory=dict)
    subdomains: list[SubdomainConfig] = Field(default_factory=list, alias="subdomain")


# --- Path resolution ---


def region_dir(region: Optional[str] = None) -> Path:
    """The on-disk directory for a region pack.

    Anchored on the backend package root (``Path(__file__).resolve().parents[1]``),
    never CWD, so resolution is stable both under ``cd backend && pytest`` and in
    the deployed image: the Dockerfile's ``COPY backend/ .`` flattens the
    ``backend/`` prefix into ``/app``, so ``backend/app/site_config.py`` ->
    ``backend/config/...`` in dev becomes ``/app/app/site_config.py`` ->
    ``/app/config/...`` in the image — the same parents[1] index resolves both
    because the pack moves *with* the backend under that flattening. A repo-root
    ``config/`` would need a different index in the image than in dev and silently
    miss (region-pack epic decision 1).
    """
    region = region or os.environ.get("REGION", DEFAULT_REGION)
    return Path(__file__).resolve().parents[1] / "config" / "regions" / region


def _venues_config_path() -> Path:
    # Explicit path overrides take absolute precedence (tests and ad-hoc runs point
    # at fixture packs without needing to set REGION or lay out a real directory).
    override = os.environ.get("VENUES_CONFIG_PATH")
    if override:
        return Path(override)
    return region_dir() / "venues.toml"


def _site_config_path() -> Path:
    # Mirrors _venues_config_path(): an explicit override takes absolute precedence
    # over the region-derived default.
    override = os.environ.get("SITE_CONFIG_PATH")
    if override:
        return Path(override)
    return region_dir() / "site.toml"


# --- Lazy, resettable singletons ---
#
# Populated on first access rather than at import or app-lifespan time: the test
# `client` fixture uses httpx's ASGITransport, which by design never drives the app
# lifespan (tests/conftest.py), so a lifespan-only load would leave every
# config-reading endpoint test looking at an empty cache. Mirrors the pattern
# app.market_time uses for the timezone (region-pack epic decision 5). A conftest
# fixture resets both caches between tests; fail-fast-at-deploy is a separate
# concern — app.main loads the site config at *module import* time (not lifespan)
# to set the FastAPI title, which already dies loudly on a bad/missing pack for
# any real deploy, so no additional boot hook is needed.

_venue_config_cache: Optional[VenueRegionConfig] = None
_site_config_cache: Optional[SiteConfig] = None


def load_venue_config() -> VenueRegionConfig:
    """Parse and validate the active region's venues.toml, caching the result.

    Raises pydantic.ValidationError (malformed/invalid config) or OSError (missing
    file) on the first call after a reset — fail fast rather than silently falling
    back to defaults, per region-pack epic decision 5.
    """
    global _venue_config_cache
    if _venue_config_cache is None:
        path = _venues_config_path()
        with path.open("rb") as f:
            raw = tomllib.load(f)
        _venue_config_cache = VenueRegionConfig.model_validate(raw)
    return _venue_config_cache


def load_site_config() -> SiteConfig:
    """Parse and validate the active region's site.toml, caching the result.

    Raises pydantic.ValidationError (malformed/invalid config) or OSError (missing
    file) on the first call after a reset — fail fast rather than silently falling
    back to defaults, per region-pack epic decision 5. Read by app.market_time (the
    timezone), app.main (FastAPI title/description), app.api.feeds (iCal headers),
    app.api.v1 (city_groups, GET /api/v1/site), and app.scheduler (cron timezone).
    """
    global _site_config_cache
    if _site_config_cache is None:
        path = _site_config_path()
        with path.open("rb") as f:
            raw = tomllib.load(f)
        _site_config_cache = SiteConfig.model_validate(raw)
    return _site_config_cache


def reset_venue_config_cache() -> None:
    """Drop the cached config so the next load_venue_config() call re-reads disk.

    Wired into a conftest fixture so tests that point VENUES_CONFIG_PATH at a
    fixture pack don't see a previous test's cached config.
    """
    global _venue_config_cache
    _venue_config_cache = None


def reset_site_config_cache() -> None:
    """Drop the cached config so the next load_site_config() call re-reads disk.

    Wired into a conftest fixture so tests that point SITE_CONFIG_PATH at a
    fixture pack don't see a previous test's cached config.
    """
    global _site_config_cache
    _site_config_cache = None
