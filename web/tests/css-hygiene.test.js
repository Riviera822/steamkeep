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
 * `.setAttribute("hidden", ...)`/`.removeAttribute("hidden")` alongside the
 * `.hidden = ...` property form, and the two local
 * `el(tag, className, ...)` / `sectionHeading(text)` factory functions in
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
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
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
    for (const m of text.matchAll(/(\w+)\.hidden\s*=/g)) {
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
    for (const m of text.matchAll(/(\w+)\.setAttribute\(\s*["']hidden["']\s*,/g)) {
      push("hiddenAssign", m.index, m[1], null);
    }
    for (const m of text.matchAll(/(\w+)\.removeAttribute\(\s*["']hidden["']\s*\)/g)) {
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
