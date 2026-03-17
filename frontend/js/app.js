// Rotating ko-fi pitch
const KOFI_PITCHES = [
  "help me pay for this domain",
  "keeps the scraper alive",
  "offset my caffeine dependency",
  "bribing the Ticketmaster API",
  "buy me a beer at Motorco",
  "one less thing to cancel",
  "cheaper than a ticket stub",
  "fuel for the scraper",
  "better than a StubHub fee",
  "my contribution to the Triangle",
  "helping you find your next show",
  "Haw River or bust",
  "keeps this free for everyone",
  "the server bill is real",
  "support local show-going",
];

document.addEventListener("DOMContentLoaded", function () {
  // Restore saved palette
  const savedPalette = localStorage.getItem("triangle-shows-palette");
  if (savedPalette && PALETTES[savedPalette]) {
    applyPalette(savedPalette);
  }

  // Restore saved mode, or detect OS preference
  const savedMode = localStorage.getItem("triangle-shows-mode");
  if (savedMode) {
    applyMode(savedMode);
  } else if (window.matchMedia("(prefers-color-scheme: light)").matches) {
    applyMode("light");
  } else {
    applyMode("dark");
  }

  // Rotating ko-fi pitch
  const pitchEl = document.querySelector(".kofi-pitch");
  if (pitchEl) {
    pitchEl.textContent = KOFI_PITCHES[Math.floor(Math.random() * KOFI_PITCHES.length)];
  }
});

// FullCalendar initialization
let calendar;
const _loadingScreenStart = Date.now();

// ── Loading screen progress bar ──────────────────────────────────────────────
const _BAR_LEN = 20;
let _barRaf = null;

function _renderBar(pct) {
  const track = document.getElementById("ls-bar-track");
  const label = document.getElementById("ls-bar-pct");
  if (!track || !label) return;
  const filled = Math.round(pct / 100 * _BAR_LEN);
  track.innerHTML =
    '<span class="ls-bar-filled">' + "█".repeat(filled) + "</span>" +
    "░".repeat(_BAR_LEN - filled);
  label.textContent = "\u00a0".repeat(3 - String(Math.round(pct)).length) + Math.round(pct) + "%";
}

function _startProgressBar() {
  _renderBar(0);
  const start = performance.now();
  const FILL_MS = 900;
  function tick(now) {
    const t = Math.min((now - start) / FILL_MS, 1);
    _renderBar((1 - Math.pow(1 - t, 3)) * 85);
    if (t < 1) _barRaf = requestAnimationFrame(tick);
  }
  _barRaf = requestAnimationFrame(tick);
}

function _finishProgressBar(cb) {
  if (_barRaf) { cancelAnimationFrame(_barRaf); _barRaf = null; }
  _renderBar(100);
  setTimeout(cb, 200);
}
// ─────────────────────────────────────────────────────────────────────────────

// ── Per-day hidden-shows chips ────────────────────────────────────────────────
function _setHiddenChip(date, count) {
  // Month view: bordered chip in the day cell bottom
  const existing = document.querySelector(`.day-hidden-chip[data-date="${date}"]`);
  if (existing) existing.remove();
  const bottom = document.querySelector(`.fc-daygrid-day[data-date="${date}"] .fc-daygrid-day-bottom`);
  if (count > 0 && bottom) {
    const chip = document.createElement("a");
    chip.className = "day-hidden-chip";
    chip.dataset.date = date;
    chip.textContent = `↺ ${count} hidden`;
    chip.addEventListener("click", (e) => { e.stopPropagation(); unhideForDate(date); });
    bottom.appendChild(chip);
  }

  // List view: restore row after the last event row for this day
  const existingRow = document.querySelector(`.fc-list-hidden-row[data-date="${date}"]`);
  if (existingRow) existingRow.remove();
  if (count > 0) {
    const dayRow = document.querySelector(`tr.fc-list-day[data-date="${date}"]`);
    if (dayRow) {
      let insertAfter = dayRow;
      let sib = dayRow.nextElementSibling;
      while (sib && !sib.classList.contains("fc-list-day")) {
        if (sib.classList.contains("fc-list-event")) insertAfter = sib;
        sib = sib.nextElementSibling;
      }
      const label = count === 1 ? "↺ 1 hidden show" : `↺ ${count} hidden shows`;
      const tr = document.createElement("tr");
      tr.className = "fc-list-hidden-row";
      tr.dataset.date = date;
      tr.innerHTML = `<td colspan="3" class="fc-list-hidden-cell"><button class="list-hidden-btn">${label}</button></td>`;
      tr.querySelector(".list-hidden-btn").addEventListener("click", () => unhideForDate(date));
      insertAfter.after(tr);
    }
  }
}

function _updateHiddenChip(date) {
  const count = calendar.getEvents().filter(
    (ev) => ev.extendedProps.date === date && isHidden(ev.id)
  ).length;
  _setHiddenChip(date, count);
}

function _updateAllHiddenChips() {
  document.querySelectorAll(".day-hidden-chip, .fc-list-hidden-row").forEach((el) => el.remove());
  const byDate = {};
  calendar.getEvents().forEach((ev) => {
    if (isHidden(ev.id)) {
      const d = ev.extendedProps.date;
      byDate[d] = (byDate[d] || 0) + 1;
    }
  });
  Object.entries(byDate).forEach(([date, count]) => _setHiddenChip(date, count));
}
// ─────────────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", function () {
  const calendarEl = document.getElementById("calendar");

  calendar = new FullCalendar.Calendar(calendarEl, {
    initialView: window.innerWidth < 768 ? "listUpcoming" : "dayGridMonth",
    views: {
      listUpcoming: {
        type: "list",
        duration: { days: 180 },
        buttonText: "list",
      },
    },
    headerToolbar: {
      left: "prev,next today",
      center: "title",
      right: "dayGridMonth,listUpcoming",
    },
    height: "auto",
    fixedWeekCount: false,
    displayEventTime: false,
    eventSources: [
      {
        url: `${API_BASE}/api/events/fullcalendar`,
        method: "GET",
        // No server-side filter params — all filtering is client-side.
        failure: function () {
          console.error("Failed to fetch events");
        },
      },
    ],
    eventClassNames: function (arg) {
      const classes = [];
      const today = new Date(); today.setHours(0, 0, 0, 0);
      if (arg.event.start && arg.event.start < today) classes.push("ev-past");
      if (arg.event.extendedProps.status === "sold_out") classes.push("ev-sold-out");
      if (typeof isFavorited === "function" && isFavorited(arg.event.id)) classes.push("ev-hearted");
      return classes;
    },
    eventClick: function (info) {
      info.jsEvent.preventDefault();
      openModal(info);
    },
    eventContent: function (arg) {
      const props   = arg.event.extendedProps;
      const soldOut = props.status === "sold_out" ? " [sold out]" : "";
      const hearted = typeof isFavorited === "function" && isFavorited(arg.event.id);
      const matched = typeof eventMatchesSpotify === "function" &&
                      typeof isSpotifyConnected  === "function" &&
                      isSpotifyConnected() &&
                      eventMatchesSpotify(arg.event.title, props.artist);
      const titleText = (matched ? "♫ " : "") + arg.event.title + soldOut;

      const html = `<div class="ev" style="--venue-color: ${props.venue_color || ''}">
        <button class="ev-heart${hearted ? " hearted" : ""}"
                data-event-id="${arg.event.id}"
                aria-label="${hearted ? "Remove from favorites" : "Add to favorites"}"
                tabindex="-1">${hearted ? "♥" : "♡"}</button>
        <button class="ev-hide"
                data-event-id="${arg.event.id}"
                aria-label="Hide this show"
                tabindex="-1">✕</button>
        <span class="ev-title">${titleText}</span>
        ${props.venue_name ? `<span class="ev-venue">${props.venue_name}</span>` : ""}
      </div>`;

      return { html };
    },
    windowResize: function (view) {
      if (window.innerWidth < 768) {
        calendar.changeView("listUpcoming");
      } else {
        calendar.changeView("dayGridMonth");
      }
    },
    // After all events finish loading, apply filters in one pass. Deferred via
    // requestAnimationFrame so FullCalendar finishes its own render cycle first —
    // calling setProp mid-render can cause list-view events to appear duplicated.
    loading: function (isLoading) {
      if (isLoading) {
        _startProgressBar();
        return;
      }
      const elapsed = Date.now() - _loadingScreenStart;
      const delay = Math.max(0, 1000 - elapsed);
      setTimeout(function () {
        _finishProgressBar(function () {
          const screen = document.getElementById("loading-screen");
          if (screen) {
            screen.classList.add("fade-out");
            screen.addEventListener("transitionend", () => screen.remove(), { once: true });
          }
        });
      }, delay);
        if (typeof applyAllFilters === "function") requestAnimationFrame(applyAllFilters);
      }
    },
    eventDidMount: function (info) {
      // Persist-hidden: suppress events the user dismissed.
      if (typeof isHidden === "function" && isHidden(info.event.id)) {
        info.event.setProp("display", "none");
        return;
      }
      // Restore heart state for events loaded after page init.
      if (typeof isFavorited === "function" && isFavorited(info.event.id)) {
        const btn = info.el.querySelector(".ev-heart");
        if (btn) { btn.classList.add("hearted"); btn.textContent = "♥"; }
        info.el.classList.add("ev-hearted");
      }
      // List view: walk up to the <tr> (info.el is the title <td> or <a>, not
      // the row), then hide the sibling time cell and narrow the graphic cell.
      const tr = info.el.closest && info.el.closest("tr.fc-list-event");
      if (tr) {
        const timeTd = tr.querySelector(".fc-list-event-time");
        if (timeTd) { timeTd.style.display = "none"; timeTd.style.width = "0"; timeTd.style.padding = "0"; }
        const graphicTd = tr.querySelector(".fc-list-event-graphic");
        if (graphicTd) { graphicTd.style.width = "22px"; graphicTd.style.maxWidth = "22px"; }
      }
    },
  });

  calendar.render();

  // ── Heart / favorite click handler ──────────────────────────────────────
  // Use capture phase so we intercept before FullCalendar's eventClick fires.
  calendarEl.addEventListener(
    "click",
    function (e) {
      const heartBtn = e.target.closest(".ev-heart");
      const hideBtn  = e.target.closest(".ev-hide");
      if (!heartBtn && !hideBtn) return;
      e.stopPropagation(); // prevent modal from opening

      const eventId = (heartBtn || hideBtn).dataset.eventId;
      const fcEvent = calendar.getEventById(eventId);
      if (!fcEvent) return;

      if (heartBtn) {
        const p = fcEvent.extendedProps;
        toggleFavorite(eventId, {
          id:         eventId,
          title:      fcEvent.title,
          date:       p.date       || "",
          show_time:  p.show_time  || null,
          venue_name: p.venue_name || "",
          venue_city: p.venue_city || "",
          ticket_url: p.ticket_url || "",
        });
      } else {
        // For grouped venues (e.g. DPAC), hide all same-day events so a
        // different sibling doesn't surface on the next filter pass.
        const slug = fcEvent.extendedProps.venue_slug;
        const date = fcEvent.extendedProps.date;
        if (typeof GROUPED_VENUE_SLUGS !== "undefined" && GROUPED_VENUE_SLUGS.has(slug)) {
          calendar.getEvents().forEach((ev) => {
            if (ev.extendedProps.venue_slug === slug && ev.extendedProps.date === date) {
              hideEvent(ev.id);
              ev.setProp("display", "none");
            }
          });
        } else {
          hideEvent(eventId);
          fcEvent.setProp("display", "none");
        }
        _updateHiddenChip(date);
      }
    },
    true // capture
  );

  // Init favorites download button visibility
  if (typeof updateFavoritesButton === "function") updateFavoritesButton();
});
