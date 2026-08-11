/**
 * DOM-harness regression pins for the two WP 4a.7 wiring fixes recorded in
 * docs/WORKPACKAGES.md's Phase 4a header as "candidate for the WP 4a.8
 * polish pass": (1) a transient sheet closes when the user navigates away
 * (mockup rule, docs/design/vault-app-mockup-NOTES.md "Behavior rule for
 * the real app"), and (2) a status icon whose word is already shown visibly
 * elsewhere is marked `aria-hidden` so it is not announced twice.
 *
 * Uses the shared fake DOM (`fake-dom.js`) rather than a real browser or
 * jsdom — same "plain object exposing only what the module touches, set
 * BEFORE importing it" pattern `store-poll-loop.test.js` established for
 * `store.js`'s `document` usage, extended just far enough (see that file's
 * header) to run `router.js` (touches `window.addEventListener` at module
 * load) and `sheet-dialog.js`/`status-icon.js` (build real element/SVG
 * subtrees, need `createElement`/`createElementNS`).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, before, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom, fakeKeyEvent } from "./fake-dom.js";
import { resetModalStack } from "../js/lib/modal-stack.js";

const dom = createFakeDom();
globalThis.document = dom.document;
globalThis.window = dom.window;

// router.js reads `window.addEventListener("popstate", ...)` at MODULE LOAD
// time (not lazily inside a function, unlike store.js) — the globals above
// must exist before this import, same requirement store-poll-loop.test.js
// documents for store.js's lazier document access.
const { onViewChange, navigateTo } = await import("../js/router.js");
const { createSheetDialog } = await import("../js/components/sheet-dialog.js");
const { createStatusIcon } = await import("../js/components/status-icon.js");

beforeEach(() => {
  // Defensive, same as modal-stack.test.js: each test below builds its own
  // createSheetDialog() instance and closes it by the end, so nothing
  // SHOULD leak across cases — but lib/modal-stack.js's stack is module-
  // level singleton state, and a reset here means a bug in one test's
  // cleanup cannot silently poison a later, unrelated test's assertions.
  resetModalStack(dom.document);
});

// ---------------------------------------------------------------------
// (1) "Navigation dismisses transient surfaces" — every real sheet
// component (clients-sheet.js, notifications.js, game-detail-sheet.js)
// wires this with the exact one-liner reproduced here:
//   onViewChange(() => dialog.close());
// ---------------------------------------------------------------------
test("a sheet opened via createSheetDialog closes when the view changes", () => {
  const dialog = createSheetDialog({ ariaLabel: "Test sheet" });
  onViewChange(() => dialog.close());

  const invoker = dom.document.createElement("button");
  dom.document.activeElement = invoker; // what "opened" the sheet

  dialog.open();
  assert.equal(dialog.isOpen(), true);

  navigateTo("downloads");
  assert.equal(
    dialog.isOpen(),
    false,
    "a bottom-nav-style navigation must close an open sheet, not leave it painted over the new view",
  );
});

test("closing on navigation also returns focus to the sheet's invoker, same as an explicit close()", () => {
  const dialog = createSheetDialog({ ariaLabel: "Test sheet 2" });
  onViewChange(() => dialog.close());

  const invoker = dom.document.createElement("button");
  dom.document.activeElement = invoker;
  dialog.open();

  navigateTo("settings");
  assert.equal(dom.document.activeElement, invoker);
});

test("Escape closes the sheet regardless of the trap now in place (WP 4a.8)", () => {
  const dialog = createSheetDialog({ ariaLabel: "Test sheet 3" });
  dialog.open();
  assert.equal(dialog.isOpen(), true);

  dom.document.dispatchEvent(fakeKeyEvent("Escape"));
  assert.equal(dialog.isOpen(), false);
});

// ---------------------------------------------------------------------
// (2) Icon aria-hidden — the "avoid double announcement" pattern every
// caller that already shows the status WORD visibly next to the icon
// applies: `icon.setAttribute("aria-hidden", "true")` on the
// `createStatusIcon()` result (components/notifications.js,
// components/clients-sheet.js, views/downloads.js's history row, and
// components/game-card.js's meta icon / pill icon, added this WP).
// ---------------------------------------------------------------------
test("createStatusIcon's own sr-only label is present by default (no caller has opted out yet)", () => {
  const icon = createStatusIcon("cached");
  assert.equal(icon.hasAttribute("aria-hidden"), false);
  const label = icon.children.find((c) => c.className === "sr-only");
  assert.ok(label, "the sr-only label node must exist for a caller with nothing else to show");
  assert.equal(label.textContent, "Current");
});

test("a caller marking the icon aria-hidden hides it from assistive tech WITHOUT removing the label node", () => {
  const icon = createStatusIcon("cached");
  // The exact one-line pattern every real call site applies.
  icon.setAttribute("aria-hidden", "true");

  assert.equal(icon.getAttribute("aria-hidden"), "true");
  // Mutation target: "fix" double-announcement by deleting the label
  // instead of hiding the wrapper would break the OTHER callers that show
  // the icon with no adjacent visible word (e.g. a bare status-icon used
  // standalone) — the label must still exist, just be excluded from the
  // accessibility tree via the wrapper's aria-hidden.
  const label = icon.children.find((c) => c.className === "sr-only");
  assert.ok(label, "hiding via aria-hidden must not delete the underlying label content");
});
