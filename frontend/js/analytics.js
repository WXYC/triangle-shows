// Outbound "Get Tickets" click tracking — the monetizable-demand proxy for the
// unit-economics experiment (issue #87). Fires a GA `ticket_click` event, carrying the
// venue slug (the stable API identifier, not the display name) and the event id.
//
// One delegated click listener on a.btn-tickets, following the city-groups.js
// plain-script pattern: pure functions + a thin DOM hookup, require-able from Node.

// Escape a value for safe use inside an HTML attribute, mirroring modal.js's _h().
function _escapeAttr(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// The delegated click listener's target selector, shared with the tests so the
// selector and what the modal.js render sites actually emit stay provably coupled
// (see analytics.test.js and modal.test.js) instead of drifting behind a hardcoded
// literal on each side.
const TICKET_ANCHOR_SELECTOR = "a.btn-tickets";

// data-* attribute string for a ticket anchor. Both modal.js render sites (the modal
// button and the list-row variant) call this, so the attribute/selector coupling that
// _onTicketClick relies on lives in one place. Named data-venue-slug (not data-venue)
// because filters.js already owns that attribute on the venue-checkbox inputs, and
// three of its selectors (filters.js:125,217,371) are unscoped — a shared name would
// let a stale modal anchor answer a checkbox existence probe after closeModal (which
// never clears #modal-content).
function ticketAnchorAttrs({ venueSlug, eventId } = {}) {
  return `data-venue-slug="${_escapeAttr(venueSlug)}" data-event-id="${_escapeAttr(eventId)}"`;
}

// A clicked anchor's dataset -> the gtag('event', name, params) tuple. Missing fields
// are omitted from params entirely — never serialized as the string "undefined".
function ticketClickPayload(dataset = {}) {
  const params = {};
  if (dataset.venueSlug) params.venue_slug = dataset.venueSlug;
  if (dataset.eventId) params.event_id = dataset.eventId;
  return ["ticket_click", params];
}

function _onTicketClick(e) {
  if (!e.target || typeof e.target.closest !== "function") return;
  // auxclick fires for any non-primary button (middle-click, and right-click in some
  // browsers when no context menu is shown); only a middle-click should count.
  if (e.type === "auxclick" && e.button !== 1) return;
  const anchor = e.target.closest(TICKET_ANCHOR_SELECTOR);
  if (!anchor || typeof gtag !== "function") return;
  const [name, params] = ticketClickPayload(anchor.dataset);
  gtag("event", name, params);
}

// Harmless in Node (no `document`); in the browser this is the whole wiring. auxclick
// covers middle-clicks on these target="_blank" anchors, which "click" never sees —
// without it, the metric undercounts in a way that a cross-metro comparison can't
// absorb (browsing habits around middle-click vary).
if (typeof document !== "undefined") {
  document.addEventListener("click", _onTicketClick);
  document.addEventListener("auxclick", _onTicketClick);
}

// Exported for the Node test runner (`node --test frontend/tests/`). Harmless in the
// browser, where `module` is undefined, so the file still works as a plain <script>.
if (typeof module !== "undefined" && module.exports) {
  module.exports = { ticketAnchorAttrs, ticketClickPayload, _onTicketClick, TICKET_ANCHOR_SELECTOR };
}
