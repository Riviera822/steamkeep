/**
 * CSS hygiene lints (WP 4e.1 brief: "the lint I want most from this
 * package... closes a bug class permanently").
 *
 * Three instances of the same bug were found by hand across earlier WPs
 * before this lint existed (docs/LEARNINGS.md, "Web UI"): `.btn`, `h4.sec`
 * and `.onbnav` each carry an author `display` declaration, which — per the
 * cascade — always wins over the UA's `[hidden]{display:none}` default
 * regardless of selector order or specificity, so `el.hidden = true` on any
 * of them silently did nothing until a matching `SELECTOR[hidden]{
 * display:none }` guard rule was added in the same origin. This file
 * cross-references every CSS rule that both (a) is authored with a
 * `display` declaration and (b) is actually toggled via `.hidden = ...`
 * somewhere in web/js/, and requires a `[hidden]` guard rule for each.
 *
 * The second lint pins a narrower, cheaper invariant: `!important` appears
 * exactly once in this codebase on purpose (theme.css's
 * `prefers-reduced-motion` block, which must win unconditionally over every
 * `animation`/`transition` declaration elsewhere in the cascade) — any OTHER
 * `!important`, in particular one introduced inside a `@media (min-width...)`
 * breakpoint block, is exactly the kind of specificity workaround that makes
 * later overrides unpredictable, so it is banned outright.
 *
 * **Scope, stated honestly.** The display/hidden cross-reference below is a
 * real static analysis, not a hand-typed list of the three known offenders —
 * it re-derives both sides (which classes are display-styled, which classes
 * are hidden-toggled) from the actual CSS/JS source on every run, so a
 * FUTURE class introduced via the same idioms this file already understands
 * is caught automatically. It is NOT a full JS/CSS parser: identifier ->
 * class-set tracking only understands the concrete DOM-construction idioms
 * this codebase actually uses (`document.getElementById`/`createElement`,
 * `.className = "..."`, `.classList.add(...)`, `.querySelector(".cls")`,
 * `.setAttribute("hidden", ...)`/`.removeAttribute("hidden")` and
 * `.toggleAttribute("hidden", ...)` (WP 4e.2 — the `.hidden = ...`/
 * `setAttribute`/`removeAttribute` trio already covered here still missed
 * this idiom; no current code uses it, but the brief that shipped the first
 * two attribute forms explicitly flagged this one as the next gap, so it is
 * closed pre-emptively rather than waiting for a live probe to find it the
 * way N1 found the `setAttribute` gap) alongside the `.hidden = ...`
 * property form, and the two local `el(tag, className, ...)` /
 * `sectionHeading(text)` factory functions in
 * onboarding.js/settings.js/downloads.js) and resolves per FILE, top-to-
 * bottom, in one forward pass — good enough for this codebase's
 * straightforward "build the element, set its class, use it later"
 * construction style (verified against every real `.hidden =` site as of
 * this WP — see the mutation notes below), but not a substitute for a real
 * scope-aware JS parser.
 *
 * **Two known limitations, stated rather than silently worked around
 * (Opus review nitpick N1, WP 4e.1 fix round).** (1) `SIMPLE_CLASS_
 * SELECTOR_RE` only recognises a SINGLE compound selector naming exactly
 * ONE class, optionally tag-qualified (`.btn`, `h4.sec`) — a multi-class
 * compound (`.jobcard.active`) or a descendant/combinator selector
 * (`.grid.list .cap`) is invisible to the display-rule side of the cross-
 * reference even if it carries `display:` and even if JS hidden-toggles an
 * element bearing exactly that class combination; this codebase's three
 * known offenders all happen to be single-class selectors, but a future one
 * built the other way would not be caught. (2) the JS-side scan is
 * per-file and flat (no block/function scoping): two DIFFERENT variables
 * named `btn` in two different functions of the same file are tracked as
 * ONE identifier in `classesByIdent`, so a hidden-toggle on one could
 * theoretically inherit a class set assigned to the OTHER's `btn` earlier in
 * the file. Neither limitation is exploited by any real code as of this WP
 * (verified by the sanity test below finding the real three-class overlap
 * this file expects), but a reviewer or future coder should know where the
 * edges are rather than assume this is a real parser.
 *
 * **Third limitation, stated rather than pretended away (Opus review,
 * WP 4e.2 second fix round): this lint ENUMERATES idioms, it does not
 * GENERALISE over "any way JS can set the hidden attribute/property".**
 * `el.hidden ||= true` / `el.hidden ??= true` and `el.toggleAttribute?.(
 * "hidden", true)` (optional chaining) are now recognised — both were
 * one character away from an idiom already covered, and both were probed
 * and found to survive silently before this fix. But `Object.assign(el,
 * {hidden: true})` sets the exact same property through a shape a FORWARD
 * regex scan over `identifier.hidden`/`identifier.toggleAttribute(...)`
 * text cannot see at all (the identifier and the property name never sit
 * next to each other in the source) — closing it would need real
 * object-literal parsing, not another regex. Not exploited by any real
 * code here as of this WP; recorded as an acknowledged gap rather than
 * implied to be covered by the four idioms this file actually scans for.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync, writeFileSync, rmSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.join(__dirname, "..");
const appCssPath = path.join(webDir, "css", "app.css");
const themeCssPath = path.join(webDir, "css", "theme.css");
const indexHtmlPath = path.join(webDir, "index.html");
const jsDir = path.join(webDir, "js");

const appCss = readFileSync(appCssPath, "utf8");
const themeCss = readFileSync(themeCssPath, "utf8");
const indexHtml = readFileSync(indexHtmlPath, "utf8");
const combinedCss = `${themeCss}\n${appCss}`;

function walkJsFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) out.push(...walkJsFiles(full));
    else if (entry.endsWith(".js")) out.push(full);
  }
  return out;
}
const jsFiles = walkJsFiles(jsDir);

// ---------------------------------------------------------------------
// CSS parsing helpers — brace-balanced, comment-stripped, good enough for
// this file's plain CSS (no strings containing braces).
// ---------------------------------------------------------------------

function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/**
 * Flatten every rule in `cssText` (recursing into @media/@supports bodies,
 * skipping @keyframes entirely — its percentage/from/to "selectors" are not
 * class selectors and would only add noise) into a flat list of
 * `{selector, body}`, one entry per individual selector in a comma-separated
 * selector list.
 */
function extractAllRules(cssText) {
  const css = stripComments(cssText);
  const rules = [];

  function parseBlock(text, start, end) {
    let j = start;
    while (j < end) {
      while (j < end && /\s/.test(text[j])) j++;
      if (j >= end) break;
      const braceIdx = text.indexOf("{", j);
      if (braceIdx === -1 || braceIdx >= end) break;
      const header = text.slice(j, braceIdx).trim();
      let depth = 1;
      let k = braceIdx + 1;
      while (depth > 0 && k < end) {
        if (text[k] === "{") depth++;
        else if (text[k] === "}") depth--;
        k++;
      }
      const body = text.slice(braceIdx + 1, k - 1);
      if (/^@media/.test(header) || /^@supports/.test(header)) {
        parseBlock(text, braceIdx + 1, k - 1);
      } else if (/^@keyframes/.test(header)) {
        // skip — no class selectors inside
      } else if (header.startsWith("@")) {
        // unknown at-rule (none expected here) — skip its body defensively
      } else {
        for (const sel of header.split(",")) {
          const trimmed = sel.trim();
          if (trimmed) rules.push({ selector: trimmed, body });
        }
      }
      j = k;
    }
  }

  parseBlock(css, 0, css.length);
  return rules;
}

const allRules = extractAllRules(combinedCss);

// Selectors this lint understands as "a single compound selector directly
// naming one class" — optionally tag-qualified (`h4.sec`), never a
// descendant/combinator selector (`.grid.list .cap`) or a multi-class
// compound (`.jobcard.active`), since those are never what a `.hidden = `
// call site's class SET reduces to one-for-one.
const SIMPLE_CLASS_SELECTOR_RE = /^([a-zA-Z][\w-]*)?\.([\w-]+)$/;
// The matching guard form: exactly the same selector text with `[hidden]`
// appended (the convention every existing guard in this codebase follows —
// `.btn[hidden]`, `h4.sec[hidden]`, `.onbnav[hidden]`).
function guardSelectorFor(selector) {
  return `${selector}[hidden]`;
}

/** class token -> Set of exact selector strings (e.g. "h4.sec", ".btn")
 * that declare `display:` for it, excluding guard rules themselves. */
const displayRulesByClass = new Map();
/** Set of guard selector strings (selector text WITH the trailing
 * `[hidden]`) that resolve to `display:none`. */
const guardSelectors = new Set();

for (const { selector, body } of allRules) {
  const hasDisplay = /display\s*:/.test(body);
  if (!hasDisplay) continue;
  const isGuard = selector.includes("[hidden]");
  if (isGuard) {
    if (/display\s*:\s*none\b/.test(body)) guardSelectors.add(selector);
    continue;
  }
  const m = SIMPLE_CLASS_SELECTOR_RE.exec(selector);
  if (!m) continue; // not a single-class compound this lint tracks
  const cls = m[2];
  if (!displayRulesByClass.has(cls)) displayRulesByClass.set(cls, new Set());
  displayRulesByClass.get(cls).add(selector);
}

// ---------------------------------------------------------------------
// index.html id -> class map (for `document.getElementById(...)` sites)
// ---------------------------------------------------------------------

function buildIdClassMap(html) {
  const map = new Map();
  for (const tagMatch of html.matchAll(/<[a-zA-Z][^>]*>/g)) {
    const tag = tagMatch[0];
    const idMatch = /\bid=["']([^"']+)["']/.exec(tag);
    const classMatch = /\bclass=["']([^"']+)["']/.exec(tag);
    if (idMatch && classMatch) map.set(idMatch[1], classMatch[1]);
  }
  return map;
}
const idClassMap = buildIdClassMap(indexHtml);

// ---------------------------------------------------------------------
// JS static scan: for every `.hidden = ` site, resolve the identifier's
// known class set at that point in the file (single forward pass, see this
// file's header for the supported idioms and their scope).
// ---------------------------------------------------------------------

function tokensFromQuotedList(argText) {
  const out = [];
  for (const m of argText.matchAll(/["']([^"']+)["']/g)) {
    out.push(...m[1].split(/\s+/).filter(Boolean));
  }
  return out;
}

/** @returns {Map<string, Array<{file:string, ident:string}>>} class token ->
 * every (file, identifier) site where an element carrying that class was
 * hidden-toggled. */
function findHiddenToggledClasses(files, idMap) {
  const results = new Map();
  const record = (file, ident, classSet) => {
    for (const cls of classSet) {
      if (!results.has(cls)) results.set(cls, []);
      results.get(cls).push({ file, ident });
    }
  };

  for (const file of files) {
    const text = readFileSync(file, "utf8");
    const events = [];
    const push = (type, index, ident, extra) => events.push({ type, index, ident, extra });

    for (const m of text.matchAll(/(\w+)\s*=\s*document\.getElementById\(\s*["']([^"']+)["']\s*\)/g)) {
      push("byId", m.index, m[1], m[2]);
    }
    for (const m of text.matchAll(/(\w+)\s*=\s*el\(\s*["']([^"']*)["']\s*,\s*["']([^"']*)["']/g)) {
      push("elFactory", m.index, m[1], { tag: m[2], cls: m[3] });
    }
    for (const m of text.matchAll(/(\w+)\s*=\s*sectionHeading\(/g)) {
      push("sectionHeadingFactory", m.index, m[1], null);
    }
    for (const m of text.matchAll(/(\w+)\.className\s*=\s*["'`]([^"'`]*)["'`]/g)) {
      push("setClassName", m.index, m[1], m[2]);
    }
    for (const m of text.matchAll(/(\w+)\.className\s*\+=\s*["'`]\s*([^"'`]*)["'`]/g)) {
      push("appendClassName", m.index, m[1], m[2]);
    }
    for (const m of text.matchAll(/(\w+)\.classList\.add\(([^)]*)\)/g)) {
      push("classListAdd", m.index, m[1], m[2]);
    }
    for (const m of text.matchAll(/\b(\w+)\s*=\s*[\w.]+\.querySelector\(\s*["']\.([\w-]+)["']\s*\)/g)) {
      push("querySelectorClass", m.index, m[1], m[2]);
    }
    // Opus review nitpick, WP 4e.2 second fix round: `el.hidden ||= true` /
    // `el.hidden ??= true` toggle the same property a plain `el.hidden = `
    // assignment does, but the literal `\s*=` below used to require the
    // `=` immediately after optional whitespace — "hidden ||=" has `|`
    // characters in that gap, so it never matched. The optional
    // `(?:\|\||\?\?)?` group closes it without touching the plain-`=` case.
    for (const m of text.matchAll(/(\w+)\.hidden\s*(?:\|\||\?\?)?=/g)) {
      push("hiddenAssign", m.index, m[1], null);
    }
    // N1 (Opus review nitpick, WP 4e.1 fix round): `el.hidden = ...` is not
    // the only way this codebase's JS toggles the `hidden` ATTRIBUTE — the
    // DOM property and the attribute are the same underlying state (setting
    // either reflects to the other), so a call site using
    // `setAttribute("hidden", ...)`/`removeAttribute("hidden")` toggles it
    // exactly as much as a `.hidden =` assignment does, and needs the same
    // guard if the element's class also carries an author `display` rule.
    // The reviewer probed this hole directly (a new toggle site written this
    // way, with no guard, survived the lint before this fix) — closed by
    // treating both attribute-manipulation forms as additional hiddenAssign
    // events at the SAME identifier.
    // Opus review nitpick, WP 4e.2 fix round: all three attribute-based
    // regexes below now carry the `i` flag. HTML attribute names are
    // case-insensitive by spec (the DOM lowercases the qualified name on
    // write, verified by the reviewer in a real DOM) — `setAttribute(
    // "HIDDEN", "")`/`toggleAttribute("Hidden", true)` genuinely toggle the
    // same `hidden` attribute a lowercase call site would, and both used to
    // survive this lint silently (the literal `["']hidden["']` match is
    // case-SENSITIVE by default, so it simply never fired for either).
    for (const m of text.matchAll(/(\w+)\.setAttribute\(\s*["']hidden["']\s*,/gi)) {
      push("hiddenAssign", m.index, m[1], null);
    }
    for (const m of text.matchAll(/(\w+)\.removeAttribute\(\s*["']hidden["']\s*\)/gi)) {
      push("hiddenAssign", m.index, m[1], null);
    }
    // WP 4e.2: `el.toggleAttribute("hidden", cond)` (and the no-second-arg
    // `el.toggleAttribute("hidden")` form) toggle the exact same underlying
    // `hidden` attribute as the three forms above — a class carrying an
    // author `display` rule needs the same `[hidden]` guard regardless of
    // WHICH of these four idioms a future call site happens to use. No file
    // in web/js/ uses this idiom as of this WP (see the sanity-negative test
    // below, which proves the regex is live rather than dead code); it is
    // added now, alongside the class of bug it closes, rather than waiting
    // for a live probe the way N1's `setAttribute` gap was found.
    //
    // Opus review nitpick, WP 4e.2 second fix round: `el.toggleAttribute?.(
    // "hidden", true)` (optional-chained, e.g. guarding a possibly-null
    // element) toggles the identical attribute a plain `.toggleAttribute(`
    // call does, but the literal `\(` right after the identifier used to
    // require the call parenthesis with nothing in between — `\??\.?`
    // allows the optional `?` and the `.` of `?.` to sit there instead.
    for (const m of text.matchAll(/(\w+)\.toggleAttribute\??\.?\(\s*["']hidden["']/gi)) {
      push("hiddenAssign", m.index, m[1], null);
    }

    events.sort((a, b) => a.index - b.index);

    const classesByIdent = new Map(); // ident -> Set<class>

    for (const ev of events) {
      if (ev.type === "byId") {
        const seed = idMap.get(ev.extra);
        classesByIdent.set(ev.ident, seed ? new Set(seed.split(/\s+/)) : new Set());
      } else if (ev.type === "elFactory") {
        classesByIdent.set(ev.ident, new Set(ev.extra.cls.split(/\s+/).filter(Boolean)));
      } else if (ev.type === "sectionHeadingFactory") {
        // Known factory (downloads.js): `document.createElement("h4");
        // h4.className = "sec";` — hardcoded here since it constructs via a
        // helper this scan cannot trace into a different call site's variable.
        classesByIdent.set(ev.ident, new Set(["sec"]));
      } else if (ev.type === "setClassName") {
        classesByIdent.set(ev.ident, new Set(ev.extra.split(/\s+/).filter(Boolean)));
      } else if (ev.type === "appendClassName") {
        const set = classesByIdent.get(ev.ident) || new Set();
        for (const t of ev.extra.split(/\s+/).filter(Boolean)) set.add(t);
        classesByIdent.set(ev.ident, set);
      } else if (ev.type === "classListAdd") {
        const set = classesByIdent.get(ev.ident) || new Set();
        for (const t of tokensFromQuotedList(ev.extra)) set.add(t);
        classesByIdent.set(ev.ident, set);
      } else if (ev.type === "querySelectorClass") {
        classesByIdent.set(ev.ident, new Set([ev.extra]));
      } else if (ev.type === "hiddenAssign") {
        const set = classesByIdent.get(ev.ident);
        if (set && set.size) record(path.relative(webDir, file), ev.ident, set);
      }
    }
  }

  return results;
}

const hiddenToggledClasses = findHiddenToggledClasses(jsFiles, idClassMap);

// ---------------------------------------------------------------------
// Lint 1: every display-styled class that is ALSO hidden-toggled needs a
// matching [hidden] guard rule.
// ---------------------------------------------------------------------

test("every CSS class with an author `display` rule that is hidden-toggled in web/js/ has a matching [hidden] guard", () => {
  const missing = [];
  for (const [cls, sites] of hiddenToggledClasses) {
    const displaySelectors = displayRulesByClass.get(cls);
    if (!displaySelectors) continue; // no author display rule for this class — the UA [hidden] default already works
    for (const selector of displaySelectors) {
      if (!guardSelectors.has(guardSelectorFor(selector))) {
        missing.push({ class: cls, selector, sites: sites.map((s) => `${s.file}:${s.ident}`) });
      }
    }
  }
  assert.deepEqual(
    missing,
    [],
    `class(es) with an author display rule and a .hidden= call site but no [hidden] guard: ${JSON.stringify(missing, null, 2)}`,
  );
});

// Sanity pin (not the mutation target itself, but proves the analysis found
// real work to do rather than vacuously passing on an empty result): the
// three historically-buggy classes from docs/LEARNINGS.md must actually be
// present in both sides of the cross-reference.
test("sanity: the three historically-buggy classes (.btn, h4.sec, .onbnav) are found on BOTH sides of the cross-reference", () => {
  for (const cls of ["btn", "sec", "onbnav"]) {
    assert.ok(displayRulesByClass.has(cls), `no display rule found for .${cls} — CSS parsing regressed`);
    assert.ok(hiddenToggledClasses.has(cls), `no hidden-toggle site found for .${cls} — JS scanning regressed`);
  }
});

// WP 4e.2: no real file in web/js/ uses `toggleAttribute("hidden", ...)`
// today, so — unlike the `setAttribute`/`removeAttribute` regexes above,
// which the sanity test's real `.btn`/`h4.sec`/`.onbnav` sites exercise
// directly — this idiom's regex would otherwise be untested dead code. This
// test drives `findHiddenToggledClasses` (the exact function the real lint
// above calls) against a throwaway fixture file containing the idiom, on
// both the two-argument and bare forms, proving the new regex actually
// fires rather than merely reading as though it should. The fixture is
// written to and removed from web/tests/ itself (never web/js/, so it can
// never leak into the real scan) inside this single test.
test("toggleAttribute(\"hidden\", ...) is recognized as a hidden-toggle site, both the two-arg and bare forms", () => {
  const fixturePath = path.join(__dirname, ".tmp-toggle-attribute-fixture.js");
  writeFileSync(
    fixturePath,
    [
      'const a = document.createElement("button");',
      'a.className = "probe-two-arg";',
      "a.toggleAttribute('hidden', someCondition);",
      'const b = document.createElement("button");',
      'b.className = "probe-bare";',
      'b.toggleAttribute("hidden");',
    ].join("\n"),
  );
  try {
    const found = findHiddenToggledClasses([fixturePath], new Map());
    assert.ok(found.has("probe-two-arg"), "toggleAttribute(\"hidden\", cond) form not recognized");
    assert.ok(found.has("probe-bare"), "bare toggleAttribute(\"hidden\") form not recognized");
  } finally {
    rmSync(fixturePath, { force: true });
  }
});

// Opus review nitpick, WP 4e.2 fix round: the reviewer's own probe —
// `toggleAttribute("HIDDEN", true)` and `setAttribute("Hidden", "")` — both
// verified in a real DOM to genuinely set the `hidden` attribute (the spec
// lowercases the qualified name on write), and both used to survive this
// lint silently because the literal `["']hidden["']` match was
// case-sensitive. This fixture pins all three attribute-based idioms
// (setAttribute/removeAttribute/toggleAttribute) against exactly the
// reviewer's own mixed-case spellings; mutation-verified by dropping the `i`
// flag from any one of the three regexes above and watching this test die.
test("the three attribute-based hidden-toggle idioms are recognized case-insensitively (HTML attribute names are case-insensitive)", () => {
  const fixturePath = path.join(__dirname, ".tmp-case-insensitive-fixture.js");
  writeFileSync(
    fixturePath,
    [
      'const a = document.createElement("button");',
      'a.className = "probe-set-upper";',
      'a.setAttribute("HIDDEN", "");',
      'const b = document.createElement("button");',
      'b.className = "probe-remove-mixed";',
      'b.removeAttribute("Hidden");',
      'const c = document.createElement("button");',
      'c.className = "probe-toggle-upper";',
      'c.toggleAttribute("HIDDEN", true);',
    ].join("\n"),
  );
  try {
    const found = findHiddenToggledClasses([fixturePath], new Map());
    assert.ok(found.has("probe-set-upper"), 'setAttribute("HIDDEN", ...) not recognized');
    assert.ok(found.has("probe-remove-mixed"), 'removeAttribute("Hidden") not recognized');
    assert.ok(found.has("probe-toggle-upper"), 'toggleAttribute("HIDDEN", ...) not recognized');
  } finally {
    rmSync(fixturePath, { force: true });
  }
});

// Opus review nitpick, WP 4e.2 second fix round: the reviewer's own two
// surviving probes — `el.hidden ||= true` (and the `??=` sibling) and
// `el.toggleAttribute?.("hidden", true)` (optional chaining) — pinned
// against the exact same throwaway-fixture pattern as every other idiom in
// this file. Mutation-verified: reverting either regex above to its
// pre-fix form (dropping `(?:\|\||\?\?)?` or `\??\.?`) makes the matching
// assertion below fail by name.
test("el.hidden ||=/??= and el.toggleAttribute?.(\"hidden\", ...) (optional chaining) are recognized as hidden-toggle sites", () => {
  const fixturePath = path.join(__dirname, ".tmp-compound-operator-fixture.js");
  writeFileSync(
    fixturePath,
    [
      'const a = document.createElement("button");',
      'a.className = "probe-or-equals";',
      "a.hidden ||= true;",
      'const b = document.createElement("button");',
      'b.className = "probe-nullish-equals";',
      "b.hidden ??= true;",
      'const c = document.createElement("button");',
      'c.className = "probe-toggle-optional-chain";',
      'c.toggleAttribute?.("hidden", true);',
    ].join("\n"),
  );
  try {
    const found = findHiddenToggledClasses([fixturePath], new Map());
    assert.ok(found.has("probe-or-equals"), "el.hidden ||= true not recognized");
    assert.ok(found.has("probe-nullish-equals"), "el.hidden ??= true not recognized");
    assert.ok(found.has("probe-toggle-optional-chain"), 'el.toggleAttribute?.("hidden", ...) not recognized');
  } finally {
    rmSync(fixturePath, { force: true });
  }
});

// N2 (Opus review nitpick, WP 4e.1 fix round): the `sectionHeadingFactory`
// branch above hardcodes `new Set(["sec"])` because this scan cannot trace
// INTO a helper function to see what class IT assigns — it has to know the
// answer up front. That hardcode can silently drift from the real
// `sectionHeading()` implementation (web/js/views/downloads.js) if a future
// change gives it a different class; this test reads the real source and
// asserts the mapping still holds, so drift fails loudly here instead of
// silently under-covering the lint.
test("sectionHeadingFactory's hardcoded ['sec'] mapping still matches the real sectionHeading() implementation", () => {
  const downloadsJs = readFileSync(path.join(jsDir, "views", "downloads.js"), "utf8");
  const fnMatch = /function sectionHeading\([^)]*\)\s*\{([\s\S]*?)\n\}/.exec(downloadsJs);
  assert.ok(fnMatch, "sectionHeading() not found in downloads.js — did it move or get renamed?");
  assert.match(
    fnMatch[1],
    /\.className\s*=\s*["']sec["']/,
    "sectionHeading() no longer assigns className \"sec\" — update the hardcoded mapping in findHiddenToggledClasses above",
  );
});

// ---------------------------------------------------------------------
// Lint 2: no `!important` outside the reduced-motion block.
// ---------------------------------------------------------------------

function findImportantViolations(cssText) {
  const css = stripComments(cssText);
  const mediaSpans = [];
  const mediaRe = /@media[^{]*\{/g;
  let m;
  while ((m = mediaRe.exec(css))) {
    const openBrace = m.index + m[0].length - 1;
    let depth = 1;
    let k = openBrace + 1;
    while (depth > 0 && k < css.length) {
      if (css[k] === "{") depth++;
      else if (css[k] === "}") depth--;
      k++;
    }
    mediaSpans.push({ header: m[0], bodyStart: openBrace + 1, bodyEnd: k - 1 });
    mediaRe.lastIndex = k;
  }

  const violations = [];
  const impRe = /!important/g;
  let im;
  while ((im = impRe.exec(css))) {
    const idx = im.index;
    const insideReducedMotion = mediaSpans.some(
      (s) => idx > s.bodyStart && idx < s.bodyEnd && /prefers-reduced-motion/.test(s.header),
    );
    if (!insideReducedMotion) {
      violations.push(css.slice(Math.max(0, idx - 60), idx + 15).trim());
    }
  }
  return violations;
}

test("no `!important` appears anywhere except inside the prefers-reduced-motion block", () => {
  const violations = findImportantViolations(combinedCss);
  assert.deepEqual(violations, [], `!important found outside prefers-reduced-motion: ${JSON.stringify(violations)}`);
});

test("sanity: the reduced-motion block itself still contains !important (proves the allow-list branch is reachable, not vacuous)", () => {
  assert.ok(/prefers-reduced-motion[\s\S]*?!important/.test(stripComments(themeCss)));
});
