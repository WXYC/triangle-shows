// Shared harness for testing the frontend's plain <script> files under `node --test`.
//
// Most of `frontend/js/` can be `require()`d straight into a test (`city-groups.js`,
// `fullcalendar-adapter.js`, `analytics.js`): they export through `module.exports` and
// do nothing at load time. The rest cannot — `modal.js` calls `document.getElementById`
// as it loads, and `site.js`, `legacy-storage.js`, and `favorites.js` have the same
// shape. Requiring one of those throws before a single assertion runs.
//
// So load them the way a browser does instead: evaluate the source into a fresh `vm`
// context holding a minimal DOM stub. Top-level `function` declarations land as
// properties of the sandbox object, which is what lets a test call the real
// `_buildEventRow` / `openModal` rather than a reimplementation of the template code.
//
// This file lives under `helpers/` deliberately. The deploy gate is
// `node --test frontend/tests/*.test.js` (.github/workflows/deploy.yml), a glob that
// matches neither this directory nor this filename — a helper collected as a test file
// would be a suite with zero tests, which some runners treat as a failure.

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// Script paths are resolved against frontend/, so call sites read as "js/modal.js"
// rather than a chain of ../ hops out of the tests directory.
const FRONTEND_DIR = path.join(__dirname, "..", "..");

const _sourceCache = new Map();

function _readSource(scriptPath) {
  const absolute = path.isAbsolute(scriptPath)
    ? scriptPath
    : path.join(FRONTEND_DIR, scriptPath);
  if (!_sourceCache.has(absolute)) {
    _sourceCache.set(absolute, fs.readFileSync(absolute, "utf8"));
  }
  return { absolute, source: _sourceCache.get(absolute) };
}

// A stand-in for a DOM element, carrying just the surface the frontend scripts touch.
// `classList.addedClasses` records every class added, so a test can assert a script
// actually acted on a specific element instead of merely finding a stub with the right
// method names.
function stubElement() {
  const addedClasses = [];
  const removedClasses = [];
  return {
    innerHTML: "",
    classList: {
      addedClasses,
      removedClasses,
      add(cls) { addedClasses.push(cls); },
      remove(cls) { removedClasses.push(cls); },
    },
    addEventListener() {},
  };
}

// A stand-in for `document`, backed by a caller-owned `{ id: element }` map so the test
// keeps references to the elements it wants to assert on. An unknown id yields a fresh
// throwaway stub rather than null, matching the "script grabs an element it doesn't end
// up using" case without forcing every test to enumerate the whole page.
function stubDocument(elements = {}) {
  return {
    getElementById: (id) => elements[id] || stubElement(),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
  };
}

// Evaluates one or more plain scripts into a single fresh vm context, in order, and
// returns the sandbox they populated.
//
// Passing several scripts is the point of the array: load-order dependencies between
// them are real (modal.js reads the global `ticketAnchorAttrs` that analytics.js
// defines), and running each into its own context would hide exactly the coupling worth
// testing. Omitting a script models it failing to load in the browser.
//
// `globals` seeds additional sandbox properties before evaluation — a stub `window`,
// `localStorage`, `fetch`, and so on.
function loadScripts(scriptPaths, { documentStub, globals } = {}) {
  const sandbox = { ...globals };
  if (documentStub !== undefined) {
    sandbox.document = documentStub;
  }
  vm.createContext(sandbox);
  for (const scriptPath of scriptPaths) {
    const { absolute, source } = _readSource(scriptPath);
    // filename surfaces the real path in stack traces from inside the sandbox.
    vm.runInContext(source, sandbox, { filename: absolute });
  }
  return { sandbox };
}

module.exports = { loadScripts, stubElement, stubDocument, FRONTEND_DIR };
