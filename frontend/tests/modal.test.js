// Tests for the ticket-anchor render paths in ../js/modal.js.
//
// modal.js is a plain <script> (no module.exports) that touches `document` at load
// time, so it can't be `require()`d directly under Node's test runner. Load it into a
// vm context with a minimal DOM stub instead — top-level function declarations become
// properties of the sandbox object, so tests here call modal.js's actual
// `_buildEventRow` / `openModal` template code, not a reimplementation of it.
//
// Covers two things:
//  - The modal must not hard-depend on /js/analytics.js: if that script is blocked,
//    404s, or is missing from a stale cached index.html, `ticketAnchorAttrs` is
//    undefined when modal.js runs. Both render sites must still produce a well-formed
//    anchor instead of throwing (which previously killed the modal for every show
//    with a ticket URL — see modal.js:33/:133).
//  - When analytics.js *has* loaded, the rendered anchor markup must carry the class
//    analytics.js's delegated click listener selects on (TICKET_ANCHOR_SELECTOR)
//    alongside the data-* attributes. The expected class below is parsed out of that
//    exported constant rather than hardcoded, so a selector change on either side
//    (the constant, or the class either render site emits) turns this suite red.

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { TICKET_ANCHOR_SELECTOR } = require("../js/analytics.js");

const MODAL_SRC = fs.readFileSync(path.join(__dirname, "../js/modal.js"), "utf8");
const ANALYTICS_SRC = fs.readFileSync(path.join(__dirname, "../js/analytics.js"), "utf8");

// TICKET_ANCHOR_SELECTOR is a CSS selector like "a.btn-tickets"; pull the class token
// back out of it so assertions below check the render sites against the real constant
// instead of a copy-pasted literal.
const _selectorMatch = /^[a-z]+\.([\w-]+)$/.exec(TICKET_ANCHOR_SELECTOR);
if (!_selectorMatch) {
  throw new Error(`TICKET_ANCHOR_SELECTOR "${TICKET_ANCHOR_SELECTOR}" isn't a simple tag.class selector`);
}
const TICKET_ANCHOR_CLASS = _selectorMatch[1];

function _stubElement() {
  const addedClasses = [];
  return {
    innerHTML: "",
    classList: {
      addedClasses,
      add(cls) { addedClasses.push(cls); },
      remove() {},
    },
    addEventListener() {},
  };
}

// Loads modal.js into a fresh vm context with a minimal DOM stub. When `withAnalytics`
// is true, analytics.js is executed into the same context first (so `ticketAnchorAttrs`
// is a real global by the time modal.js runs) — simulating a normal page load. When
// false, `ticketAnchorAttrs` is left undefined — simulating analytics.js failing to load.
function loadModal({ withAnalytics }) {
  const elements = {
    "event-modal": _stubElement(),
    "modal-overlay": _stubElement(),
    "modal-content": _stubElement(),
  };
  const documentStub = {
    getElementById: (id) => elements[id] || _stubElement(),
    addEventListener() {},
  };
  const sandbox = { document: documentStub };
  vm.createContext(sandbox);
  if (withAnalytics) {
    vm.runInContext(ANALYTICS_SRC, sandbox);
  }
  vm.runInContext(MODAL_SRC, sandbox);
  return { sandbox, elements };
}

test("_buildEventRow (group modal row) renders a well-formed ticket anchor without throwing when analytics.js hasn't loaded", () => {
  const { sandbox } = loadModal({ withAnalytics: false });
  const ev = {
    id: "42",
    title: "Show Title",
    extendedProps: {
      venue_slug: "cats-cradle",
      ticket_url: "https://tickets.example.com/42",
      support_artists: [],
    },
  };

  let html;
  assert.doesNotThrow(() => {
    html = sandbox._buildEventRow(ev);
  });
  assert.match(
    html,
    /<a href="https:\/\/tickets\.example\.com\/42" target="_blank" rel="noopener" class="[^"]*"\s*>Get Tickets<\/a>/
  );
  assert.match(html, new RegExp(`class="${TICKET_ANCHOR_CLASS} btn-tickets-sm"`));
  assert.equal(html.includes("data-venue-slug"), false);
  assert.equal(html.includes("data-event-id"), false);
});

test("openModal (single-event modal) renders a well-formed ticket anchor without throwing when analytics.js hasn't loaded", () => {
  const { sandbox, elements } = loadModal({ withAnalytics: false });
  const eventInfo = {
    event: {
      id: "42",
      extendedProps: {
        date: "2026-08-08",
        name: "Show Name",
        venue_slug: "cats-cradle",
        venue_name: "Cat's Cradle",
        venue_city: "Carrboro",
        ticket_url: "https://tickets.example.com/42",
      },
    },
  };

  assert.doesNotThrow(() => sandbox.openModal(eventInfo));
  const html = elements["modal-content"].innerHTML;
  assert.match(
    html,
    /<a href="https:\/\/tickets\.example\.com\/42" target="_blank" rel="noopener" class="[^"]*"\s*>Get Tickets<\/a>/
  );
  assert.match(html, new RegExp(`class="${TICKET_ANCHOR_CLASS}"`));
  assert.equal(html.includes("data-venue-slug"), false);
  assert.equal(html.includes("data-event-id"), false);
  // Confirms the modal actually opened: classList.add("active") ran against the real
  // #event-modal element, not just that the stub still has an `add` method.
  assert.deepEqual(elements["event-modal"].classList.addedClasses, ["active"]);
});

test("_buildEventRow carries class=\"btn-tickets\" alongside the real data-* attributes when analytics.js has loaded", () => {
  const { sandbox } = loadModal({ withAnalytics: true });
  const ev = {
    id: "42",
    title: "Show Title",
    extendedProps: {
      venue_slug: "cats-cradle",
      ticket_url: "https://tickets.example.com/42",
      support_artists: [],
    },
  };

  const html = sandbox._buildEventRow(ev);
  assert.match(html, new RegExp(`class="${TICKET_ANCHOR_CLASS} btn-tickets-sm"`));
  assert.match(html, /data-venue-slug="cats-cradle"/);
  assert.match(html, /data-event-id="42"/);
});

test("openModal carries class=\"btn-tickets\" alongside the real data-* attributes when analytics.js has loaded", () => {
  const { sandbox, elements } = loadModal({ withAnalytics: true });
  const eventInfo = {
    event: {
      id: "42",
      extendedProps: {
        date: "2026-08-08",
        name: "Show Name",
        venue_slug: "cats-cradle",
        venue_name: "Cat's Cradle",
        venue_city: "Carrboro",
        ticket_url: "https://tickets.example.com/42",
      },
    },
  };

  sandbox.openModal(eventInfo);
  const html = elements["modal-content"].innerHTML;
  assert.match(html, new RegExp(`class="${TICKET_ANCHOR_CLASS}"`));
  assert.match(html, /data-venue-slug="cats-cradle"/);
  assert.match(html, /data-event-id="42"/);
});
