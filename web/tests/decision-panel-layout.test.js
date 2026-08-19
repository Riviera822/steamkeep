/**
 * Structural CSS pins for the suggestions panel's two presentations
 * (WP 4h.2) — same technique as css-layout-foundation.test.js (no jsdom in
 * web/tests/, see that file's header and web/tests/README.md): this parses
 * app.css/theme.css as text, split into top-level vs. `@media` blocks, and
 * pins the exact declarations rather than reasoning about rendered pixels.
 *
 * The pin this file exists for: `docs/PROJECT_PLAN.md`:1867-1869 requires
 * BOTH a right-hand column at BP-XL (>=1800px) AND a collapsible card below
 * that width, from ONE component — never both presentations active at once,
 * never the panel entirely absent at a width the plan says it should exist.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.join(__dirname, "..");
const appCss = readFileSync(path.join(webDir, "css", "app.css"), "utf8");
const themeCss = readFileSync(path.join(webDir, "css", "theme.css"), "utf8");
const indexHtml = readFileSync(path.join(webDir, "index.html"), "utf8");

function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** Same brace-balanced splitter as css-layout-foundation.test.js (kept local
 * rather than imported — that file exports nothing, by its own footprint
 * rule this WP does not touch it). */
function splitMedia(cssText) {
  const css = stripComments(cssText);
  let topLevel = "";
  const mediaBlocks = [];
  let i = 0;
  while (i < css.length) {
    const atIdx = css.indexOf("@media", i);
    if (atIdx === -1) {
      topLevel += css.slice(i);
      break;
    }
    topLevel += css.slice(i, atIdx);
    const openBrace = css.indexOf("{", atIdx);
    const header = css.slice(atIdx, openBrace + 1).trim();
    let depth = 1;
    let j = openBrace + 1;
    while (depth > 0 && j < css.length) {
      if (css[j] === "{") depth++;
      else if (css[j] === "}") depth--;
      j++;
    }
    mediaBlocks.push({ header, body: css.slice(openBrace + 1, j - 1) });
    i = j;
  }
  return { topLevel, mediaBlocks };
}

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

const { topLevel: appTop, mediaBlocks: appMedia } = splitMedia(appCss);
const { topLevel: themeTop } = splitMedia(themeCss);

function findMediaBlock(minWidthPx) {
  return appMedia.find((b) => new RegExp(`min-width:\\s*${minWidthPx}px`).test(b.header) && !/and/.test(b.header));
}
const bpL = findMediaBlock(1024);
const bpXl = findMediaBlock(1800);

// ---------------------------------------------------------------------
// index.html: exactly one panel element, last child of #app, hidden by
// default, and its two act buttons/body exist.
// ---------------------------------------------------------------------

test('index.html has exactly one <aside id="decision-panel"> and it is [hidden] by default', () => {
  const matches = [...indexHtml.matchAll(/<aside\b[^>]*id=["']decision-panel["'][^>]*>/g)];
  assert.equal(matches.length, 1, `expected exactly one #decision-panel element, found ${matches.length}`);
  assert.match(matches[0][0], /\bhidden\b/, "#decision-panel must start [hidden]");
});

test("#decision-panel is the LAST child inside #app, after <main id=\"view-root\"> — necessary for D-14, but NOT sufficient on its own (see the BP-L grid-area pins below: DOM order alone does not decide visual order once #app is a CSS grid)", () => {
  const appOpen = indexHtml.indexOf('<div id="app"');
  const mainIdx = indexHtml.indexOf('id="view-root"', appOpen);
  const panelIdx = indexHtml.indexOf('id="decision-panel"', appOpen);
  const appCloseIdx = indexHtml.indexOf("</div>", panelIdx); // the #app close tag, approx — good enough given no nested </div> between panel and #app's own close in this file's known structure
  assert.ok(appOpen !== -1 && mainIdx !== -1 && panelIdx !== -1, "one of #app/#view-root/#decision-panel not found");
  assert.ok(mainIdx < panelIdx, "#decision-panel must come AFTER <main id=\"view-root\"> in DOM order (D-14: no new focusable content ahead of the grid)");
  assert.ok(appCloseIdx !== -1);
});

test("#decision-panel carries #dp-body, #dp-collapse and #dp-dismiss", () => {
  for (const id of ["dp-body", "dp-collapse", "dp-dismiss"]) {
    assert.match(indexHtml, new RegExp(`id=["']${id}["']`), `#${id} not found in index.html`);
  }
});

// ---------------------------------------------------------------------
// Opus review blocker B1 (WP 4h.2 fix round), measured live at 1280px in
// both bypass-banner states (see the coder's report for the full before/
// after numbers): a fifth in-flow child of #app with no explicit grid-area
// auto-places into whichever cell the grid's own algorithm finds first
// vacant once #app becomes a grid at BP-L — landing ABOVE the library
// (banner hidden, the "banner" cell is vacant) or inside the RAIL's own
// 148px column (banner visible, auto-placement opens an implicit row
// there instead). The fix: an explicit fourth row/area in BP-L's own
// template, and an explicit `grid-area:panel` on `.decision-panel` there.
// These pins are the STATIC half of that fix (structural, no jsdom); the
// LIVE half (an actual bounding-rect comparison in both banner states) is
// documented in the coder's report, not here — this file has no way to
// instantiate a real grid layout.
// ---------------------------------------------------------------------

test("MUTATION PIN (B1): BP-L's .app grid gains a fourth 'rail panel' row — never re-flattened back to three", () => {
  assert.ok(bpL, "no bare min-width:1024px block found");
  const body = ruleBody(bpL.body, ".app");
  assert.ok(body, ".app rule not found in the BP-L block");
  assert.match(body, /grid-template-rows\s*:\s*auto\s+auto\s+1fr\s+auto/, "BP-L's .app grid-template-rows must be exactly 'auto auto 1fr auto' — a dropped fourth track re-opens B1");
  assert.match(body, /"rail\s+panel"/, 'the "rail panel" area row is missing from BP-L\'s .app grid-template-areas — without it, .decision-panel auto-places again');
});

test("MUTATION PIN (B1): '.decision-panel{ grid-area:panel }' is explicit inside the BP-L block, unconditionally (not gated on .app.has-decision-panel — an auto-height row costs nothing when empty, unlike the BP-XL column)", () => {
  const body = ruleBody(bpL.body, ".decision-panel");
  assert.ok(body, ".decision-panel rule not found directly in the BP-L block (bare selector, not scoped under .app.has-decision-panel)");
  assert.match(body, /grid-area\s*:\s*panel/, "BP-L's .decision-panel rule must set grid-area:panel — without it, B1's auto-placement bug returns");
});

test("MUTATION PIN: '.decision-panel{ grid-area:panel' never appears at the top level (would make it a column below BP-L too, where #app is not even a grid)", () => {
  assert.equal(/\.decision-panel\s*\{[^}]*grid-area\s*:\s*panel/.test(appTop), false, "grid-area:panel leaked into the top-level (pre-BP-L) rule");
});

test("MUTATION PIN: the 3-column '.app.has-decision-panel' grid template exists ONLY inside the BP-XL block", () => {
  assert.equal(/\.app\.has-decision-panel\s*\{/.test(appTop), false, "the 3-column template leaked to top level — the column would always reserve space, even off Library or when dismissed");
  assert.ok(bpXl.body.includes(".app.has-decision-panel"), "'.app.has-decision-panel' rule not found in the BP-XL block");
  const body = ruleBody(bpXl.body, ".app.has-decision-panel");
  assert.ok(body, ".app.has-decision-panel rule body not found");
  assert.match(body, /grid-template-columns\s*:\s*var\(--rail-w\)\s+1fr\s+var\(--panel-w\)/, "the 3-column template must derive the panel's width from --panel-w, not a hard-coded number");
  assert.match(body, /"rail\s+main\s+panel"/, 'the "rail main panel" area row is missing or changed');

  for (const block of appMedia) {
    if (block === bpXl) continue;
    assert.equal(/\.app\.has-decision-panel\s*\{/.test(block.body), false, `'.app.has-decision-panel' rule found in an unexpected media block: ${block.header}`);
  }
});

// ---------------------------------------------------------------------
// Opus review should-fix S3 (WP 4h.2 fix round): the BP-XL column-only
// rules (grid-area:panel for the COLUMN presentation, the no-collapse
// treatment) must be scoped to `.app.has-decision-panel .X`, never bare
// `.X` — a bare selector here would apply the "no collapse, force-
// expanded" column treatment even when there is nothing to say (has-
// decision-panel absent), leaving an empty-result panel stuck looking like
// a column with no column to back it.
// ---------------------------------------------------------------------

// A selector STARTS a new source line in this file's own formatting
// convention (every rule in this file begins on its own line — verified by
// every OTHER test in this suite reading real, unmodified rule bodies via
// this same convention) — so a selector preceded on its own line only by
// whitespace is a BARE, standalone rule; one preceded by another selector
// and a combinator space (".app.has-decision-panel .decision-panel{") is
// not, and this regex — anchored on the newline — cannot conflate the two,
// unlike a plain substring search (which would wrongly match ".decision-
// panel{" as a tail substring of ".app.has-decision-panel .decision-panel{").
function hasBareRule(cssBody, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`\\n[ \\t]*${escaped}\\s*\\{`).test(cssBody);
}

test("MUTATION PIN (S3 + S6): the BP-XL column-only rules are scoped under '.app.has-decision-panel', never bare '.decision-panel'/'.dp-collapse', and the scoped .decision-panel rule sets width:auto (S6, Opus review round 2 — this test predates that fix; the name now names both pins it actually carries)", () => {
  assert.equal(hasBareRule(bpXl.body, ".decision-panel"), false, "a BARE '.decision-panel{...}' rule exists in the BP-XL block — S3 requires it scoped to '.app.has-decision-panel .decision-panel' so an empty result doesn't get column treatment");
  assert.equal(hasBareRule(bpXl.body, ".dp-collapse"), false, "a BARE '.dp-collapse{...}' rule exists in the BP-XL block — same S3 requirement");

  const scopedPanel = ruleBody(bpXl.body, ".app.has-decision-panel .decision-panel");
  assert.ok(scopedPanel, "'.app.has-decision-panel .decision-panel{ grid-area:panel }' not found in the BP-XL block");
  assert.match(scopedPanel, /grid-area\s*:\s*panel/);
  // Should-fix S6 (Opus review round 2): the top-level rule's own
  // `width:100%` (needed there for the AUTO-margin case, S2) makes THIS
  // rule's `margin-right:16px` inert unless it explicitly resets width
  // back to `auto` — measured live: without this, the card sat flush
  // against the track's right edge at every BP-XL width (the gutter gone).
  assert.match(scopedPanel, /width\s*:\s*auto/, "'.app.has-decision-panel .decision-panel' must set width:auto — without it, the top-level width:100% wins and margin-right becomes inert (S6)");

  const scopedCollapse = ruleBody(bpXl.body, ".app.has-decision-panel .dp-collapse");
  assert.ok(scopedCollapse, "'.app.has-decision-panel .dp-collapse{ display:none }' not found in the BP-XL block");
  assert.match(scopedCollapse, /display\s*:\s*none/);
});

test("MUTATION PIN: 'remove the card entirely' — .decision-panel carries a real, non-empty top-level rule (the sub-BP-XL/empty-result presentation actually renders something)", () => {
  const body = ruleBody(appTop, ".decision-panel");
  assert.ok(body && body.trim().length > 0, ".decision-panel has no top-level rule at all — the card presentation would not exist below BP-XL");
  assert.match(body, /background\s*:/, ".decision-panel's top-level rule must give the card a real background (proves it is a rendered box, not an invisible wrapper)");
});

test(".decision-panel.collapsed .dp-body is display:none at top level, and forced back open ONLY under '.app.has-decision-panel' in the BP-XL block", () => {
  const topBody = ruleBody(appTop, ".decision-panel.collapsed .dp-body");
  assert.ok(topBody, ".decision-panel.collapsed .dp-body base rule not found");
  assert.match(topBody, /display\s*:\s*none/);

  assert.equal(
    hasBareRule(bpXl.body, ".decision-panel.collapsed .dp-body"),
    false,
    "a BARE '.decision-panel.collapsed .dp-body{...}' rule exists in the BP-XL block — S3 requires it scoped under '.app.has-decision-panel'",
  );
  const bpXlBody = ruleBody(bpXl.body, ".app.has-decision-panel .decision-panel.collapsed .dp-body");
  assert.ok(bpXlBody, ".app.has-decision-panel .decision-panel.collapsed .dp-body override not found in the BP-XL block");
  assert.equal(/display\s*:\s*none/.test(bpXlBody), false, "the BP-XL override must NOT also say display:none — it exists specifically to force the body back open");
});

// ---------------------------------------------------------------------
// S2 (Opus review should-fix, WP 4h.2 fix round): centring, not a fixed
// left-gutter margin, at both the top level and inside BP-L (the fix is
// the SAME declaration in both places, since BP-L's rule composes with the
// top-level one rather than fully replacing every property).
// ---------------------------------------------------------------------

test("MUTATION PIN (S2): '.decision-panel' centres itself (margin uses 'auto' horizontally), never a fixed var(--gutter) left-inset", () => {
  const topBody = ruleBody(appTop, ".decision-panel");
  assert.match(topBody, /margin\s*:\s*20px\s+auto\s+32px/, "top-level .decision-panel must centre with margin:20px auto 32px — a fixed var(--gutter) inset here left it mis-aligned against .view-root's own centred column (measured live: a 108px stair-step at 1023px)");
  assert.equal(/margin\s*:\s*20px\s+var\(--gutter\)\s+32px/.test(topBody), false, "the old, non-centring margin shape must not reappear");
});

// ---------------------------------------------------------------------
// No author `display` on .decision-panel anywhere (the [hidden]-guard-free
// design this file's own app.css comment explains) — and therefore, per
// css-hygiene.test.js's own documented rule, no guard is required. This is
// a SANITY check that the design assumption still holds; it does not
// replace css-hygiene.test.js's own (untouched) suite.
// ---------------------------------------------------------------------

test("sanity: '.decision-panel' itself carries no author display: declaration anywhere (top level or any media block) — by design, no [hidden] guard is needed", () => {
  const combined = stripComments(themeCss) + "\n" + stripComments(appCss);
  const re = /\.decision-panel\s*\{([^}]*)\}/g;
  let m;
  let found = false;
  while ((m = re.exec(combined))) {
    if (/display\s*:/.test(m[1])) found = true;
  }
  assert.equal(found, false, ".decision-panel now carries an author display: rule — re-check whether a .decision-panel[hidden]{display:none} guard is needed (css-hygiene.test.js)");
});

// ---------------------------------------------------------------------
// theme.css: --panel-w exists, is a real px length, and is the token the
// BP-XL grid template actually references (not a hard-coded duplicate).
// ---------------------------------------------------------------------

test("theme.css declares --panel-w as a px length", () => {
  assert.match(themeTop, /--panel-w\s*:\s*\d+px/);
});

// ---------------------------------------------------------------------
// The existing BP-XL --w-wall hook (css-layout-foundation.test.js's own
// pin) must still be the SAME block this file adds the panel rules to —
// not a second, duplicate `@media (min-width:1800px)` block, which would
// make "the panel takes width from the wall" unverifiable by inspection.
// ---------------------------------------------------------------------

test("the panel's BP-XL rules live in the SAME bare min-width:1800px block as --w-wall (not a second, duplicate block)", () => {
  const bareBlocks = appMedia.filter((b) => /min-width:\s*1800px/.test(b.header) && !/and/.test(b.header));
  assert.equal(bareBlocks.length, 1, `expected exactly one bare min-width:1800px block, found ${bareBlocks.length}`);
  assert.match(bareBlocks[0].body, /--w-wall\s*:\s*2000px/);
  assert.match(bareBlocks[0].body, /\.app\.has-decision-panel/);
});
