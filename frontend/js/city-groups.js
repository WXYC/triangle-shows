// City display grouping — a UI concern, deliberately not stored data.
//
// venues.city holds real municipalities; Chapel Hill and Carrboro are adjacent enough
// that the calendar presents them as one "Chapel Hill-Carrboro" filter chip. The API
// keeps accepting the grouping label as a query alias (backend/app/api/v1.py), but the
// only place the label is *produced* is here.

const CITY_DISPLAY_GROUPS = {
  "Chapel Hill": "Chapel Hill-Carrboro",
  Carrboro: "Chapel Hill-Carrboro",
};

// The filter-chip label for a municipality: its display group, or itself if ungrouped.
function cityDisplayGroup(city) {
  return CITY_DISPLAY_GROUPS[city] || city;
}

// Exported for the Node test runner (`node --test frontend/tests/`). Harmless in the
// browser, where `module` is undefined, so the file still works as a plain <script>.
if (typeof module !== "undefined" && module.exports) {
  module.exports = { cityDisplayGroup, CITY_DISPLAY_GROUPS };
}
