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
      if (!isLoading && typeof applyAllFilters === "function") {
        requestAnimationFrame(applyAllFilters);
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
        // Hide persistently (survives refresh until user restores)
        hideEvent(eventId);
        fcEvent.setProp("display", "none");
      }
    },
    true // capture
  );

  // Init favorites download button visibility
  if (typeof updateFavoritesButton === "function") updateFavoritesButton();
});
