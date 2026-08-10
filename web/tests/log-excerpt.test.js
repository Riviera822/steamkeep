/**
 * Headless tests for web/js/lib/log-excerpt.js (WP 4a.5).
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { selectExcerptDisplay, EXCERPT_STATE } from "../js/lib/log-excerpt.js";

test("collapsed: a row that isn't expanded shows nothing, regardless of any other field", () => {
  assert.deepEqual(selectExcerptDisplay({ expanded: false }), {
    state: EXCERPT_STATE.COLLAPSED,
    lines: [],
    truncated: false,
  });
  // Even a fetched excerpt or an in-flight load must not leak through while
  // collapsed — a row can be re-collapsed while its fetch is still in
  // flight (fast double-click), and the collapsed state must win.
  assert.equal(
    selectExcerptDisplay({ expanded: false, loading: true, excerpt: "some log" }).state,
    EXCERPT_STATE.COLLAPSED,
  );
});

test("loading: expanded with a fetch in flight, before any excerpt has arrived", () => {
  const display = selectExcerptDisplay({ expanded: true, loading: true });
  assert.equal(display.state, EXCERPT_STATE.LOADING);
  assert.deepEqual(display.lines, []);
});

test("error: the lazy GET /v1/jobs/{id} fetch failed", () => {
  const display = selectExcerptDisplay({ expanded: true, error: "not found" });
  assert.equal(display.state, EXCERPT_STATE.ERROR);
  assert.equal(display.message, "not found");
});

test("error takes priority over a stale excerpt from a PREVIOUS successful fetch", () => {
  const display = selectExcerptDisplay({ expanded: true, error: "network", excerpt: "old log" });
  assert.equal(display.state, EXCERPT_STATE.ERROR);
});

test("empty: excerpt is null (job never produced output, e.g. cancelled before it ran)", () => {
  assert.equal(selectExcerptDisplay({ expanded: true, excerpt: null }).state, EXCERPT_STATE.EMPTY);
});

test("empty: excerpt is undefined (fetch never completed, distinct from null but same display)", () => {
  assert.equal(
    selectExcerptDisplay({ expanded: true, excerpt: undefined }).state,
    EXCERPT_STATE.EMPTY,
  );
});

test("empty: excerpt is whitespace-only", () => {
  assert.equal(selectExcerptDisplay({ expanded: true, excerpt: "   \n  " }).state, EXCERPT_STATE.EMPTY);
});

test("ready: a normal multi-line excerpt is split into lines, not truncated", () => {
  const display = selectExcerptDisplay({
    expanded: true,
    excerpt: "line one\nline two\n[vault-api] done",
  });
  assert.equal(display.state, EXCERPT_STATE.READY);
  assert.equal(display.truncated, false);
  assert.deepEqual(display.lines, ["line one", "line two", "[vault-api] done"]);
});

test("ready: a single-line excerpt with no newline at all", () => {
  const display = selectExcerptDisplay({ expanded: true, excerpt: "[vault-api] queued." });
  assert.equal(display.state, EXCERPT_STATE.READY);
  assert.deepEqual(display.lines, ["[vault-api] queued."]);
});

// ---------------------------------------------------------------------
// api/README.md: "log_excerpt is the ANSI-stripped tail... prefixed with
// [...truncated...] when it was cut". The marker must be detected AND
// stripped from the displayed body, and the caller told to show a note —
// the marker text itself must never leak into the first displayed line.
// ---------------------------------------------------------------------
test("ready: a truncated excerpt is flagged AND the marker is stripped from the body", () => {
  const display = selectExcerptDisplay({
    expanded: true,
    excerpt: "[...truncated...]\nthe rest of the log\nmore output",
  });
  assert.equal(display.state, EXCERPT_STATE.READY);
  assert.equal(display.truncated, true);
  assert.deepEqual(display.lines, ["the rest of the log", "more output"]);
  assert.ok(!display.lines.some((l) => l.includes("truncated")));
});

test("ready: the truncation marker is only recognised at the very START of the text", () => {
  // A log line that happens to CONTAIN the words "truncated" elsewhere must
  // not be mistaken for the real marker — only a literal prefix counts.
  const display = selectExcerptDisplay({
    expanded: true,
    excerpt: "line one\n[...truncated...] appears mid-log, not as a real marker",
  });
  assert.equal(display.truncated, false);
  assert.equal(display.lines.length, 2);
});

test("ready: multiple blank lines right after the truncation marker are collapsed away, not shown as empty lines", () => {
  const display = selectExcerptDisplay({ expanded: true, excerpt: "[...truncated...]\n\n\nreal content" });
  assert.equal(display.truncated, true);
  assert.deepEqual(display.lines, ["real content"]);
});
