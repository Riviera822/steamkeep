/**
 * Header/hero art for the game detail card (WP 4h.3).
 *
 * `components/header-art.js` is DOM-building, so — per the general posture
 * `sheet-dialog.js`'s header documents for this whole family of components —
 * it is not unit-tested for its general shape. It gets ONE named exception,
 * the same kind `dialog-wiring.test.js` already carves out of that general
 * rule for `sheet-dialog.js`'s own modal-stack wiring: the two behaviors
 * this WP's brief demands a mutation-tested pin for.
 *
 *   1. The `<img>` src is built through `lib/cover-art.js`'s shipped
 *      `headerArtUrl(appid)` mechanism, never a hardcoded string
 *      (`cover-art.test.js` already pins `headerArtUrl` itself; this file
 *      additionally proves `buildHeaderArt` actually calls it, varying with
 *      the appid it is given).
 *   2. Graceful absence: on an image load failure the ENTIRE wrapper is
 *      removed — not just the `<img>` — so no reserved aspect-ratio band
 *      survives a 404. Contrast `game-card.js`'s `buildCover()`, where
 *      `img.remove()` alone is correct because a styled fallback tile stays
 *      underneath; this component has no such tile.
 *
 * Uses the shared `web/tests/fake-dom.js` harness (same one
 * `css-overlay-geometry.test.js`/`dialog-wiring.test.js`/`modal-stack.
 * test.js` use) — `header-art.js` only touches `document.createElement`
 * inside `buildHeaderArt()`, never at module load, so a fresh fake
 * `{document}` set as `globalThis.document` before importing is enough (same
 * reasoning `game-card.test.js`'s header gives for `game-card.js` itself).
 *
 * The CSS section below pins the no-layout-shift resolution this WP's brief
 * asks to be stated and pinned explicitly: `.header-art` reserves its box
 * via `aspect-ratio` ONLY (no `height`/`min-height` anywhere on it, which
 * would recreate a permanent band on the 404 case `buildHeaderArt` above
 * already handles by removing the wrapper outright).
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

test("buildHeaderArt: an <img> load failure removes the WHOLE wrapper, not just the <img> — no reserved band survives a 404 (mutation: drop the error handler -> dies)", async () => {
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
    "the reserved wrapper must be gone from its parent entirely after an image load failure — a guard that only hides/removes the <img> itself would leave this at 1",
  );
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
// 3. CSS: the no-layout-shift resolution, pinned structurally.
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

test(".header-art reserves its box via aspect-ratio (the no-layout-shift mechanism while loading-or-present)", () => {
  const body = ruleBody(strippedAppCss, ".header-art");
  assert.ok(body, ".header-art rule not found in app.css");
  assert.match(body, /aspect-ratio\s*:\s*460\s*\/\s*215/, ".header-art has no aspect-ratio:460/215 declaration");
});

test(".header-art does NOT reserve space with height/min-height — aspect-ratio must be the ONLY reservation mechanism, or the 404 case would still leave a permanent band once the wrapper's own removal is bypassed", () => {
  const body = ruleBody(strippedAppCss, ".header-art");
  assert.ok(body, ".header-art rule not found in app.css");
  assert.doesNotMatch(body, /\bheight\s*:/, ".header-art must not declare height (aspect-ratio must be the only reservation)");
  assert.doesNotMatch(body, /min-height\s*:/, ".header-art must not declare min-height");
});

test(".header-art has no transition/animation (standing 'no motion on overlays/cards' rule, D-13) — a finished load just appears", () => {
  const body = ruleBody(strippedAppCss, ".header-art");
  const imgBody = ruleBody(strippedAppCss, ".header-art img");
  assert.doesNotMatch(body || "", /transition|animation/);
  assert.doesNotMatch(imgBody || "", /transition|animation/);
});

test(".header-art img fills the reserved box (object-fit:cover) and is a block element (no inline-baseline gap)", () => {
  const body = ruleBody(strippedAppCss, ".header-art img");
  assert.ok(body, ".header-art img rule not found in app.css");
  assert.match(body, /display\s*:\s*block/);
  assert.match(body, /object-fit\s*:\s*cover/);
});
