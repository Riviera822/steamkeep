/**
 * Headless tests for web/js/lib/modal-stack.js (WP 4a.8 — the deferred focus
 * trap: `inert`/`aria-hidden` on `#app` correctly composed across nested
 * overlays, AND the centralized "Escape closes only the topmost overlay"
 * dispatcher — see that module's header for the live-found bug this fixes:
 * before it existed, two independently-bound `document` keydown listeners
 * (the sheet's own, and a confirm dialog stacked on top of it) both reacted
 * to the same Escape press and closed both surfaces at once.
 *
 * Uses the shared fake DOM (`fake-dom.js`) rather than a real browser: this
 * module only ever calls `getElementById`/`setAttribute`/`removeAttribute`/
 * `addEventListener`/`removeEventListener`/`dispatchEvent`, all of which the
 * fake `FakeElement`/document support (see that file's header for the "why
 * not jsdom" reasoning).
 *
 * `resetModalStack()` runs before every test because the module holds its
 * stack at module scope — shared across every test IN THIS FILE, same
 * "module-level state that must be reset between cases" posture
 * `demo-data.test.js`'s `resetDemoData()` documents. It also re-points the
 * module's single Escape listener at THIS test's fake document, since a
 * fresh `createFakeDom()` per test is otherwise a different object than
 * whatever the module bound its listener to last.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom, fakeKeyEvent } from "./fake-dom.js";
import { pushModal, popModal, modalDepth, resetModalStack } from "../js/lib/modal-stack.js";

let dom;
beforeEach(() => {
  dom = createFakeDom();
  resetModalStack(dom.document);
});

test("pushing one overlay marks #app inert + aria-hidden, and the overlay itself is not", () => {
  const overlay = dom.document.createElement("div");
  pushModal(overlay, null, dom.document);

  assert.equal(dom.appRoot.getAttribute("inert"), "");
  assert.equal(dom.appRoot.getAttribute("aria-hidden"), "true");
  assert.equal(overlay.hasAttribute("inert"), false);
  assert.equal(overlay.hasAttribute("aria-hidden"), false);
  assert.equal(modalDepth(), 1);
});

test("popping the only overlay restores #app to non-inert", () => {
  const overlay = dom.document.createElement("div");
  pushModal(overlay, null, dom.document);
  popModal(overlay, dom.document);

  assert.equal(dom.appRoot.hasAttribute("inert"), false);
  assert.equal(dom.appRoot.hasAttribute("aria-hidden"), false);
  assert.equal(modalDepth(), 0);
});

// ---------------------------------------------------------------------
// The nested case this module exists for (module header, "why a stack, not
// a boolean/counter"): the detail sheet's own delete/GC confirm dialogs
// open ON TOP of the already-open sheet.
// ---------------------------------------------------------------------
test("nesting a second overlay makes the FIRST one inert too, leaving only the topmost reachable", () => {
  const sheet = dom.document.createElement("div");
  const confirm = dom.document.createElement("div");
  pushModal(sheet, null, dom.document);
  pushModal(confirm, null, dom.document);

  assert.equal(dom.appRoot.getAttribute("inert"), "");
  // Mutation target: a plain counter (no stack) cannot express this —
  // flipping this module to "just count, only touch #app" would leave the
  // sheet reachable behind the confirm dialog.
  assert.equal(sheet.getAttribute("inert"), "");
  assert.equal(sheet.getAttribute("aria-hidden"), "true");
  assert.equal(confirm.hasAttribute("inert"), false);
  assert.equal(modalDepth(), 2);
});

test("popping the topmost overlay restores the one beneath it, not #app", () => {
  const sheet = dom.document.createElement("div");
  const confirm = dom.document.createElement("div");
  pushModal(sheet, null, dom.document);
  pushModal(confirm, null, dom.document);
  popModal(confirm, dom.document);

  assert.equal(sheet.hasAttribute("inert"), false);
  assert.equal(sheet.hasAttribute("aria-hidden"), false);
  // #app stays inert — the sheet (still open) is the new topmost, and the
  // app shell behind it must stay unreachable.
  assert.equal(dom.appRoot.getAttribute("inert"), "");
  assert.equal(modalDepth(), 1);
});

test("popping out of LIFO order (the bottom element, while the top is still open) still recomputes correctly", () => {
  const sheet = dom.document.createElement("div");
  const confirm = dom.document.createElement("div");
  pushModal(sheet, null, dom.document);
  pushModal(confirm, null, dom.document);
  popModal(sheet, dom.document); // out of order — see module header, "overlays do not always close in strict LIFO order"

  assert.equal(modalDepth(), 1);
  assert.equal(confirm.hasAttribute("inert"), false); // still the (only, now) topmost
  assert.equal(dom.appRoot.getAttribute("inert"), "");
});

test("popModal clears the POPPED element's own inert/aria-hidden even when it was not the topmost", () => {
  const sheet = dom.document.createElement("div");
  const confirm = dom.document.createElement("div");
  pushModal(sheet, null, dom.document);
  pushModal(confirm, null, dom.document);
  // `sheet` is inert here (module header, "why a stack") — pop it out of
  // order and confirm it does not carry that attribute away with it.
  assert.equal(sheet.getAttribute("inert"), "");
  popModal(sheet, dom.document);
  assert.equal(sheet.hasAttribute("inert"), false);
  assert.equal(sheet.hasAttribute("aria-hidden"), false);
});

test("pushing an already-stacked element moves it to the top instead of duplicating it", () => {
  const overlay = dom.document.createElement("div");
  pushModal(overlay, null, dom.document);
  pushModal(overlay, null, dom.document);
  assert.equal(modalDepth(), 1);
});

test("popping an element that was never pushed is a safe no-op", () => {
  const overlay = dom.document.createElement("div");
  popModal(overlay, dom.document);
  assert.equal(modalDepth(), 0);
  assert.equal(dom.appRoot.hasAttribute("inert"), false);
});

// ---------------------------------------------------------------------
// The centralized Escape dispatcher — the live-found bug's regression pin.
// ---------------------------------------------------------------------
test("Escape calls the topmost overlay's onEscape and nothing else", () => {
  const sheet = dom.document.createElement("div");
  const confirm = dom.document.createElement("div");
  let sheetClosed = 0;
  let confirmClosed = 0;
  pushModal(sheet, () => sheetClosed++, dom.document);
  pushModal(confirm, () => confirmClosed++, dom.document);

  dom.document.dispatchEvent(fakeKeyEvent("Escape"));

  // MUTATION TARGET (the exact live-found bug): if this dispatcher called
  // every stacked onEscape instead of only the topmost's, sheetClosed would
  // also be 1 here — that is precisely "Escape closed both the confirm AND
  // the whole detail sheet in one keystroke", reproduced and pinned.
  assert.equal(confirmClosed, 1);
  assert.equal(sheetClosed, 0);
});

test("popping the topmost overlay makes Escape reach the one beneath it", () => {
  const sheet = dom.document.createElement("div");
  const confirm = dom.document.createElement("div");
  let sheetClosed = 0;
  pushModal(sheet, () => sheetClosed++, dom.document);
  pushModal(confirm, () => {}, dom.document);
  popModal(confirm, dom.document);

  dom.document.dispatchEvent(fakeKeyEvent("Escape"));
  assert.equal(sheetClosed, 1);
});

test("a non-Escape key never calls onEscape", () => {
  const overlay = dom.document.createElement("div");
  let closed = 0;
  pushModal(overlay, () => closed++, dom.document);

  dom.document.dispatchEvent(fakeKeyEvent("Enter"));
  assert.equal(closed, 0);
});

test("an overlay pushed with no onEscape (onboarding's own independent handling) is a safe no-op for the centralized dispatcher", () => {
  const overlay = dom.document.createElement("div");
  pushModal(overlay, null, dom.document);
  // Must not throw, and nothing else on an empty-below stack to call either.
  dom.document.dispatchEvent(fakeKeyEvent("Escape"));
  assert.equal(modalDepth(), 1); // unchanged — no onEscape means Escape does nothing here
});

test("Escape with nothing on the stack is a safe no-op", () => {
  dom.document.dispatchEvent(fakeKeyEvent("Escape"));
  assert.equal(modalDepth(), 0);
});
