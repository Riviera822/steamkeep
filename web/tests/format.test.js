/**
 * Headless tests for web/js/lib/format.js (WP 4a.3).
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { formatBytesGB, formatTimestamp } from "../js/lib/format.js";

test("formatBytesGB: under 100 GB gets one decimal place", () => {
  assert.equal(formatBytesGB(5_000_000_000), "4.7 GB");
});
test("formatBytesGB: 100 GB and over rounds to a whole number", () => {
  assert.equal(formatBytesGB(150_000_000_000), "140 GB");
});
test("formatBytesGB: null/undefined/zero/negative never fabricate a number", () => {
  assert.equal(formatBytesGB(null), null);
  assert.equal(formatBytesGB(undefined), null);
  assert.equal(formatBytesGB(0), null);
  assert.equal(formatBytesGB(-5), null);
});
test("formatBytesGB: non-finite input never fabricates a number", () => {
  assert.equal(formatBytesGB(NaN), null);
  assert.equal(formatBytesGB(Infinity), null);
});
test("formatBytesGB: non-number input never fabricates a number", () => {
  assert.equal(formatBytesGB("5000000000"), null);
});

// formatTimestamp (WP 4a.5, Downloads history rows) — added WP 4a.5.
test("formatTimestamp: null/undefined never fabricate a time", () => {
  assert.equal(formatTimestamp(null), "—");
  assert.equal(formatTimestamp(undefined), "—");
});
test("formatTimestamp: an unparseable string never fabricates a time", () => {
  assert.equal(formatTimestamp("not a date"), "—");
  assert.equal(formatTimestamp(""), "—");
});
test("formatTimestamp: a valid ISO timestamp renders the real date", () => {
  // Deliberately not asserting the exact locale-formatted string (that
  // varies with the test runner's locale/timezone) — just that a REAL
  // value came through untouched, not the "nothing honest to print" dash.
  const result = formatTimestamp("2026-06-15T12:00:00Z");
  assert.notEqual(result, "—");
  assert.ok(result.includes("2026"));
});
