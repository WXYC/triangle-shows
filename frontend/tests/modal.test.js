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
//  - When analytics.js *has* loaded, the rendered anchor markup must carry
//    class="btn-tickets" alongside the data-* attributes, since analytics.js's
//    delegated click listener selects on `a.btn-tickets` — that selector has to stay
//    coupled to what the render sites actually emit.

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const MODAL_SRC = fs.readFileSync(path.join(__dirname, "../js/modal.js"), "utf8");
const ANALYTICS_SRC = fs.readFileSync(path.join(__dirname, "../js/analytics.js"), "utf8");

function _stubElement() {
  return {
    innerHTML: "",
    classList: { add() {}, remove() {} },
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
    /<a href="https:\/\/tickets\.example\.com\/42" target="_blank" rel="noopener" class="btn-tickets btn-tickets-sm"\s*>Get Tickets<\/a>/
  );
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
    /<a href="https:\/\/tickets\.example\.com\/42" target="_blank" rel="noopener" class="btn-tickets"\s*>Get Tickets<\/a>/
  );
  assert.equal(html.includes("data-venue-slug"), false);
  assert.equal(html.includes("data-event-id"), false);
  assert.equal(elements["event-modal"].classList.add.name !== undefined, true); // sanity: still an object
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
  assert.match(html, /class="btn-tickets btn-tickets-sm"/);
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
  assert.match(html, /class="btn-tickets"/);
  assert.match(html, /data-venue-slug="cats-cradle"/);
  assert.match(html, /data-event-id="42"/);
});
