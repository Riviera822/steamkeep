/**
 * Headless tests for web/js/lib/gc-log-summary.js (WP 4a.4).
 *
 * Fixtures are the EXACT log text api/README.md quotes for "What you see"
 * (dry run) and the executed-run example under "GC totals" — same fixtures
 * the Android sibling's `GcLogSummaryTest.kt` (WP 4b.6) pins, ported here
 * verbatim so both frontends read the same server text the same way.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseGcLogSummary, GC_MODE } from "../js/lib/gc-log-summary.js";

const dryRunLog = `[vault-api] GC for app 440: DRY RUN.
  depot 441 planned: orphans=2 (700 bytes) held_back=2 (700 bytes) kept=2 ...
    ~ 00..b1: stored 0.3 days ago, grace window is 14 days
[vault-api] GC totals (DRY RUN): orphans=2 (700 bytes) held_back=2 (700 bytes)
  would_delete=0 (0 bytes) reclaimable_dedupe_bytes=0 planned_depots=[441]. ...`;

const executedLog = `[vault-api] GC for app 440: EXECUTE.
  depot 441 planned: orphans=2 (700 bytes) -> removed=2 (700 bytes) already_gone=0 ...
  depot 900 skipped_no_manifest: ADR-0007 readiness gate: no current manifest ...
[vault-api] GC totals (EXECUTED): chunks_removed=2 bytes_freed=700 already_gone=0
  dedupe_removed=2 dedupe_bytes_freed=1234 total_bytes_freed=1934 problems=0
  declined=0 held_back=0 (0 bytes) depots_touched=[441]
  needs_force_set_for=[440, 730]`;

test("null or blank excerpt parses to null", () => {
  assert.equal(parseGcLogSummary(null), null);
  assert.equal(parseGcLogSummary(undefined), null);
  assert.equal(parseGcLogSummary(""), null);
  assert.equal(parseGcLogSummary("   "), null);
});

test("an excerpt with no GC totals line parses to null -- never fabricates a headline", () => {
  assert.equal(parseGcLogSummary("some unrelated prefill log with no GC markers at all"), null);
});

test("dry run -- would_delete and held_back read from the TOTALS line", () => {
  const summary = parseGcLogSummary(dryRunLog);
  assert.equal(summary.mode, GC_MODE.DRY_RUN);
  assert.equal(summary.wouldDeleteCount, 0);
  assert.equal(summary.wouldDeleteBytes, 0);
  assert.equal(summary.heldBackCount, 2);
  assert.equal(summary.heldBackBytes, 700);
  assert.equal(summary.chunksRemoved, null);
  assert.equal(summary.totalBytesFreed, null);
});

test("MUTATION TARGET -- distinguishes the totals line's held_back from an earlier per-depot held_back", () => {
  const log = `[vault-api] GC for app 440: DRY RUN.
  depot 441 planned: orphans=9 (9000 bytes) held_back=1 (300 bytes) kept=1 ...
[vault-api] GC totals (DRY RUN): orphans=9 (9000 bytes) held_back=2 (700 bytes)
  would_delete=0 (0 bytes) reclaimable_dedupe_bytes=0 planned_depots=[441]. ...`;
  const summary = parseGcLogSummary(log);
  assert.equal(summary.heldBackCount, 2);
  assert.equal(summary.heldBackBytes, 700);
});

test("executed -- chunks_removed, bytes_freed and total_bytes_freed read correctly despite the dedupe_ and total_ prefixed lookalike keys", () => {
  const summary = parseGcLogSummary(executedLog);
  assert.equal(summary.mode, GC_MODE.EXECUTE);
  assert.equal(summary.chunksRemoved, 2);
  // MUTATION TARGET: without the \b word-boundary guard, a bare
  // "bytes_freed=" regex would match inside "dedupe_bytes_freed=1234" or
  // "total_bytes_freed=1934" too -- this pins the STANDALONE bytes_freed=700,
  // not either lookalike.
  assert.equal(summary.bytesFreed, 700);
  assert.equal(summary.totalBytesFreed, 1934);
  assert.equal(summary.heldBackCount, 0);
  assert.equal(summary.heldBackBytes, 0);
  assert.equal(summary.wouldDeleteCount, null);
});

test("NIT -- bytes_freed resolves correctly regardless of key ORDER, not just because it happens to come first", () => {
  const reorderedLog =
    "[vault-api] GC totals (EXECUTED): total_bytes_freed=1934 " +
    "dedupe_bytes_freed=1234 bytes_freed=700 chunks_removed=2";
  const summary = parseGcLogSummary(reorderedLog);
  assert.equal(summary.bytesFreed, 700);
  assert.equal(summary.totalBytesFreed, 1934);
  assert.equal(summary.chunksRemoved, 2);
});

test("a nonzero would_delete count reports the exact bytes", () => {
  const log = `[vault-api] GC totals (DRY RUN): orphans=5 (12345 bytes) held_back=0 (0 bytes)
  would_delete=5 (12345 bytes) reclaimable_dedupe_bytes=0 planned_depots=[441].`;
  const summary = parseGcLogSummary(log);
  assert.equal(summary.wouldDeleteCount, 5);
  assert.equal(summary.wouldDeleteBytes, 12345);
});
