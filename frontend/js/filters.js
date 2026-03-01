// Filter logic — all filtering is done client-side.
// The API is fetched once per calendar month (FullCalendar's default); no server-side
// filter params are sent. Venue, city, and search filters are applied locally.

let venues = [];
let activeFilters = {
  search: "",
  forYou: false,
};

async function loadVenues(attempt = 0) {
  try {
    const resp = await fetch(`${API_BASE}/api/venues`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    venues = await resp.json();
    renderFilters();
  } catch (err) {
    console.error("Failed to load venues:", err);
    if (attempt < 2) {
      setTimeout(() => loadVenues(attempt + 1), 2000);
    }
  }
}

function renderFilters() {
  renderCityFilters();
  renderVenueFilters();
  setupSearch();

  if (window.innerWidth < 768) {
    // Mobile startup: open sidebar and start with no venues checked,
    // so the user intentionally picks what they want to see.
    document.querySelectorAll(".venue-checkbox input[type=checkbox]").forEach((cb) => {
      cb.checked = false;
    });
    applyAllFilters();
    updateCityChipStates();
    toggleSidebar();
  } else {
    updateCityChipStates(); // set initial active state (all cities on)
  }
}

function renderCityFilters() {
  const container = document.getElementById("city-filters");
  const cities = [...new Set(venues.map((v) => v.city))].sort();

  container.innerHTML = cities
    .map((city) => {
      const colors = CITY_COLORS[city] || {};
      const border = colors.border || "var(--dim)";
      const activeBg = colors.activeBg || "var(--accent-bg)";
      return `<button class="chip city-chip" data-city="${city}"
        style="--chip-border: ${border}; --chip-active-bg: ${activeBg}"
        onclick="toggleCity('${city}')">${city}</button>`;
    })
    .join("");
}

function renderVenueFilters() {
  const container = document.getElementById("venue-filters");
  container.innerHTML = venues
    .map(
      (v) => `
      <label class="venue-checkbox" data-venue-city="${v.city}">
        <input type="checkbox" data-venue="${v.slug}" checked onchange="toggleVenue('${v.slug}')">
        <span class="venue-dot" style="background-color: ${v.color}"></span>
        <span class="venue-label">${v.name}</span>
      </label>`
    )
    .join("");
}

function setupSearch() {
  const input = document.getElementById("search-input");
  let timeout;
  let viewSwitchedBySearch = false;

  input.addEventListener("input", () => {
    const q = input.value.trim();

    // Immediately switch to list view on first keystroke
    if (q && calendar && calendar.view.type !== "listUpcoming") {
      calendar.changeView("listUpcoming");
      viewSwitchedBySearch = true;
    }

    // Switch back to month grid when search is cleared (desktop only)
    if (!q && viewSwitchedBySearch && window.innerWidth >= 768) {
      calendar.changeView("dayGridMonth");
      viewSwitchedBySearch = false;
    }

    // Debounce the filter application
    clearTimeout(timeout);
    timeout = setTimeout(() => {
      activeFilters.search = q;
      applyAllFilters();
    }, 300);
  });
}

// ── Core filter logic ─────────────────────────────────────────────────────

// Toggle the "★ for you" Spotify filter on/off.
function toggleForYou() {
  activeFilters.forYou = !activeFilters.forYou;
  const chip = document.getElementById("chip-for-you");
  if (chip) chip.classList.toggle("active", activeFilters.forYou);
  applyAllFilters();
}

// Generate a webcal:// subscription URL from the currently-checked venues
// and redirect to it, triggering the native Add Subscription dialog.
function subscribeToVenues() {
  const allCbs  = [...document.querySelectorAll(".venue-checkbox input[type=checkbox]")];
  const checked = allCbs.filter(cb => cb.checked);
  let path = "/feeds/events.ics";
  if (checked.length > 0 && checked.length < allCbs.length) {
    path += "?venue=" + checked.map(cb => cb.dataset.venue).join(",");
  }
  window.location.href = `webcal://${window.location.host}${path}`;
}

// Returns true if an event should be visible given the current filter state.
// venueMap is a pre-built {slug: boolean} lookup (passed in to avoid per-event DOM queries).
function _checkEventVisible(ev, venueMap) {
  const props = ev.extendedProps;

  // "For you" mode: show only Spotify-matched events, ignoring venue filters.
  if (activeFilters.forYou) {
    return typeof eventMatchesSpotify === "function" &&
      eventMatchesSpotify(ev.title, props.artist);
  }

  // Venue checkbox — if unchecked, hide
  if (venueMap && props.venue_slug in venueMap) {
    if (!venueMap[props.venue_slug]) return false;
  } else if (!venueMap) {
    // Fallback: direct DOM query (used when called outside applyAllFilters)
    const cb = document.querySelector(`[data-venue="${props.venue_slug}"]`);
    if (cb && !cb.checked) return false;
  }

  // Search filter
  if (activeFilters.search) {
    const q = activeFilters.search.toLowerCase();
    const haystack = [ev.title, props.name, props.artist, props.venue_name]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    if (!haystack.includes(q)) return false;
  }

  return true;
}

// Apply all active filters to every event currently in the calendar.
function applyAllFilters() {
  if (!calendar) return;

  // Build a slug→checked map once so _checkEventVisible never touches the DOM per event.
  const venueMap = {};
  document.querySelectorAll(".venue-checkbox input[type=checkbox]").forEach((cb) => {
    venueMap[cb.dataset.venue] = cb.checked;
  });

  calendar.getEvents().forEach((ev) => {
    const target = _checkEventVisible(ev, venueMap) ? "auto" : "none";
    if (ev.display !== target) ev.setProp("display", target);
  });
}

// ── Filter toggles ────────────────────────────────────────────────────────

// City chips solo/restore — mirrors the venue checkbox behaviour but at city level.
//   • All venues on  → click city  → solo that city (hide all other cities).
//   • Only that city → click city  → restore all venues.
//   • Mixed state    → click city  → enable all venues in that city.
function toggleCity(city) {
  const allCheckboxes = [
    ...document.querySelectorAll(".venue-checkbox input[type=checkbox]"),
  ];
  if (!allCheckboxes.length) return; // venues not yet loaded

  const cityCheckboxes = [
    ...document.querySelectorAll(
      `.venue-checkbox[data-venue-city="${city}"] input[type=checkbox]`
    ),
  ];
  if (!cityCheckboxes.length) return; // unknown city

  const totalChecked  = allCheckboxes.filter((cb) => cb.checked).length;
  const total         = allCheckboxes.length;
  const cityChecked   = cityCheckboxes.filter((cb) => cb.checked).length;
  const cityTotal     = cityCheckboxes.length;

  if (totalChecked === total) {
    // Everything is on → solo this city
    allCheckboxes.forEach((cb) => {
      cb.checked = cityCheckboxes.includes(cb);
    });
  } else if (cityChecked === cityTotal && totalChecked === cityTotal) {
    // Only this city is showing → restore all
    allCheckboxes.forEach((cb) => { cb.checked = true; });
  } else {
    // Mixed state → enable all venues in this city
    cityCheckboxes.forEach((cb) => { cb.checked = true; });
  }

  applyAllFilters();
  updateCityChipStates();
}

// City chip active state: active = at least one venue in that city is enabled.
function updateCityChipStates() {
  document.querySelectorAll(".city-chip").forEach((btn) => {
    const city = btn.dataset.city;
    const checkboxes = [
      ...document.querySelectorAll(
        `.venue-checkbox[data-venue-city="${city}"] input[type=checkbox]`
      ),
    ];
    const anyEnabled = checkboxes.some((cb) => cb.checked);
    btn.classList.toggle("active", anyEnabled);
  });
}

// Venue checkbox toggle with solo/restore behavior:
// • If all venues were enabled and user unchecks one → solo that venue (show only it).
// • If only one venue was enabled and user unchecks it → restore all venues.
// • Otherwise → normal toggle.
function toggleVenue(slug) {
  const allCheckboxes = [
    ...document.querySelectorAll(".venue-checkbox input[type=checkbox]"),
  ];
  const cb = document.querySelector(`[data-venue="${slug}"]`);
  const checkedCount = allCheckboxes.filter((c) => c.checked).length;
  const total = allCheckboxes.length;

  if (!cb.checked && checkedCount === total - 1) {
    // All were enabled; user unchecked one → solo it
    allCheckboxes.forEach((c) => {
      c.checked = c === cb;
    });
  } else if (!cb.checked && checkedCount === 0) {
    // Only this venue was enabled; user unchecked it → restore all
    allCheckboxes.forEach((c) => {
      c.checked = true;
    });
  }
  // else: normal toggle, checkbox state already updated by browser

  applyAllFilters();
  updateCityChipStates();
}

// Toggle filter sidebar on mobile
function toggleSidebar() {
  const sidebar = document.getElementById("sidebar");
  const backdrop = document.getElementById("sidebar-backdrop");
  sidebar.classList.toggle("open");
  if (backdrop) backdrop.classList.toggle("active");
}

// Initialize
loadVenues();
