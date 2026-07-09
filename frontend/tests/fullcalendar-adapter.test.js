// Unit tests for the FullCalendar adapter (../js/fullcalendar-adapter.js).
//
// Runs on Node's built-in test runner — no build step, no npm install:
//   node --test frontend/tests/
//
// These lock the two risky parts of moving presentation client-side: the price/time
// string formatting (ported from the removed server-side Python) and the extendedProps
// key set. Nothing else guards those keys — modal.js/filters.js/favorites.js read them
// by name and break silently if one is renamed.

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { toFullCalendarEvent, _fcFormatPrice, _fcFormatTime } = require("../js/fullcalendar-adapter.js");

test("price formatting matches the former server _format_price", () => {
  assert.equal(_fcFormatPrice(null, 25), null); // no min -> no price
  assert.equal(_fcFormatPrice(0, null), "Free");
  assert.equal(_fcFormatPrice(0, 0), "Free");
  assert.equal(_fcFormatPrice(20, 25), "$20-$25");
  assert.equal(_fcFormatPrice(20, null), "$20");
  assert.equal(_fcFormatPrice(20, 20), "$20"); // max == min collapses to one price
  assert.equal(_fcFormatPrice(20, 0), "$20"); // falsy max collapses
  assert.equal(_fcFormatPrice(0, 25), "$0-$25");
  // Half-dollar prices must round half-to-even to match Python's f"{x:.0f}" exactly,
  // so the string is byte-identical to the removed server feed (not naive round-half-up).
  assert.equal(_fcFormatPrice(12.5, null), "$12"); // 12.5 -> 12 (even), NOT 13
  assert.equal(_fcFormatPrice(13.5, null), "$14"); // 13.5 -> 14 (even)
  assert.equal(_fcFormatPrice(2.5, null), "$2"); // 2.5 -> 2, NOT 3
  assert.equal(_fcFormatPrice(20.5, 25.5), "$20-$26"); // 20.5->20 (even), 25.5->26 (even)
  assert.equal(_fcFormatPrice(12.99, null), "$13"); // non-tie still rounds normally
});

test("time formatting is 12-hour with no leading zero on the hour", () => {
  assert.equal(_fcFormatTime(null), null);
  assert.equal(_fcFormatTime("20:00:00"), "8:00 PM");
  assert.equal(_fcFormatTime("07:00:00"), "7:00 AM");
  assert.equal(_fcFormatTime("00:30:00"), "12:30 AM"); // midnight
  assert.equal(_fcFormatTime("12:00:00"), "12:00 PM"); // noon
  assert.equal(_fcFormatTime("23:05:00"), "11:05 PM");
  assert.equal(_fcFormatTime("09:15:00"), "9:15 AM");
});

test("toFullCalendarEvent produces the exact shape the calendar + modal read", () => {
  const neutral = {
    id: 42, name: "DOGA release", artist: "Juana Molina", support_artists: "Support Act",
    date: "2026-08-01", doors_time: "19:00:00", show_time: "20:00:00",
    ticket_url: "https://tix", price_min: 20, price_max: 25, image_url: "https://img",
    genre: "Rock", subgenre: "Experimental", status: "on_sale", age_restriction: "18+",
    description: "desc", source: "manual", updated_at: "2026-07-01T00:00:00+00:00",
    venue_name: "Cat's Cradle", venue_slug: "cats-cradle", venue_city: "Carrboro", venue_color: "#222222",
  };
  const fc = toFullCalendarEvent(neutral);

  assert.equal(fc.id, 42);
  assert.equal(fc.title, "Juana Molina"); // artist wins over name
  assert.equal(fc.start, "2026-08-01");
  assert.equal(fc.allDay, true);
  assert.equal(fc.backgroundColor, "#222222");
  assert.equal(fc.borderColor, "#222222");
  assert.equal(fc.textColor, "#ffffff");

  // The extendedProps key set is a hard contract: modal.js/filters.js/favorites.js read
  // these by name and are not otherwise tested.
  assert.deepEqual(Object.keys(fc.extendedProps).sort(), [
    "age_restriction", "artist", "date", "description", "doors_time", "event_id",
    "genre", "image_url", "name", "price", "price_max", "price_min", "show_time",
    "status", "subgenre", "support_artists", "ticket_url", "venue_city",
    "venue_color", "venue_name", "venue_slug",
  ]);
  assert.equal(fc.extendedProps.venue_color, "#222222"); // resolved color, not raw
  assert.equal(fc.extendedProps.show_time, "8:00 PM");
  assert.equal(fc.extendedProps.doors_time, "7:00 PM");
  assert.equal(fc.extendedProps.price, "$20-$25");
  assert.equal(fc.extendedProps.price_min, 20); // raw number preserved alongside the string
});

test("falls back to name for the title and to the default color when venue_color is absent", () => {
  const fc = toFullCalendarEvent({ id: 1, name: "Open Mic", artist: null, date: "2026-08-02", venue_color: null });
  assert.equal(fc.title, "Open Mic");
  assert.equal(fc.backgroundColor, "#6366f1");
  assert.equal(fc.extendedProps.venue_color, "#6366f1");
});
