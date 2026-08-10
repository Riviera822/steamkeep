/**
 * Headless tests for web/js/lib/bulk-plan.js (WP 4a.3).
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  classifyBulkSelection,
  buildBulkDownloadPlan,
  classifyBulkDeleteEligibility,
} from "../js/lib/bulk-plan.js";

const game = (appid, over) => ({
  appid,
  name: `Game ${appid}`,
  status: "idle",
  last_prefill_at: null,
  size_bytes: null,
  ...over,
});

const NOT_CACHED = game(1, { status: "idle", size_bytes: null });
const CACHED = game(2, { status: "done", size_bytes: 5_000_000_000 });
const ERRORED = game(3, { status: "error", size_bytes: null });
const CACHED_2 = game(4, { status: "done", size_bytes: 1_000_000_000 });
const ERRORED_WITH_BYTES = game(5, { status: "error", size_bytes: 2_000_000_000 });

test("classifyBulkSelection: busy (queued/running/paused prefill) is excluded from both buckets", () => {
  const jobs = [{ id: 1, appid: NOT_CACHED.appid, type: "prefill", status: "queued" }];
  const { busy, needsDownload, current } = classifyBulkSelection([NOT_CACHED, CACHED], jobs);
  assert.deepEqual(busy.map((g) => g.appid), [1]);
  assert.deepEqual(needsDownload.map((g) => g.appid), []);
  assert.deepEqual(current.map((g) => g.appid), [2]);
});

test("classifyBulkSelection: none AND error both land in needsDownload", () => {
  const { needsDownload, current } = classifyBulkSelection([NOT_CACHED, ERRORED, CACHED], []);
  assert.deepEqual(
    needsDownload.map((g) => g.appid).sort(),
    [1, 3],
  );
  assert.deepEqual(current.map((g) => g.appid), [2]);
});

test("classifyBulkSelection: a GC job for the appid does not count as busy", () => {
  const jobs = [{ id: 1, appid: NOT_CACHED.appid, type: "gc", status: "running" }];
  const { busy, needsDownload } = classifyBulkSelection([NOT_CACHED], jobs);
  assert.deepEqual(busy, []);
  assert.deepEqual(needsDownload.map((g) => g.appid), [1]);
});

// ---------------------------------------------------------------------
// buildBulkDownloadPlan — the three visible outcomes (mockup round 5,
// narrowed by one branch since there is no "stale" bucket — see
// bulk-plan.js's module header).
// ---------------------------------------------------------------------

test("plan: something needs downloading -> primary targets exactly that, skip count spelled out", () => {
  const classification = classifyBulkSelection([NOT_CACHED, CACHED], []);
  const plan = buildBulkDownloadPlan(classification, 2);
  assert.equal(plan.primaryEnabled, true);
  assert.equal(plan.primaryLabel, "Download 1 of 2");
  assert.deepEqual(plan.primaryTargets, [1]);
  assert.match(plan.note, /already cached/);
  assert.equal(plan.secondaryLabel, null);
});

test("plan: nothing needs downloading, some already cached -> disabled + explicit re-download secondary", () => {
  const classification = classifyBulkSelection([CACHED, CACHED_2], []);
  const plan = buildBulkDownloadPlan(classification, 2);
  assert.equal(plan.primaryEnabled, false);
  assert.equal(plan.primaryLabel, "All cached — nothing to download");
  assert.deepEqual(plan.primaryTargets, []);
  assert.equal(plan.secondaryLabel, "Re-download 2");
  assert.deepEqual(plan.secondaryTargets, [2, 4]);
});

test("plan: everything picked is already busy -> disabled, 'Already downloading', no secondary", () => {
  const jobs = [{ id: 1, appid: NOT_CACHED.appid, type: "prefill", status: "running" }];
  const classification = classifyBulkSelection([NOT_CACHED], jobs);
  const plan = buildBulkDownloadPlan(classification, 1);
  assert.equal(plan.primaryEnabled, false);
  assert.equal(plan.primaryLabel, "Already downloading");
  assert.equal(plan.secondaryLabel, null);
});

test("plan: a single not-cached game -> singular label, no skip note", () => {
  const classification = classifyBulkSelection([NOT_CACHED], []);
  const plan = buildBulkDownloadPlan(classification, 1);
  assert.equal(plan.primaryLabel, "Download 1 game");
  assert.equal(plan.note, "");
});

// ---------------------------------------------------------------------
// classifyBulkDeleteEligibility (WP 4a.3 review fix, should-fix 1) — the
// mockup's rule is has-cache-content, not "status !== none".
// ---------------------------------------------------------------------

test("classifyBulkDeleteEligibility: a cached game is eligible", () => {
  const eligible = classifyBulkDeleteEligibility([CACHED], []);
  assert.deepEqual(eligible.map((g) => g.appid), [2]);
});

test("classifyBulkDeleteEligibility: 'error' with ZERO visible bytes is EXCLUDED (would 404 — no depot mappings left)", () => {
  const eligible = classifyBulkDeleteEligibility([ERRORED], []);
  assert.deepEqual(eligible, []);
});

test("classifyBulkDeleteEligibility: 'error' WITH visible bytes is INCLUDED (a half-deleted/partial run genuinely has content to clean up)", () => {
  const eligible = classifyBulkDeleteEligibility([ERRORED_WITH_BYTES], []);
  assert.deepEqual(eligible.map((g) => g.appid), [5]);
});

test("classifyBulkDeleteEligibility: a not-cached (never downloaded) game is excluded", () => {
  const eligible = classifyBulkDeleteEligibility([NOT_CACHED], []);
  assert.deepEqual(eligible, []);
});

test("classifyBulkDeleteEligibility: a busy (job in flight) game is excluded even if it has bytes", () => {
  const jobs = [{ id: 1, appid: CACHED.appid, type: "prefill", status: "running" }];
  const eligible = classifyBulkDeleteEligibility([CACHED], jobs);
  assert.deepEqual(eligible, []);
});

test("classifyBulkDeleteEligibility: mixed selection returns exactly the eligible subset", () => {
  const eligible = classifyBulkDeleteEligibility(
    [NOT_CACHED, CACHED, ERRORED, ERRORED_WITH_BYTES],
    [],
  );
  assert.deepEqual(
    eligible.map((g) => g.appid).sort((a, b) => a - b),
    [2, 5],
  );
});
