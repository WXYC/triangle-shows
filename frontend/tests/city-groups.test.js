// Unit tests for the city display grouping (../js/city-groups.js).
//
// Runs on Node's built-in test runner — no build step, no npm install:
//   node --test frontend/tests/
//
// venues.city now holds real municipalities (Chapel Hill and Carrboro separately);
// "Chapel Hill-Carrboro" survives only as a display/filter grouping. These lock the
// mapping the filter chips are derived from.

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { cityDisplayGroup } = require("../js/city-groups.js");

test("Chapel Hill and Carrboro group into the combined chip", () => {
  assert.equal(cityDisplayGroup("Chapel Hill"), "Chapel Hill-Carrboro");
  assert.equal(cityDisplayGroup("Carrboro"), "Chapel Hill-Carrboro");
});

test("ungrouped municipalities display as themselves", () => {
  assert.equal(cityDisplayGroup("Durham"), "Durham");
  assert.equal(cityDisplayGroup("Cary"), "Cary");
  assert.equal(cityDisplayGroup("Saxapahaw"), "Saxapahaw");
});
