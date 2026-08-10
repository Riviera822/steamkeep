/**
 * Headless tests for web/js/lib/format.js (WP 4a.3).
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { formatBytesGB } from "../js/lib/format.js";

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
