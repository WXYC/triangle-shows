# triangle-shows

Live music calendar for the Triangle — Raleigh, Durham, Chapel Hill, Carrboro, and Saxapahaw — on one page.

**[triangle-shows.org](https://triangle-shows.org)**

---

## What it does

Scrapes show listings from 15+ venues every 6 hours and serves them as a FullCalendar month/list view. Venue, city, and artist search filters all run client-side. Users can heart shows and export their picks as an `.ics` file.

### Venues covered

| Venue | City |
|---|---|
| DPAC | Durham |
| Motorco Music Hall | Durham |
| The Pinhook | Durham |
| Shadowbox Studio | Durham |
| The Ritz | Raleigh |
| Lincoln Theatre | Raleigh |
| Red Hat Amphitheater | Raleigh |
| Koka Booth Amphitheatre | Raleigh |
| Kings | Raleigh |
| Neptune's Parlour | Raleigh |
| Cat's Cradle | Chapel Hill-Carrboro |
| Cat's Cradle Back Room | Chapel Hill-Carrboro |
| Local 506 | Chapel Hill-Carrboro |
| The Cave | Chapel Hill-Carrboro |
| Haw River Ballroom | Saxapahaw |

---

## Stack

- **Backend:** Python / FastAPI, SQLAlchemy (async), PostgreSQL
- **Scrapers:** Ticketmaster Discovery API + custom scrapers for RHP Tickets, Tribe Events, Squarespace, EventPrime, VenuePilot, and venue-specific parsers
- **Scheduler:** APScheduler (6-hour scrape interval in production)
- **Frontend:** Vanilla JS, [FullCalendar v6](https://fullcalendar.io/)
- **Deployment:** Google Cloud Run + Neon PostgreSQL + Cloud Scheduler

---

## Running locally

Requires Docker.

```bash
git clone https://github.com/ty-fi/triangle-shows
cd triangle-shows
cp backend/.env.example backend/.env   # add your TICKETMASTER_API_KEY
docker-compose up
```

The app is available at `http://localhost:8000`. On startup it runs migrations, seeds venues, and triggers an initial scrape.

To trigger a manual scrape:

```bash
curl -X POST http://localhost:8000/api/scrape
```

---

## Project structure

```
backend/
  app/
    api/          # FastAPI route handlers
    scrapers/     # One scraper per venue/platform
    models.py     # SQLAlchemy ORM (Venue, Event, ScrapeLog)
    scheduler.py  # APScheduler job config
    seed.py       # Venue seed data
frontend/
  index.html
  css/styles.css
  js/
    app.js        # FullCalendar init
    filters.js    # Client-side filter logic
    config.js     # Palettes, API base URL
    modal.js      # Event detail modal
    favorites.js  # Heart/hide/export
```

---

mostly made by [claude code](https://claude.ai/claude-code), with piloting from [@tyfi](https://bsky.app/profile/tyfi.bsky.social).
