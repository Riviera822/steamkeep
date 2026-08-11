/**
 * Headless tests for web/js/lib/detail-wording.js (WP 4a.4).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { confirmedCurrentWording, CONFIRMED_CURRENT_WORDING } from "../js/lib/detail-wording.js";

test("last_manifest_check null -- NEVER_CONFIRMED, regardless of last_prefill_at", () => {
  assert.equal(confirmedCurrentWording(null, null), CONFIRMED_CURRENT_WORDING.NEVER_CONFIRMED);
  assert.equal(confirmedCurrentWording("2026-08-01T00:00:00Z", null), CONFIRMED_CURRENT_WORDING.NEVER_CONFIRMED);
});

test("both present -- CONFIRMED", () => {
  assert.equal(
    confirmedCurrentWording("2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z"),
    CONFIRMED_CURRENT_WORDING.CONFIRMED,
  );
});

test("last_manifest_check present but last_prefill_at null -- the post-deletion shape survives as CONFIRMED_BEFORE_CACHE_CLEARED", () => {
  assert.equal(
    confirmedCurrentWording(null, "2026-08-02T00:00:00Z"),
    CONFIRMED_CURRENT_WORDING.CONFIRMED_BEFORE_CACHE_CLEARED,
  );
});

test("undefined is treated the same as null (== comparison, not ===)", () => {
  assert.equal(confirmedCurrentWording(undefined, undefined), CONFIRMED_CURRENT_WORDING.NEVER_CONFIRMED);
  assert.equal(
    confirmedCurrentWording(undefined, "2026-08-02T00:00:00Z"),
    CONFIRMED_CURRENT_WORDING.CONFIRMED_BEFORE_CACHE_CLEARED,
  );
});
