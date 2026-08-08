// Unit tests for the ticket_click GA analytics helpers (../js/analytics.js).
//
// Runs on Node's built-in test runner — no build step, no npm install:
//   node --test frontend/tests/
//
// Covers the two pure functions (the dataset -> gtag payload builder, and
// ticketAnchorAttrs, which stamps the data-* attributes both modal.js render sites
// read from) plus _onTicketClick, the click handler: it only touches `e.target` and
// `gtag`, both fakeable with plain objects, so it doesn't need a real DOM. A live
// click in a real browser (GA DebugView, under adblock) is still QA'd manually per
// the issue's checklist — this locks the payload/no-op logic, not the wiring.

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { ticketAnchorAttrs, ticketClickPayload, _onTicketClick } = require("../js/analytics.js");

test("ticketClickPayload maps a full dataset to the gtag event tuple", () => {
  const [name, params] = ticketClickPayload({ venueSlug: "cats-cradle", eventId: "42" });
  assert.equal(name, "ticket_click");
  assert.deepEqual(params, { venue_slug: "cats-cradle", event_id: "42" });
});

test("ticketClickPayload omits missing fields instead of stringifying undefined", () => {
  const [, onlyVenue] = ticketClickPayload({ venueSlug: "motorco" });
  assert.deepEqual(onlyVenue, { venue_slug: "motorco" });
  assert.equal("event_id" in onlyVenue, false);

  const [, empty] = ticketClickPayload({});
  assert.deepEqual(empty, {});

  const [, noArg] = ticketClickPayload();
  assert.deepEqual(noArg, {});
});

test("ticketAnchorAttrs emits both data attributes, namespaced away from filters.js's data-venue", () => {
  const attrs = ticketAnchorAttrs({ venueSlug: "cats-cradle", eventId: "42" });
  assert.equal(attrs, 'data-venue-slug="cats-cradle" data-event-id="42"');
});

test("ticketAnchorAttrs escapes quotes so a value can't break out of the attribute", () => {
  const attrs = ticketAnchorAttrs({ venueSlug: '"><script>evil', eventId: "1" });
  assert.equal(attrs.includes('"><script>'), false);
  assert.match(attrs, /^data-venue-slug="[^"]*" data-event-id="1"$/);
});

test("ticketAnchorAttrs tolerates missing props", () => {
  assert.equal(ticketAnchorAttrs({}), 'data-venue-slug="" data-event-id=""');
  assert.equal(ticketAnchorAttrs(), 'data-venue-slug="" data-event-id=""');
});

test("_onTicketClick emits ticket_click with the anchor's dataset for a.btn-tickets", () => {
  const calls = [];
  globalThis.gtag = (...args) => calls.push(args);
  try {
    const anchor = { dataset: { venueSlug: "cats-cradle", eventId: "42" } };
    _onTicketClick({ target: { closest: () => anchor } });
    assert.deepEqual(calls, [["event", "ticket_click", { venue_slug: "cats-cradle", event_id: "42" }]]);
  } finally {
    delete globalThis.gtag;
  }
});

test("_onTicketClick stays silent for clicks outside a.btn-tickets", () => {
  const calls = [];
  globalThis.gtag = (...args) => calls.push(args);
  try {
    _onTicketClick({ target: { closest: () => null } });
    assert.deepEqual(calls, []);
  } finally {
    delete globalThis.gtag;
  }
});

test("_onTicketClick no-ops when gtag isn't defined (adblock, no GA id)", () => {
  delete globalThis.gtag;
  const anchor = { dataset: { venueSlug: "cats-cradle", eventId: "42" } };
  // Would throw ("gtag is not a function") if the guard were missing.
  assert.doesNotThrow(() => _onTicketClick({ target: { closest: () => anchor } }));
});
