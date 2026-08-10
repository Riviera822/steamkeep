/**
 * Headless tests for web/js/lib/game-status.js (WP 4a.3).
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  KIND,
  findLiveJob,
  indexLiveJobsByAppid,
  hasVisibleCacheContent,
  hasProtectedCacheContent,
  dispKind,
  statusAction,
  isJobStateTransition,
} from "../js/lib/game-status.js";

const game = (over) => ({
  appid: 1,
  name: "Aurora Cascade",
  status: "idle",
  last_prefill_at: null,
  depot_count: 0,
  size_bytes: null,
  needs_force: false,
  ...over,
});

const job = (over) => ({
  id: 1,
  appid: 1,
  type: "prefill",
  status: "running",
  ...over,
});

// ---------------------------------------------------------------------
// findLiveJob / indexLiveJobsByAppid
// ---------------------------------------------------------------------

test("findLiveJob matches only prefill jobs in running/paused", () => {
  const jobs = [
    job({ id: 1, appid: 10, status: "running" }),
    job({ id: 2, appid: 20, status: "paused" }),
    job({ id: 3, appid: 30, status: "queued" }),
    job({ id: 4, appid: 40, status: "done" }),
    job({ id: 5, appid: 50, status: "running", type: "gc" }),
  ];
  assert.equal(findLiveJob(jobs, 10).id, 1);
  assert.equal(findLiveJob(jobs, 20).id, 2);
  assert.equal(findLiveJob(jobs, 30), undefined, "queued is not live (matches mockup jobFor)");
  assert.equal(findLiveJob(jobs, 40), undefined);
  assert.equal(findLiveJob(jobs, 50), undefined, "a GC job must never drive the download pill");
});

test("indexLiveJobsByAppid is a Map keyed by appid, same filtering as findLiveJob", () => {
  const jobs = [job({ id: 1, appid: 10, status: "running" }), job({ id: 2, appid: 20, status: "queued" })];
  const map = indexLiveJobsByAppid(jobs);
  assert.equal(map.size, 1);
  assert.equal(map.get(10).id, 1);
  assert.equal(map.get(20), undefined);
});

test("indexLiveJobsByAppid tolerates a null/undefined jobs snapshot", () => {
  assert.equal(indexLiveJobsByAppid(undefined).size, 0);
  assert.equal(indexLiveJobsByAppid(null).size, 0);
});

// ---------------------------------------------------------------------
// hasVisibleCacheContent / hasProtectedCacheContent
// ---------------------------------------------------------------------

test("hasVisibleCacheContent requires a positive numeric size_bytes", () => {
  assert.equal(hasVisibleCacheContent(game({ size_bytes: 100 })), true);
  assert.equal(hasVisibleCacheContent(game({ size_bytes: 0 })), false);
  assert.equal(hasVisibleCacheContent(game({ size_bytes: null })), false);
  assert.equal(hasVisibleCacheContent(game({ size_bytes: undefined })), false);
});

test("hasProtectedCacheContent mirrors the server predicate: idle + never-prefilled + no job => false", () => {
  assert.equal(
    hasProtectedCacheContent(game({ status: "idle", last_prefill_at: null }), false),
    false,
  );
});

test("hasProtectedCacheContent: any of status!=idle / last_prefill_at set / active job protects", () => {
  assert.equal(hasProtectedCacheContent(game({ status: "done" }), false), true);
  assert.equal(hasProtectedCacheContent(game({ status: "error" }), false), true);
  assert.equal(
    hasProtectedCacheContent(game({ status: "idle", last_prefill_at: "2026-08-01T00:00:00Z" }), false),
    true,
  );
  assert.equal(hasProtectedCacheContent(game({ status: "idle", last_prefill_at: null }), true), true);
});

test("hasVisibleCacheContent and hasProtectedCacheContent can disagree (the 'last cached remnant' case)", () => {
  // status 'done' but the depot behind it was just reclaimed as an
  // orphaned remnant by an UNRELATED delete — api/README.md "Last cached
  // remnants". The grid must show this as Not cached; the deletion-side
  // predicate must still treat it as protecting a depot until it is
  // re-prefilled (fail-closed).
  const remnant = game({ status: "done", size_bytes: null, last_prefill_at: "2026-08-01T00:00:00Z" });
  assert.equal(hasVisibleCacheContent(remnant), false);
  assert.equal(hasProtectedCacheContent(remnant, false), true);
});

// ---------------------------------------------------------------------
// dispKind
// ---------------------------------------------------------------------

test("dispKind: a live running job overrides cache state", () => {
  assert.equal(dispKind(game({ status: "done", size_bytes: 100 }), job({ status: "running" })), KIND.RUNNING);
});
test("dispKind: a live paused job overrides cache state", () => {
  assert.equal(dispKind(game({ status: "idle" }), job({ status: "paused" })), KIND.PAUSED);
});
test("dispKind: no live job, status error => ERROR regardless of bytes", () => {
  assert.equal(dispKind(game({ status: "error", size_bytes: 500 }), undefined), KIND.ERROR);
  assert.equal(dispKind(game({ status: "error", size_bytes: null }), undefined), KIND.ERROR);
});
test("dispKind: no live job, done + visible bytes => CACHED", () => {
  assert.equal(dispKind(game({ status: "done", size_bytes: 500 }), undefined), KIND.CACHED);
});
test("dispKind: no live job, done but zero/no bytes => NONE (invariant, mockup round 5 finding 6)", () => {
  assert.equal(dispKind(game({ status: "done", size_bytes: null }), undefined), KIND.NONE);
  assert.equal(dispKind(game({ status: "done", size_bytes: 0 }), undefined), KIND.NONE);
});
test("dispKind: idle, no bytes => NONE", () => {
  assert.equal(dispKind(game({ status: "idle", size_bytes: null }), undefined), KIND.NONE);
});

// ---------------------------------------------------------------------
// statusAction
// ---------------------------------------------------------------------

test("statusAction: null while multi-select is active, regardless of state", () => {
  assert.equal(statusAction(game({ status: "idle" }), undefined, true), null);
  assert.equal(statusAction(game({ status: "done", size_bytes: 5 }), job({ status: "running" }), true), null);
});
test("statusAction: running job => pause", () => {
  const a = statusAction(game(), job({ status: "running" }), false);
  assert.equal(a.type, "pause");
});
test("statusAction: paused job => resume", () => {
  const a = statusAction(game(), job({ status: "paused" }), false);
  assert.equal(a.type, "resume");
});
test("statusAction: not-cached game => download", () => {
  const a = statusAction(game({ status: "idle", size_bytes: null }), undefined, false);
  assert.equal(a.type, "download");
  assert.equal(a.title, "Download to cache");
});
test("statusAction: errored game => download, titled as a retry", () => {
  const a = statusAction(game({ status: "error" }), undefined, false);
  assert.equal(a.type, "download");
  assert.equal(a.title, "Retry download");
});
test("statusAction: a cached game is inert (never a silent re-download)", () => {
  assert.equal(statusAction(game({ status: "done", size_bytes: 5 }), undefined, false), null);
});

// ---------------------------------------------------------------------
// isJobStateTransition — the round-7 "don't rebuild on a no-op tick" guard
// ---------------------------------------------------------------------

test("isJobStateTransition: same status (even with a grown log_excerpt) is NOT a transition", () => {
  const a = job({ status: "running", log_excerpt: "line 1" });
  const b = job({ status: "running", log_excerpt: "line 1\nline 2\nline 3" });
  assert.equal(isJobStateTransition(a, b), false);
});
test("isJobStateTransition: a status change IS a transition", () => {
  assert.equal(isJobStateTransition(job({ status: "running" }), job({ status: "paused" })), true);
});
test("isJobStateTransition: a brand-new job (no prev) is always a transition", () => {
  assert.equal(isJobStateTransition(undefined, job({ status: "running" })), true);
});
test("isJobStateTransition: a job disappearing (no curr) is always a transition", () => {
  assert.equal(isJobStateTransition(job({ status: "running" }), undefined), true);
});
