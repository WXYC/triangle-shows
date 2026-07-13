// FullCalendar adapter — converts a neutral /api/v1 EventResponse into the
// FullCalendar v6 event object the calendar renders.
//
// This presentation used to be built server-side (the removed GET
// /api/events/fullcalendar feed). It now lives in the client so the API stays
// surface-neutral and other consumers (e.g. an iOS app via the WXYC Backend-Service)
// can build their own presentation. The output shape — top-level keys and the
// extendedProps bag — must stay identical to what modal.js, filters.js, favorites.js,
// and app.js already read; those files are not guarded by tests, so a renamed key
// breaks them silently.

// Fallback calendar color when a venue has none — matches the old server feed.
const FC_DEFAULT_COLOR = "#6366f1";

// Round to an integer the way Python's f"{x:.0f}" does — half to EVEN (banker's
// rounding), not half up. Matters only for exact half-dollar prices, where a naive
// Math.round would diverge from the old server feed (a $12.50 door price must render
// "$12", not "$13", so the calendar shows the same string it always did).
function _fcRound(x) {
  if (Math.abs(x % 1) === 0.5) {
    const lower = Math.floor(x);
    return lower % 2 === 0 ? lower : lower + 1;
  }
  return Math.round(x);
}

// Human-readable price string: null, "Free", "$20", or "$20-$25".
// Ported verbatim from the old server-side _format_price.
function _fcFormatPrice(priceMin, priceMax) {
  if (priceMin === null || priceMin === undefined) return null;
  if (priceMin === 0 && (priceMax === null || priceMax === undefined || priceMax === 0)) return "Free";
  if (priceMax && priceMax !== priceMin) return `$${_fcRound(priceMin)}-$${_fcRound(priceMax)}`;
  return `$${_fcRound(priceMin)}`;
}

// "20:00:00" -> "8:00 PM" (12-hour, no leading zero on the hour), or null.
// Ported from the old server-side strftime("%I:%M %p").lstrip("0").
function _fcFormatTime(t) {
  if (!t) return null;
  const parts = t.split(":");
  const hour = parseInt(parts[0], 10);
  const minute = parts[1] || "00";
  const ampm = hour < 12 ? "AM" : "PM";
  const hour12 = hour % 12 === 0 ? 12 : hour % 12;
  return `${hour12}:${minute} ${ampm}`;
}

// Map one neutral EventResponse -> a FullCalendar event object. The neutral feed
// already serializes `date` as "YYYY-MM-DD" and times as "HH:MM:SS", so start and the
// time fields need no reformatting beyond the 12-hour display strings below.
function toFullCalendarEvent(ev) {
  const color = ev.venue_color || FC_DEFAULT_COLOR;
  return {
    id: ev.id,
    // Rendered as all-day blocks in month view; the real time is in extendedProps.
    title: ev.artist || ev.name,
    start: ev.date,
    allDay: true,
    backgroundColor: color,
    borderColor: color,
    textColor: "#ffffff",
    extendedProps: {
      event_id: ev.id,
      name: ev.name,
      artist: ev.artist,
      support_artists: ev.support_artists ?? [],
      venue_name: ev.venue_name,
      venue_slug: ev.venue_slug,
      venue_city: ev.venue_city,
      venue_color: color,
      date: ev.date,
      doors_time: _fcFormatTime(ev.doors_time),
      show_time: _fcFormatTime(ev.show_time),
      ticket_url: ev.ticket_url,
      price: _fcFormatPrice(ev.price_min, ev.price_max),
      price_min: ev.price_min,
      price_max: ev.price_max,
      image_url: ev.image_url,
      genre: ev.genre,
      subgenre: ev.subgenre,
      status: ev.status,
      age_restriction: ev.age_restriction,
      description: ev.description,
    },
  };
}

// Exported for the Node test runner (`node --test frontend/tests/`). Harmless in the
// browser, where `module` is undefined, so the file still works as a plain <script>.
if (typeof module !== "undefined" && module.exports) {
  module.exports = { toFullCalendarEvent, _fcFormatPrice, _fcFormatTime, FC_DEFAULT_COLOR };
}
