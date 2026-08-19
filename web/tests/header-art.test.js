/**
 * Header/hero art for the game detail card (WP 4h.3, load-then-reveal
 * design superseded to this shape by WP 4h.5 — docs/WORKPACKAGES.md D-17).
 *
 * `components/header-art.js` is DOM-building, so — per the general posture
 * `sheet-dialog.js`'s header documents for this whole family of components —
 * it is not unit-tested for its general shape. It gets ONE named exception,
 * the same kind `dialog-wiring.test.js` already carves out of that general
 * rule for `sheet-dialog.js`'s own modal-stack wiring: the behaviors this
 * WP's brief (and WP 4h.3's before it) demand a mutation-tested pin for.
 *
 *   1. The `<img>` src is built through `lib/cover-art.js`'s shipped
 *      `headerArtUrl(appid)` mechanism, never a hardcoded string
 *      (`cover-art.test.js` already pins `headerArtUrl` itself; this file
 *      additionally proves `buildHeaderArt` actually calls it, varying with
 *      the appid it is given).
 *   2. Graceful absence: on an image load failure the ENTIRE wrapper is
 *      removed — not just the `<img>` — so no reserved band survives a 404.
 *      Contrast `game-card.js`'s `buildCover()`, where `img.remove()` alone
 *      is correct because a styled fallback tile stays underneath; this
 *      component has no such tile. Under WP 4h.5's load-then-reveal design
 *      this matters even more directly than it did under WP 4h.3: nothing
 *      was ever reserved before removal, so there is nothing left to prove
 *      about a "collapse" — there was never anything to collapse.
 *   3. New in WP 4h.5: the wrapper starts WITHOUT the `.loaded` class (zero
 *      reserved size, per the CSS section below) and gains it only once the
 *      image actually signals ready — proving the reveal never fires early.
 *
 * WP 4h.5 also changes the fake-DOM harness's relevance for the reveal path
 * itself: `buildHeaderArt` prefers `img.decode()` when available and falls
 * back to the `load` event otherwise. `fake-dom.js`'s `FakeElement` has no
 * `decode` method, so every test below exercises the fallback `load`-event
 * path — this is deliberate and is what a real browser lacking `decode()`
 * support would also do; the decode() branch itself is plain use of a
 * standard Promise-returning DOM API with no project-specific logic beyond
 * "call reveal on success, swallow the rejection" (the module's own header
 * explains why), so it is not separately re-implemented in this harness.
 *
 * Uses the shared `web/tests/fake-dom.js` harness (same one
 * `css-overlay-geometry.test.js`/`dialog-wiring.test.js`/`modal-stack.
 * test.js` use) — `header-art.js` only touches `document.createElement`
 * inside `buildHeaderArt()`, never at module load, so a fresh fake
 * `{document}` set as `globalThis.document` before importing is enough (same
 * reasoning `game-card.test.js`'s header gives for `game-card.js` itself).
 *
 * The CSS section below pins the load-then-reveal resolution this WP's
 * brief asks to be stated and pinned explicitly: `.header-art` reserves
 * NOTHING in its base state (no `aspect-ratio`/`height`/`min-height`, a
 * `grid-template-rows:0fr` track), `.header-art.loaded` reveals the true
 * 460/215 box via `grid-template-rows:1fr`, the `<img>` itself carries the
 * real `aspect-ratio:460/215`, and the reveal transition carries no
 * `!important` (so the standing whole-app `prefers-reduced-motion`
 * wildcard, pinned separately in `keyboard-pointer-model.test.js`, governs
 * it automatically).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { createFakeDom } from "./fake-dom.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.join(__dirname, "..");

// ---------------------------------------------------------------------
// 1. Behavioural pins (fake DOM) — buildHeaderArt itself.
// ---------------------------------------------------------------------

function findChild(el, tagName) {
  return el.children.find((c) => c.tagName === tagName) || null;
}

test("buildHeaderArt: the <img> src is built via the shipped headerArtUrl(appid) mechanism, and varies with the appid (mutation: hardcode a URL -> dies)", async () => {
  const dom = createFakeDom();
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const { buildHeaderArt } = await import("../js/components/header-art.js");
  const { headerArtUrl } = await import("../js/lib/cover-art.js");

  const wrapA = buildHeaderArt(440);
  const imgA = findChild(wrapA, "IMG");
  assert.ok(imgA, "buildHeaderArt(440) produced no <img>");
  assert.equal(imgA.src, headerArtUrl(440));

  const wrapB = buildHeaderArt(2010010);
  const imgB = findChild(wrapB, "IMG");
  assert.equal(imgB.src, headerArtUrl(2010010));

  // The load-bearing mutation-kill: a hardcoded (or appid-440-only) URL
  // would make imgB.src equal imgA.src instead of tracking its own appid.
  assert.notEqual(imgA.src, imgB.src, "the <img> src must be a function of the appid passed to buildHeaderArt, not a fixed/hardcoded URL");
});

test("buildHeaderArt: an <img> load failure removes the WHOLE wrapper, not just the <img> — no band ever appeared to collapse (mutation: drop the error handler -> dies)", async () => {
  const dom = createFakeDom();
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const { buildHeaderArt } = await import("../js/components/header-art.js");

  const container = dom.document.createElement("div");
  const wrap = buildHeaderArt(440);
  container.appendChild(wrap);
  assert.equal(container.children.length, 1, "sanity: the wrapper was appended");

  const img = findChild(wrap, "IMG");
  img.dispatchEvent({ type: "error" });

  assert.equal(
    container.children.length,
    0,
    "the wrapper must be gone from its parent entirely after an image load failure — a guard that only hides/removes the <img> itself would leave this at 1",
  );
});

test("buildHeaderArt: the wrapper starts WITHOUT the .loaded class — nothing is revealed before the image is known-good (mutation: add .loaded eagerly -> dies)", async () => {
  const dom = createFakeDom();
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const { buildHeaderArt } = await import("../js/components/header-art.js");

  const wrap = buildHeaderArt(440);
  assert.equal(wrap.classList.contains("loaded"), false, "buildHeaderArt must not add .loaded synchronously — reveal only happens once the image signals ready");
});

test("buildHeaderArt: a successful load reveals the wrapper by adding .loaded (mutation: drop the reveal listener -> dies)", async () => {
  const dom = createFakeDom();
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const { buildHeaderArt } = await import("../js/components/header-art.js");

  const wrap = buildHeaderArt(440);
  const img = findChild(wrap, "IMG");
  // fake-dom's FakeElement has no `decode()` method, so buildHeaderArt's
  // fallback path is exercised here: reveal is wired to the "load" event.
  assert.equal(typeof img.decode, "undefined", "sanity: this harness has no decode() — the load-event fallback is what's under test");

  img.dispatchEvent({ type: "load" });

  assert.equal(wrap.classList.contains("loaded"), true, "a successful load must add .loaded to the wrapper");
});

test("buildHeaderArt: the <img> is decorative (empty alt) — same discipline as the grid cover/mini-cover", async () => {
  const dom = createFakeDom();
  globalThis.document = dom.document;
  globalThis.window = dom.window;
  const { buildHeaderArt } = await import("../js/components/header-art.js");
  const img = findChild(buildHeaderArt(440), "IMG");
  assert.equal(img.alt, "");
});

// ---------------------------------------------------------------------
// 2. Production wiring — game-detail-sheet.js actually calls buildHeaderArt
// (the WP 4a.1/4a.3 failure class: a mechanism with zero real callers).
// ---------------------------------------------------------------------

test("game-detail-sheet.js imports header-art.js and calls buildHeaderArt(state.appid) inside render()", () => {
  const detailJs = readFileSync(path.join(webDir, "js", "components", "game-detail-sheet.js"), "utf8");
  assert.match(
    detailJs,
    /import\s*\{\s*buildHeaderArt\s*\}\s*from\s*["']\.\/header-art\.js["']/,
    "game-detail-sheet.js no longer imports buildHeaderArt from ./header-art.js",
  );
  assert.match(
    detailJs,
    /contentEl\.append\(\s*buildHeaderArt\(state\.appid\)\s*\)/,
    "game-detail-sheet.js no longer appends buildHeaderArt(state.appid) into contentEl — the header art has no real caller",
  );
});

// ---------------------------------------------------------------------
// 3. CSS: the load-then-reveal resolution, pinned structurally.
// ---------------------------------------------------------------------

const appCssPath = path.join(webDir, "css", "app.css");
const appCss = readFileSync(appCssPath, "utf8");

function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** Same brace-balanced single-rule extractor css-overlay-geometry.test.js
 * uses — duplicated locally per this codebase's own convention (neither
 * file exports test helpers; each CSS test file carries its own small
 * parsing utilities, see that file's header). */
function ruleBody(text, selector) {
  const idx = text.indexOf(selector + "{");
  if (idx === -1) return null;
  const openBrace = idx + selector.length;
  let depth = 1;
  let j = openBrace + 1;
  while (depth > 0 && j < text.length) {
    if (text[j] === "{") depth++;
    else if (text[j] === "}") depth--;
    j++;
  }
  return text.slice(openBrace + 1, j - 1);
}

const strippedAppCss = stripComments(appCss);

test(".header-art reserves NOTHING in its base (unloaded) state — no aspect-ratio, no height/min-height (mutation: reserve aspect-ratio on the base rule -> dies)", () => {
  const body = ruleBody(strippedAppCss, ".header-art");
  assert.ok(body, ".header-art rule not found in app.css");
  assert.doesNotMatch(body, /aspect-ratio\s*:/, ".header-art's BASE rule must not reserve space via aspect-ratio — that is the whole point of WP 4h.5's load-then-reveal design (WP 4h.3's premature reservation is exactly what was superseded)");
  assert.doesNotMatch(body, /\bheight\s*:/, ".header-art must not declare height on its base rule");
  assert.doesNotMatch(body, /min-height\s*:/, ".header-art must not declare min-height on its base rule");
});

test(".header-art's base rule starts its grid row at 0fr — genuinely zero height, not a 1px seam (mutation: start at a nonzero fr/px value -> dies)", () => {
  const body = ruleBody(strippedAppCss, ".header-art");
  assert.ok(body, ".header-art rule not found in app.css");
  assert.match(body, /grid-template-rows\s*:\s*0fr\b/, ".header-art must start its row track at 0fr");
});

test(".header-art.loaded reveals the box via grid-template-rows:1fr and opacity:1 (mutation: reveal to any other row value -> dies)", () => {
  const body = ruleBody(strippedAppCss, ".header-art.loaded");
  assert.ok(body, ".header-art.loaded rule not found in app.css");
  assert.match(body, /grid-template-rows\s*:\s*1fr\b/, ".header-art.loaded must reveal via grid-template-rows:1fr");
  assert.match(body, /opacity\s*:\s*1\b/, ".header-art.loaded must reveal via opacity:1");
});

test(".header-art img carries the true 460/215 aspect-ratio — the expanded box's real shape (mutation: wrong ratio -> dies)", () => {
  const body = ruleBody(strippedAppCss, ".header-art img");
  assert.ok(body, ".header-art img rule not found in app.css");
  assert.match(body, /aspect-ratio\s*:\s*460\s*\/\s*215/, ".header-art img has no aspect-ratio:460/215 declaration");
});

test(".header-art declares its own reveal transition with NO !important, so the standing whole-app reduced-motion wildcard governs it (mutation: add !important -> dies)", () => {
  const body = ruleBody(strippedAppCss, ".header-art");
  assert.ok(body, ".header-art rule not found in app.css");
  assert.match(body, /transition\s*:/, ".header-art must declare a transition — the grow-in this WP's brief asks for");
  assert.doesNotMatch(body, /!important/, ".header-art's own transition must carry no !important — the reduced-motion wildcard's !important is what must win, unconditionally");
});

test(".header-art img fills the expanded box (object-fit:cover), is a block element (no inline-baseline gap), and can shrink to 0 with its parent (min-height:0)", () => {
  const body = ruleBody(strippedAppCss, ".header-art img");
  assert.ok(body, ".header-art img rule not found in app.css");
  assert.match(body, /display\s*:\s*block/);
  assert.match(body, /object-fit\s*:\s*cover/);
  assert.match(body, /min-height\s*:\s*0\b/, ".header-art img needs min-height:0 or the grid row could never actually reach zero height");
});
