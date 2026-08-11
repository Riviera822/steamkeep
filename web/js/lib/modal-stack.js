/**
 * Shared modal-overlay stack (WP 4a.8 — the focus trap deferred by
 * onboarding.js and sheet-dialog.js's own module headers: "a real focus
 * TRAP (Tab/Shift+Tab wrapping inside the sheet) and `inert`/`aria-hidden`
 * on the app shell behind it").
 *
 * Every top-level overlay this app shows — the three `createSheetDialog`
 * sheets (clients, notifications, game detail) and the bespoke delete/
 * GC-execute confirm dialogs (`views/library.js`,
 * `components/game-detail-sheet.js`) — is a DIRECT CHILD of `document.body`,
 * a sibling of `#app` (see each component's own
 * `document.body.appendChild(...)`). The native `inert` attribute (baseline
 * browser support, no polyfill) is what actually implements the trap: an
 * inert subtree is removed from the tab order, from click-through, AND —
 * per the HTML spec — from the accessibility tree, all with one attribute.
 * That is a MORE robust trap than hand-rolled Tab/Shift+Tab key
 * interception: it cannot be defeated by a focusable element a manual
 * enumeration missed, and it is the one mechanism that satisfies both
 * deferred items ("focus trap" AND "inert/aria-hidden background") at once
 * instead of two mechanisms that could drift apart. `aria-hidden` is set
 * alongside it anyway, for older assistive tech that predates `inert`'s
 * AT-hiding behaviour. (The onboarding overlay also pushes/pops itself here
 * for the `inert` half — see `onboarding.js` — but keeps its OWN
 * independent Escape handling; see "Escape" below for why that is safe.)
 *
 * **Why a stack, not a boolean/counter.** The detail sheet's own delete/GC
 * confirm dialogs open ON TOP of the already-open sheet — a second overlay,
 * not a replacement (`css/app.css`'s `.dialog-backdrop` is explicitly
 * z-index 45, "ABOVE .sheet-backdrop's 40" — WP 4a.4 finding). When that
 * happens the sheet itself must ALSO go inert: its Tab order must not leak
 * into a dialog that is now visually behind a THIRD layer. A stack derives
 * "only the topmost overlay (plus whatever is truly outside `#app`) stays
 * reachable" correctly for any nesting depth, and `popModal` restores the
 * new topmost to non-inert without the caller needing to know whether it
 * was "the" modal or one of several. `push`/`pop` are idempotent — pushing
 * an already-stacked element just moves it to the top (safe to call from a
 * render function on every tick while the same overlay stays open, as
 * `game-detail-sheet.js`'s GC-confirm wiring does); popping an element not
 * on the stack is a no-op.
 *
 * **Escape closes only the TOPMOST overlay — a bug found live during this
 * WP's e2e pass, not a design done up front.** Before this module existed,
 * `sheet-dialog.js` bound its OWN `document`-level `keydown` listener per
 * instance, and the WP 4a.8 confirm-dialog wiring added a second,
 * independent one for the delete/GC-execute confirms. With the detail sheet
 * open and its GC-execute confirm open ON TOP of it, a single Escape press
 * fired BOTH listeners — the confirm's `dismissGcExecuteConfirm()` AND the
 * sheet's own `close()` — because nothing stopped one dialog's keydown
 * handler from also reacting to a key meant for the dialog stacked above
 * it. Verified live (Browser pane): pressing Escape while the confirm was
 * open closed the confirm AND the whole detail sheet in one keystroke.
 * `pushModal`'s optional `onEscape` callback and the ONE centralized
 * `keydown` listener below fix this at the root: only the CURRENT topmost
 * entry's callback ever runs, so a lower dialog's Escape handling is
 * structurally unreachable while anything sits above it — the same "single
 * source of truth instead of two listeners that can race" reasoning
 * `job-partition.js`'s `jobStatusWord` already documents for a different
 * kind of duplication.
 *
 * DOM-only, no fetch, no store access. Not unit-tested against a real
 * `document.getElementById` (bare-Node posture, see web/tests/README.md);
 * `web/tests/modal-stack.test.js` covers the STACK ARITHMETIC (push/pop
 * order, idempotency, which element ends up inert, which callback Escape
 * invokes) against the fake DOM shared with the dialog-wiring regression
 * tests.
 */

const stack = []; // {el, onEscape}

function appRoot(doc) {
  return doc.getElementById("app");
}

function applyInert(doc) {
  const top = stack.length ? stack[stack.length - 1].el : null;
  const app = appRoot(doc);
  if (app) {
    if (stack.length) {
      app.setAttribute("inert", "");
      app.setAttribute("aria-hidden", "true");
    } else {
      app.removeAttribute("inert");
      app.removeAttribute("aria-hidden");
    }
  }
  for (const entry of stack) {
    if (entry.el === top) {
      entry.el.removeAttribute("inert");
      entry.el.removeAttribute("aria-hidden");
    } else {
      entry.el.setAttribute("inert", "");
      entry.el.setAttribute("aria-hidden", "true");
    }
  }
}

/** The `document` the single Escape listener below is currently bound to.
 * Tracked (rather than bound unconditionally once at module load) so the
 * fake-DOM regression tests can each get their own working listener —
 * production only ever passes the real global `document`, so this rebinds
 * exactly once there. */
let escapeBoundTo = null;

function onEscapeKeydown(event) {
  if (event.key !== "Escape" || !stack.length) return;
  const top = stack[stack.length - 1];
  if (!top.onEscape) return; // this overlay does not want Escape handled centrally (see onboarding.js)
  event.preventDefault();
  top.onEscape();
}

function ensureEscapeListener(doc) {
  if (escapeBoundTo === doc) return;
  if (escapeBoundTo) escapeBoundTo.removeEventListener("keydown", onEscapeKeydown);
  doc.addEventListener("keydown", onEscapeKeydown);
  escapeBoundTo = doc;
}

/**
 * Push `el` (a direct `document.body` child — a sheet backdrop or a confirm
 * backdrop) onto the modal stack and re-derive every tracked element's (and
 * `#app`'s) `inert`/`aria-hidden` state. While `el` is the topmost entry, an
 * `Escape` keypress calls `onEscape()` (if given) and nothing else on the
 * stack — see the module header's "Escape closes only the TOPMOST overlay".
 * @param {Element} el
 * @param {(() => void) | null} [onEscape]
 * @param {Document} [doc] injectable for the fake-DOM regression tests;
 *   defaults to the real global `document` in the browser.
 */
export function pushModal(el, onEscape = null, doc = document) {
  if (!el) return;
  ensureEscapeListener(doc);
  const i = stack.findIndex((entry) => entry.el === el);
  if (i !== -1) stack.splice(i, 1);
  stack.push({ el, onEscape });
  applyInert(doc);
}

/**
 * Remove `el` from the modal stack — wherever it is; overlays do not always
 * close in strict LIFO order — and re-derive `inert`/`aria-hidden` state.
 * A no-op if `el` was not on the stack.
 *
 * `el` itself is explicitly cleared before `applyInert` runs, not left to
 * that loop: `applyInert` only ever visits elements still IN `stack`, so an
 * out-of-order pop (removing a non-topmost entry while something above it
 * is still open — e.g. `closeDetail()` popping the sheet's backdrop after
 * an unrelated caller already popped its GC-execute confirm) would
 * otherwise leave `el`'s own `inert`/`aria-hidden` exactly as they were the
 * instant before removal. Harmless in EVERY reachable call path today (this
 * module's push/pop pairs all unwind LIFO in practice, and a re-pushed `el`
 * gets fresh, correct attributes from `applyInert` regardless) — but an
 * element popped and never pushed again would otherwise carry a stale
 * `inert` forever, so it is cleared unconditionally rather than relying on
 * that self-healing.
 * @param {Element} el
 * @param {Document} [doc]
 */
export function popModal(el, doc = document) {
  const i = stack.findIndex((entry) => entry.el === el);
  if (i === -1) return;
  stack.splice(i, 1);
  el.removeAttribute("inert");
  el.removeAttribute("aria-hidden");
  applyInert(doc);
}

/** Test/debug helper: how many overlays are currently stacked. Not used by
 * any production call site. */
export function modalDepth() {
  return stack.length;
}

/** Test-only: forcibly empty the stack between test cases (module state is
 * otherwise shared across every test file that imports this module). Also
 * re-points the Escape listener at `doc`, so a fresh fake DOM per test gets
 * a working listener rather than the previous test's now-orphaned one. */
export function resetModalStack(doc = document) {
  stack.length = 0;
  escapeBoundTo = null;
  ensureEscapeListener(doc);
  applyInert(doc);
}
