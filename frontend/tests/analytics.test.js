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
const { ticketAnchorAttrs, ticketClickPayload, _onTicketClick, TICKET_ANCHOR_SELECTOR } = require("../js/analytics.js");

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

test("ticketAnchorAttrs escapes apostrophes too, so the attribute string is safe in either quoting style", () => {
  const attrs = ticketAnchorAttrs({ venueSlug: "o'brien's", eventId: "1" });
  assert.equal(attrs.includes("'"), false);
  assert.equal(attrs, 'data-venue-slug="o&#39;brien&#39;s" data-event-id="1"');
});

// Converts a rendered `data-*` attribute name to the camelCase key the browser's
// `element.dataset` API exposes it as (e.g. "data-venue-slug" -> "venueSlug").
// Test-only: mirrors browser behavior so the round-trip test below can go from
// ticketAnchorAttrs' rendered string back to a dataset object without a real DOM.
function _datasetKeyFromAttrName(attrName) {
  return attrName.replace(/^data-/, "").replace(/-([a-z])/g, (_, c) => c.toUpperCase());
}

// Parses a ticketAnchorAttrs() output string into the dataset object a real click
// handler reads off `anchor.dataset` in the browser.
function _parseDataset(attrString) {
  const dataset = {};
  const re = /data-([\w-]+)="([^"]*)"/g;
  let m;
  while ((m = re.exec(attrString))) {
    dataset[_datasetKeyFromAttrName(`data-${m[1]}`)] = m[2];
  }
  return dataset;
}

test("ticketAnchorAttrs output round-trips through the real dataset -> payload path (a rename on either side goes red)", () => {
  const attrs = ticketAnchorAttrs({ venueSlug: "cats-cradle", eventId: "42" });
  const dataset = _parseDataset(attrs);
  const [, params] = ticketClickPayload(dataset);
  assert.deepEqual(params, { venue_slug: "cats-cradle", event_id: "42" });
});

test("_onTicketClick emits ticket_click with the anchor's dataset for a.btn-tickets", () => {
  const calls = [];
  globalThis.gtag = (...args) => calls.push(args);
  try {
    const anchor = { dataset: { venueSlug: "cats-cradle", eventId: "42" } };
    const closestArgs = [];
    const closest = (selector) => {
      closestArgs.push(selector);
      return anchor;
    };
    _onTicketClick({ target: { closest } });
    // Ties the handler to the exported constant, not just to whatever `closest` was
    // stubbed to return — a delegated selector that drifted from TICKET_ANCHOR_SELECTOR
    // would still pass every other assertion here.
    assert.deepEqual(closestArgs, [TICKET_ANCHOR_SELECTOR]);
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

test("_onTicketClick no-ops when e.target is missing or lacks closest, matching app.js's defensive convention", () => {
  assert.doesNotThrow(() => _onTicketClick({}));
  assert.doesNotThrow(() => _onTicketClick({ target: null }));
  assert.doesNotThrow(() => _onTicketClick({ target: {} }));
});

test("_onTicketClick counts a middle-click (auxclick, button 1) on a.btn-tickets", () => {
  const calls = [];
  globalThis.gtag = (...args) => calls.push(args);
  try {
    const anchor = { dataset: { venueSlug: "cats-cradle", eventId: "42" } };
    _onTicketClick({ type: "auxclick", button: 1, target: { closest: () => anchor } });
    assert.deepEqual(calls, [["event", "ticket_click", { venue_slug: "cats-cradle", event_id: "42" }]]);
  } finally {
    delete globalThis.gtag;
  }
});

test("_onTicketClick ignores non-middle auxclicks (e.g. right-click, button 2)", () => {
  const calls = [];
  globalThis.gtag = (...args) => calls.push(args);
  try {
    const anchor = { dataset: { venueSlug: "cats-cradle", eventId: "42" } };
    _onTicketClick({ type: "auxclick", button: 2, target: { closest: () => anchor } });
    assert.deepEqual(calls, []);
  } finally {
    delete globalThis.gtag;
  }
});
