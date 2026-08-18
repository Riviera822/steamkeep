/**
 * Desktop layout foundation (WP 4e.1) — structural CSS pins.
 *
 * No jsdom/browser in web/tests/ (see web/tests/README.md), so this cannot
 * measure real computed layout at a given viewport width (that is done live,
 * against a running vault-api instance in a real browser — see the coder's
 * report for the six-width measurement table). What IS verifiable headlessly,
 * and is exactly the DoD's "structural" claim, is that:
 *
 *   1. every genuinely NEW *layout-shell* rule (the rail, the grid, the
 *      breakpoints themselves) lives inside a `@media (min-width...)` block,
 *      never at the top level. **Correction (Opus review blocker B3, WP
 *      4e.1 fix round): this does NOT mean nothing below BP-M (720px)
 *      changed.** One deliberate top-level fix — `.banner-wrap{width:100%}`,
 *      the shrink-to-fit bug fix — changes real rendering from ~430px up to
 *      719px, because it is not itself gated by any media query (the bug it
 *      fixes exists at every width, not just desktop ones). Measured live
 *      (reviewer, against a `git archive HEAD` pre-WP baseline):
 *        430px viewport: pre-WP 398.3px wide @ x=8.3  -> post-fix 415px @ x=0
 *        719px viewport: pre-WP 398.3px wide @ x=152.8 -> post-fix 704px @ x=0
 *      The ONLY width that is genuinely byte-identical end to end is the
 *      mockup's own 390px (measured: 173.0x259.5 cover tile, unchanged) —
 *      below `.banner-wrap`'s own effective range, and below where any of
 *      this WP's real content starts changing. "Base is untouched" in this
 *      file's test names below means specifically "the SHELL/RAIL/BREAKPOINT
 *      machinery is gated correctly", not "literally nothing renders
 *      differently below 720px" — see the dedicated `.banner-wrap` pin further
 *      down for the one rule that legitimately, deliberately changes it, and
 *      docs/PROJECT_PLAN.md's Phase 4e section for the same correction;
 *   2. the tokens that DO apply at the top level (`--w-text`, `--nav-h`, ...)
 *      still resolve to the exact pixel values the pre-WP-4e.1 literals had
 *      (960px / 64px+14px=78px / 928px), so token substitution alone changed
 *      no rendered pixel;
 *   3. no colour/font/radius token changed (Android's VaultColors literal-
 *      pins them — out of scope for this WP by the brief's own rule);
 *   4. `--w-wall` (the library grid's width ceiling once BP-L applies) does
 *      NOT widen past the pre-WP baseline (960px) in any breakpoint block —
 *      an orchestrator-review anti-regression pin, see the tests near
 *      "--w-wall never widens" below for the full story: a first version of
 *      this package widened it to 1440px/1720px, which measurably made the
 *      mockup's already-oversized fixed-column cover tile BIGGER (458x687 ->
 *      ~815x1222 at 1920px live-measured), because the grid itself is still
 *      the fixed 2/3/list switch, not an auto-fill density grid. The fix is
 *      sequencing (keep the effective width until a later WP's grid can use
 *      it), not scope creep.
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

function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** Split `cssText` into { topLevel, mediaBlocks: [{header, body}] } — a
 * single non-recursive pass (no nested @media exist anywhere in this
 * codebase; @supports/@keyframes are left inside `topLevel`/whichever media
 * block contains them, since neither is relevant to this file's assertions). */
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

/** First top-level-selector rule body matching `selector` in `text` (which
 * must itself already be free of @media wrappers — pass a topLevel string or
 * one media block's body). */
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

// ---------------------------------------------------------------------
// 1. New layout machinery lives ONLY inside min-width blocks.
// ---------------------------------------------------------------------

test("app.css's top-level (non-media) text contains no rail/grid layout machinery", () => {
  for (const marker of ["grid-template-areas", "grid-area:rail", "grid-area:top", "grid-area:main", "var(--rail-w)"]) {
    assert.equal(appTop.includes(marker), false, `found "${marker}" outside any @media block`);
  }
});

test("app.css's top-level .nav rule is still the base 3-column bottom bar, untouched in substance", () => {
  const body = ruleBody(appTop, ".nav");
  assert.ok(body, ".nav rule not found at top level");
  assert.ok(body.includes("bottom:0"), "base .nav must still be bottom-pinned");
  assert.ok(body.includes("repeat(3, 1fr)"), "base .nav must still be the 3-column grid");
  assert.ok(body.includes("order:1"), "base .nav needs the D-11 order compensation for the DOM reorder");
});

test("app.css's top-level .chips rule still scrolls horizontally (BP-M's flex-wrap override lives only in its own media block)", () => {
  const body = ruleBody(appTop, ".chips");
  assert.ok(body, ".chips rule not found at top level");
  assert.ok(body.includes("overflow-x:auto"));
  assert.equal(body.includes("flex-wrap:wrap"), false);
});

test("app.css's top-level .app rule is still a plain column flexbox (grid conversion lives only in the BP-L media block)", () => {
  const body = ruleBody(appTop, ".app");
  assert.ok(body, ".app rule not found at top level");
  assert.ok(body.includes("display:flex"));
  assert.equal(body.includes("display:grid"), false);
});

// ---------------------------------------------------------------------
// 2. The three named breakpoints exist with the expected content.
// ---------------------------------------------------------------------

function findMediaBlock(minWidthPx) {
  return appMedia.find((b) => new RegExp(`min-width:\\s*${minWidthPx}px`).test(b.header) && !/and/.test(b.header));
}

test("BP-M (min-width:720px) narrows --w-text to 760px and wraps the filter chips", () => {
  const block = findMediaBlock(720);
  assert.ok(block, "no bare min-width:720px block found");
  assert.match(block.body, /--w-text:\s*760px/);
  const chipsBody = ruleBody(block.body, ".chips");
  assert.ok(chipsBody, ".chips override not found in the BP-M block");
  assert.match(chipsBody, /flex-wrap:\s*wrap/);
});

test("BP-L (min-width:1024px) turns #app into a rail grid and zeroes --nav-h (WITH a unit)", () => {
  const block = findMediaBlock(1024);
  assert.ok(block, "no bare min-width:1024px block found");
  // MUST be "0px", never a bare "0": `.bulk`'s `bottom:calc(var(--nav-h) +
  // 14px)` mixes this token with a length inside calc() — a unitless zero
  // is a <number>, not a <length>, which is a calc() TYPE MISMATCH, invalid
  // at computed-value time, and silently falls back to `bottom:auto` (Opus
  // review blocker B1, WP 4e.1 fix round: measured live, this put the bulk
  // action bar's `bottom` at -143782px — off screen — at every width
  // >=1024px). A regex of just `/0\b/` gets this BACKWARDS: `\b` only sits
  // between "0" and a NON-word character, so `/0\b/` matches the buggy bare
  // "0" (word boundary right after it) but NOT the fixed "0px" ("0" and "p"
  // are both word characters — no boundary between them) — asserting the
  // unit explicitly, as below, is what actually pins the fix.
  assert.match(block.body, /--nav-h:\s*0px\b/);
  const appBody = ruleBody(block.body, ".app");
  assert.ok(appBody, ".app override not found in the BP-L block");
  assert.match(appBody, /display:\s*grid/);
  assert.match(appBody, /grid-template-areas/);
  const navBody = ruleBody(block.body, ".nav");
  assert.ok(navBody, ".nav override not found in the BP-L block");
  assert.match(navBody, /grid-area:\s*rail/);
});

// ---------------------------------------------------------------------
// Anti-regression pin (Opus review blocker B2, WP 4e.1 fix round):
// `index.html` puts `.nav-pip` FIRST inside the Downloads `<nav-btn>`
// (before the icon) — harmless while it is `position:absolute` in the
// base/BP-M layout, but once BP-L makes it `position:static` in a row-
// direction flex container, plain DOM order would render the pip BEFORE
// the icon and push icon+label to the right instead of pinning the pip to
// the trailing edge (measured live: the whole Downloads row sat ~88px out
// of line with Library/Settings). `order` fixes this WITHOUT moving the
// DOM node (`app.js`'s `data-view` wiring, click handling and the `.on`
// toggle are all untouched) — pinned here as a class of "does this survive
// DOM order" bug, not just this instance.
// ---------------------------------------------------------------------

test("BP-L's .nav-pip override sets order:1 so DOM-first no longer means visually-first", () => {
  const block = findMediaBlock(1024);
  const pipBody = ruleBody(block.body, ".nav-pip");
  assert.ok(pipBody, ".nav-pip override not found in the BP-L block");
  assert.match(pipBody, /position:\s*static/);
  assert.match(pipBody, /order:\s*1\b/);
  assert.match(pipBody, /margin-left:\s*auto/);
});

test("BP-L's pointer-only affordance is gated on (hover:hover) and (pointer:fine), not width alone", () => {
  const found = appMedia.some(
    (b) => /min-width:\s*1024px/.test(b.header) && /hover:\s*hover/.test(b.header) && /pointer:\s*fine/.test(b.header),
  );
  assert.ok(found, "no (min-width:1024px) and (hover:hover) and (pointer:fine) block found");
});

test("BP-XL (min-width:1800px) exists, structurally, as a --w-wall hook", () => {
  const block = findMediaBlock(1800);
  assert.ok(block, "no bare min-width:1800px block found");
  assert.match(block.body, /--w-wall:/);
});

// ---------------------------------------------------------------------
// 2b. Anti-regression pin (orchestrator review finding, WP 4e.1): --w-wall
// must not widen `.view-root` past the pre-4e.1 baseline until a later WP
// lands the auto-fill grid `--tile-min` exists for. A first version of this
// package set `--w-wall` to 1440px (BP-L) / 1720px (BP-XL) — the plumbing
// (rail, breakpoints, tokens) was correct, but because `.grid` is STILL the
// mockup's fixed 2/3/list column switch (not an auto-fill density grid),
// widening `.view-root`'s cap only made the ALREADY-oversized cover tile
// bigger: measured live at 1920px, a tile that was 458x687 before this WP
// grew to ~815x1222 — worse, not better. The fix is sequencing, not scope
// creep: `--w-wall` stays equal to the pre-WP effective cap (960px, same as
// `--w-text`'s own base value) in EVERY breakpoint block until the auto-fill
// grid package changes this ONE value. This test greps the ENTIRE
// (top-level + every media block) app.css text for every `--w-wall:`
// assignment and requires all of them to agree on 960px — it will
// necessarily start failing the moment a later WP legitimately widens it,
// which is the point: that WP must consciously update (not silently
// desync) this pin alongside shipping the grid that justifies it.
// ---------------------------------------------------------------------

test("--w-wall never widens past the pre-auto-fill-grid baseline (960px) in ANY breakpoint block yet", () => {
  // Comments stripped first — this file's own explanatory prose quotes the
  // rejected 1440px/1720px values verbatim, which would otherwise false-
  // positive as a real declaration under a naive whole-file regex scan.
  // theme.css (the :root base) + app.css (BP-L/BP-XL) combined — the base
  // declaration lives in the OTHER file, `--w-wall` is not app.css-only.
  const codeOnly = stripComments(themeCss) + "\n" + stripComments(appCss);
  const assignments = [...codeOnly.matchAll(/--w-wall\s*:\s*([^;]+);/g)].map((m) => m[1].trim());
  assert.ok(assignments.length >= 3, `expected --w-wall assigned at :root, BP-L and BP-XL, found ${assignments.length}`);
  for (const value of assignments) {
    assert.equal(value, "960px", `--w-wall assigned "${value}" somewhere — must stay 960px until the auto-fill grid lands`);
  }
  // N6 (Opus review nitpick, WP 4e.1 fix round): `--tile-min` has NO
  // consumer yet (declared for a `grid-template-columns` a later WP writes —
  // see theme.css's own comment on it) — an automated "remove unused CSS
  // custom property" sweep would have every reason to delete it, since
  // nothing references it. Folded into THIS test (rather than a separate
  // one) specifically because it travels with `--w-wall`'s own "don't widen
  // yet, a later WP needs this" story — the same later WP consumes both.
  assert.match(codeOnly, /--tile-min\s*:\s*150px/, "--tile-min missing from theme.css — a later WP's auto-fill grid needs this token to still exist");
});

test(".view-root's BP-L max-width (--w-wall) computes to the SAME 960px as its own base/BP-M cap, i.e. no net widening", () => {
  const block = findMediaBlock(1024);
  const wWallInBlock = /--w-wall\s*:\s*([^;]+);/.exec(block.body);
  assert.ok(wWallInBlock, "--w-wall not (re)declared in the BP-L block");
  assert.equal(wWallInBlock[1].trim(), "960px");
});

// ---------------------------------------------------------------------
// 2c. Anti-regression pin (Opus review blocker B1, WP 4e.1 fix round): a
// UNITLESS `--nav-h` assignment is invalid the moment it meets `.bulk`'s
// `calc(var(--nav-h) + 14px)` — see app.css's BP-L block comment for the
// full mechanism (calc() type mismatch -> invalid at computed-value time ->
// `bottom:auto` -> the bulk action bar lands off screen at its static-flow
// position). This pins the CLASS of bug (every assignment must carry a
// unit), not just the one instance already fixed.
// ---------------------------------------------------------------------

test("every --nav-h assignment anywhere (theme.css + app.css) carries an explicit px unit", () => {
  const codeOnly = stripComments(themeCss) + "\n" + stripComments(appCss);
  const assignments = [...codeOnly.matchAll(/--nav-h\s*:\s*([^;]+);/g)].map((m) => m[1].trim());
  assert.ok(assignments.length >= 2, `expected --nav-h assigned at :root and BP-L, found ${assignments.length}`);
  for (const value of assignments) {
    assert.match(value, /^\d+px$/, `--nav-h assigned "${value}" — must be a unitful px length (a bare number breaks .bulk's calc())`);
  }
});

// ---------------------------------------------------------------------
// 2d. Should-fix S4 (Opus review, WP 4e.1 fix round): `.banner-wrap` and
// `.view-root` sit in the same visual column at BP-L (grid areas "banner"
// then "main") and must share ONE width cap, or a mismatched pair of
// independently-centred boxes reads as a layout bug the first time a human
// sees it (measured live: left edges 100px apart before this fix).
// ---------------------------------------------------------------------

test(".banner-wrap picks up --w-wall (not --w-text) at BP-L, matching .view-root's own cap exactly", () => {
  const block = findMediaBlock(1024);
  const bannerBody = ruleBody(block.body, ".banner-wrap");
  assert.ok(bannerBody, ".banner-wrap override not found in the BP-L block");
  assert.match(bannerBody, /max-width:\s*var\(--w-wall\)/);
});

// ---------------------------------------------------------------------
// 3. Token values at the top level reproduce the exact pre-WP-4e.1 pixel
//    values, and no colour/font/radius token changed.
// ---------------------------------------------------------------------

test(":root's base --w-text is 960px (the exact literal .view-root/.onb/.banner-wrap all hard-coded before this WP)", () => {
  assert.match(themeTop, /--w-text:\s*960px/);
});

test(":root's base --nav-h + 14px reproduces the old hand-derived .bulk bottom:78px", () => {
  const m = /--nav-h:\s*(\d+)px/.exec(themeTop);
  assert.ok(m, "--nav-h not found at top level");
  assert.equal(Number(m[1]) + 14, 78);
});

test(".bulk's max-width derives from --w-text/--gutter instead of repeating a second literal (928)", () => {
  const body = ruleBody(appTop, ".bulk");
  assert.ok(body, ".bulk rule not found at top level");
  assert.match(body, /max-width:\s*calc\(var\(--w-text\)\s*-\s*\(var\(--gutter\)\s*\*\s*2\)\)/);
  assert.equal(/928px/.test(body), false, "928 must not be repeated as a second literal");
});

test(".view-root/.onb/.banner-wrap reference --w-text, never a bare 960px literal, at the top level", () => {
  for (const selector of [".view-root", ".onb", ".banner-wrap"]) {
    const body = ruleBody(appTop, selector);
    assert.ok(body, `${selector} rule not found at top level`);
    assert.match(body, /var\(--w-text\)/, `${selector} does not reference --w-text`);
    assert.equal(/960px/.test(body), false, `${selector} still hard-codes 960px`);
  }
});

// This is the ONE top-level (non-`@media`) rule this WP changed that alters
// rendering below BP-M — see this file's header correction (Opus review
// blocker B3). It must not silently revert to the shrink-to-fit bug: without
// `width:100%`, `margin:0 auto`'s auto CROSS-axis margin on this flex item
// disables `align-items:stretch`, and the box falls back to a fixed
// ~366-398px content width regardless of viewport (measured live against a
// pre-WP baseline: 398.3px @ x=8.3 at 430px viewport, 398.3px @ x=152.8 at
// 719px — same width, different centring offset, i.e. genuinely
// shrink-to-fit, not merely narrow).
test(".banner-wrap has width:100% (the shrink-to-fit fix — must never silently revert) and still carries no `display` declaration of its own", () => {
  const body = ruleBody(appTop, ".banner-wrap");
  assert.ok(body);
  assert.match(body, /width:\s*100%/);
  assert.equal(/(^|[;{\s])display\s*:/.test(body), false, "`.banner-wrap` must stay free of an author `display` rule — it is [hidden]-toggled");
});

test("no colour/font/radius :root token changed — every original hex/font/radius value from theme.css is still present verbatim", () => {
  const mustStillContain = [
    "--bg:#0A1016", "--surface:#111C24", "--raised:#17252F", "--raised-2:#1D303C",
    "--line:#24363F", "--line-soft:#1B2A33", "--text:#E4EFF3", "--dim:#8AA1AD", "--dim-2:#63798A",
    "--accent:#2ED9CE", "--accent-deep:#12A79F", "--accent-ink:#04211F",
    "--ok:#57DD8A", "--run:#F2CE5B", "--stale:#F07B2E", "--none:#8296A6", "--danger:#FF6F6F",
    "--r-s:8px", "--r-m:12px", "--r-l:18px", "--r-xl:26px",
  ];
  for (const literal of mustStillContain) {
    assert.ok(themeTop.includes(literal), `theme.css's :root no longer contains "${literal}"`);
  }
});
