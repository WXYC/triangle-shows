"""VenuePilot scraper — GraphQL API at venuepilot.co."""
import logging
from datetime import date, datetime, time
from typing import Optional

import httpx

from app.scrapers.base import BaseScraper, ScrapedEvent, BROWSER_HEADERS

logger = logging.getLogger(__name__)

GQL_URL = "https://www.venuepilot.co/graphql"

EVENTS_QUERY = """
query GetEvents($accountId: Int!, $startDate: String!) {
  publicEvents(accountId: $accountId, startDate: $startDate) {
    id
    name
    date
    doorTime
    startTime
    support
    highlightedImage
    ticketsUrl
    status
    minimumAge
  }
}
"""


class VenuePilotScraper(BaseScraper):
    """Scrape events from VenuePilot ticketing platform via GraphQL API.

    Used by: Haw River Ballroom
    """

    async def scrape(self) -> list[ScrapedEvent]:
        account_id = self.config.get("account_id")
        if not account_id:
            raise ValueError(f"No account_id configured for {self.venue_slug}")

        today = date.today().isoformat()
        events = []

        async with httpx.AsyncClient(timeout=30, headers=BROWSER_HEADERS) as client:
            resp = await client.post(
                GQL_URL,
                json={"query": EVENTS_QUERY, "variables": {"accountId": account_id, "startDate": today}},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()

        if "errors" in data:
            raise RuntimeError(f"GraphQL errors: {data['errors']}")

        for item in data.get("data", {}).get("publicEvents", []):
            parsed = self._parse_event(item)
            if parsed:
                events.append(parsed)

        logger.info(f"[VenuePilot] Found {len(events)} events for {self.venue_slug}")
        return events

    def _parse_event(self, item: dict) -> Optional[ScrapedEvent]:
        try:
            name = (item.get("name") or "").strip()
            if not name:
                return None

            date_str = item.get("date", "")
            if not date_str:
                return None
            event_date = date.fromisoformat(date_str[:10])

            # Prefer startTime over doorTime for show_time
            show_time = self._parse_hms(item.get("startTime")) or self._parse_hms(item.get("doorTime"))
            doors_time = self._parse_hms(item.get("doorTime"))
            # If start == doors, only set doors
            if show_time == doors_time:
                doors_time = None

            support = (item.get("support") or "").strip() or None
            image_url = item.get("highlightedImage") or None
            ticket_url = item.get("ticketsUrl") or None

            age = item.get("minimumAge") or 0
            age_restriction = f"{age}+" if age > 0 else None

            return ScrapedEvent(
                name=name,
                date=event_date,
                venue_slug=self.venue_slug,
                source="venuepilot",
                external_id=str(item.get("id", "")),
                artist=name,
                support_artists=support,
                doors_time=doors_time,
                show_time=show_time,
                ticket_url=ticket_url,
                image_url=image_url,
                age_restriction=age_restriction,
                source_url=ticket_url,
            )
        except Exception as e:
            logger.warning(f"[VenuePilot] Failed to parse event: {e}")
            return None

    @staticmethod
    def _parse_hms(s: Optional[str]) -> Optional[time]:
        """Parse 'HH:MM:SS' or 'HH:MM' into a time object."""
        if not s:
            return None
        try:
            parts = s.split(":")
            h, m = int(parts[0]), int(parts[1])
            return time(h, m)
        except (ValueError, IndexError):
            return None
