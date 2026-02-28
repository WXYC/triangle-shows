"""Koka Booth Amphitheatre supplementary scraper — Carbonhouse CMS / ETIX."""
import json
import logging
import re
from datetime import datetime, date, time
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScrapedEvent

logger = logging.getLogger(__name__)


class KokaBoothScraper(BaseScraper):
    """Supplementary scraper for Koka Booth to catch ETIX-only shows.

    Scrapes the venue's own Carbonhouse CMS site for events not on Ticketmaster.
    Used by: Koka Booth Amphitheatre (supplementary to TM)
    """

    async def scrape(self) -> list[ScrapedEvent]:
        url = self.config.get("url", "https://www.boothamphitheatre.com/events")
        events = []

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            # Try JSON-LD first
            events.extend(self._extract_jsonld(soup, url))

            # Parse event cards from Carbonhouse layout
            cards = soup.select(
                ".event-card, .event-item, .event-listing, "
                ".views-row, article.event, .list-item"
            )

            for card in cards:
                parsed = self._parse_card(card)
                if parsed:
                    events.append(parsed)

            # Follow detail links if we found none
            if not events:
                links = soup.select("a[href*='event'], a[href*='show']")
                seen = set()
                for link in links:
                    href = link.get("href", "")
                    if href and href not in seen and "boothamphitheatre" in href:
                        seen.add(href)

                for detail_url in list(seen)[:20]:
                    try:
                        d_resp = await client.get(detail_url)
                        d_resp.raise_for_status()
                        d_soup = BeautifulSoup(d_resp.text, "lxml")
                        ld_events = self._extract_jsonld(d_soup, detail_url)
                        events.extend(ld_events)
                    except Exception as e:
                        logger.warning(f"[KokaBooth] Detail fetch failed: {e}")

        # Deduplicate
        seen_hashes = set()
        unique = []
        for ev in events:
            if ev.hash not in seen_hashes:
                seen_hashes.add(ev.hash)
                unique.append(ev)

        logger.info(f"[KokaBooth] Found {len(unique)} events for {self.venue_slug}")
        return unique

    def _extract_jsonld(self, soup: BeautifulSoup, page_url: str) -> list[ScrapedEvent]:
        events = []
        scripts = soup.find_all("script", type="application/ld+json")
        for script in scripts:
            try:
                data = json.loads(script.string)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("@type") in ("Event", "MusicEvent"):
                        parsed = self._parse_jsonld(item, page_url)
                        if parsed:
                            events.append(parsed)
            except (json.JSONDecodeError, TypeError):
                continue
        return events

    def _parse_jsonld(self, item: dict, page_url: str) -> Optional[ScrapedEvent]:
        try:
            name = item.get("name", "").strip()
            start = item.get("startDate", "")
            if not name or not start:
                return None

            if "T" in start:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                event_date = dt.date()
                show_time = dt.time().replace(tzinfo=None) if dt.time() != time(0, 0) else None
            else:
                event_date = date.fromisoformat(start[:10])
                show_time = None

            image = item.get("image", "")
            if isinstance(image, (list, dict)):
                image = image[0] if isinstance(image, list) else image.get("url", "")

            offers = item.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            ticket_url = offers.get("url") if isinstance(offers, dict) else None

            return ScrapedEvent(
                name=name,
                date=event_date,
                venue_slug=self.venue_slug,
                source="koka_booth",
                artist=name,

                show_time=show_time,
                ticket_url=ticket_url or page_url,
                image_url=image or None,
                source_url=item.get("url") or page_url,
            )
        except Exception as e:
            logger.warning(f"[KokaBooth] JSON-LD parse error: {e}")
            return None

    def _parse_card(self, card) -> Optional[ScrapedEvent]:
        try:
            title_el = card.select_one("h2 a, h3 a, .event-title a, .title a")
            if not title_el:
                title_el = card.select_one("h2, h3, .event-title")
            if not title_el:
                return None

            name = title_el.get_text(strip=True)
            if not name:
                return None

            link = title_el.get("href") if title_el.name == "a" else None

            date_el = card.select_one("time, .date, .event-date")
            event_date = None
            if date_el:
                dt_attr = date_el.get("datetime")
                if dt_attr:
                    try:
                        event_date = date.fromisoformat(dt_attr[:10])
                    except ValueError:
                        pass
                if not event_date:
                    text = re.sub(r'^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*,?\s*', '', date_el.get_text(strip=True))
                    for fmt in ["%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"]:
                        try:
                            event_date = datetime.strptime(text, fmt).date()
                            break
                        except ValueError:
                            continue

            if not event_date:
                return None

            img_el = card.select_one("img")
            image_url = img_el.get("src") if img_el else None

            return ScrapedEvent(
                name=name,
                date=event_date,
                venue_slug=self.venue_slug,
                source="koka_booth",
                artist=name,

                ticket_url=link,
                image_url=image_url,
                source_url=link,
            )
        except Exception as e:
            logger.warning(f"[KokaBooth] Card parse error: {e}")
            return None
