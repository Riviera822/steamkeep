import { test } from "node:test";
import assert from "node:assert/strict";
import { sweepTargetsMessage, cachedSweepGcRiskWarning } from "../js/lib/schedule-presentation.js";

// ---------------------------------------------------------------------
// sweepTargetsMessage — THREE distinct states (review round 1 blocker B1:
// the first version only had two, collapsing "never run" together with "a
// sweep started but recorded no result", which is permanent after a crash
// mid-sweep — api/vault_api/scheduler.py::claim_sweep stamps last_sweep_at
// and NULLs both counters in one statement; finish_sweep fills them in only
// once the sweep completes).
// ---------------------------------------------------------------------

test("no schedule snapshot yet -> null (nothing honest to print)", () => {
  assert.equal(sweepTargetsMessage(null), null);
  assert.equal(sweepTargetsMessage(undefined), null);
});

test("last_sweep_at AND last_sweep_targets both null -> the 'never run' message", () => {
  const msg = sweepTargetsMessage({ last_sweep_targets: null, last_sweep_at: null, last_sweep_enqueued: null });
  assert.match(msg, /has not run yet/);
  assert.doesNotMatch(msg, /found no games to check/);
  assert.doesNotMatch(msg, /started/); // must not claim a sweep started when it never claimed one
});

test("B1 FIX: last_sweep_at STAMPED but last_sweep_targets still null -> a THIRD message, states both remaining possibilities, picks neither", () => {
  const neverRun = sweepTargetsMessage({ last_sweep_targets: null, last_sweep_at: null, last_sweep_enqueued: null });
  const startedNoResult = sweepTargetsMessage({
    last_sweep_targets: null,
    last_sweep_at: "2026-08-22T10:00:00Z",
    last_sweep_enqueued: null,
  });
  assert.notEqual(startedNoResult, neverRun);
  assert.match(startedNoResult, /started/);
  // Both remaining possibilities named, neither asserted as fact:
  assert.match(startedNoResult, /still be running/i);
  assert.match(startedNoResult, /stopped before finishing/i);
  assert.doesNotMatch(startedNoResult, /has not run yet/);
  assert.doesNotMatch(startedNoResult, /found no games to check/);
});

test("MUTATION PIN: last_sweep_targets: 0 is a DIFFERENT message from null-with-no-timestamp — a falsy check would collapse them", () => {
  const neverRun = sweepTargetsMessage({ last_sweep_targets: null, last_sweep_at: null, last_sweep_enqueued: null });
  const zeroTargets = sweepTargetsMessage({
    last_sweep_targets: 0,
    last_sweep_at: "2026-08-22T10:00:00Z",
    last_sweep_enqueued: 0,
  });
  assert.notEqual(neverRun, zeroTargets);
  assert.match(zeroTargets, /found no games to check/);
  assert.doesNotMatch(zeroTargets, /has not run yet/);
});

test("last_sweep_targets: 0 offers possibilities to check, never a diagnosis or a named default", () => {
  const msg = sweepTargetsMessage({ last_sweep_targets: 0, last_sweep_at: null, last_sweep_enqueued: 0 });
  assert.match(msg, /check whether/i);
  // Must not claim a specific cause as fact, and must not name a default
  // value for the cached-sweep setting (that setting's default can change
  // out from under this file — see this module's own header).
  assert.doesNotMatch(msg, /off by default/i);
  assert.doesNotMatch(msg, /no agent is installed/i);
});

test("last_sweep_targets > 0: reports the count and the enqueued count, and is a FOURTH distinct message", () => {
  const neverRun = sweepTargetsMessage({ last_sweep_targets: null, last_sweep_at: null, last_sweep_enqueued: null });
  const startedNoResult = sweepTargetsMessage({ last_sweep_targets: null, last_sweep_at: "2026-08-22T09:00:00Z", last_sweep_enqueued: null });
  const zeroTargets = sweepTargetsMessage({ last_sweep_targets: 0, last_sweep_at: null, last_sweep_enqueued: 0 });
  const hasTargets = sweepTargetsMessage({
    last_sweep_targets: 7,
    last_sweep_at: "2026-08-22T10:00:00Z",
    last_sweep_enqueued: 2,
  });
  assert.match(hasTargets, /checked 7 games and started 2 new jobs/);
  assert.notEqual(hasTargets, neverRun);
  assert.notEqual(hasTargets, startedNoResult);
  assert.notEqual(hasTargets, zeroTargets);
});

test("singular wording for exactly one game / one job", () => {
  const msg = sweepTargetsMessage({ last_sweep_targets: 1, last_sweep_at: null, last_sweep_enqueued: 1 });
  assert.match(msg, /checked 1 game and started 1 new job\./);
});

test("a malformed last_sweep_targets (wrong type) prints nothing rather than fabricating a count", () => {
  assert.equal(sweepTargetsMessage({ last_sweep_targets: "3" }), null);
  assert.equal(sweepTargetsMessage({ last_sweep_targets: NaN }), null);
});

// ---------------------------------------------------------------------
// cachedSweepGcRiskWarning — review round 1 blocker B2: the first version
// asserted present-tense ACTIVITY ("cached games ARE BEING refreshed") from
// a field that is a pure CONFIGURATION predicate, unconditional on whether
// the scheduler is even enabled. Every case below, including the
// disabled-scheduler one, must read as configuration, never activity.
// ---------------------------------------------------------------------

test("no schedule snapshot yet -> no warning", () => {
  assert.equal(cachedSweepGcRiskWarning(null), null);
  assert.equal(cachedSweepGcRiskWarning(undefined), null);
});

test("sweep_cached_gc_risk: false -> no warning", () => {
  assert.equal(cachedSweepGcRiskWarning({ sweep_cached_gc_risk: false }), null);
});

test("sweep_cached_gc_risk: true -> the warning appears, explains the mechanism, and does not claim a block or an auto-fix", () => {
  const msg = cachedSweepGcRiskWarning({ sweep_cached_gc_risk: true });
  assert.ok(msg);
  assert.match(msg, /leave its previous chunks/);
  assert.doesNotMatch(msg, /\bblocked\b/i);
  assert.doesNotMatch(msg, /automatically (turn|turns|enabl)/i);
});

test("B2 FIX: the warning is worded as configuration, never as present-tense activity", () => {
  const msg = cachedSweepGcRiskWarning({ sweep_cached_gc_risk: true });
  assert.match(msg, /is set to include cached games/i);
  assert.doesNotMatch(msg, /are being refreshed/i);
  assert.doesNotMatch(msg, /is being refreshed/i);
  assert.doesNotMatch(msg, /disk usage will grow/i); // "would grow", never asserted as certain
});

test("B2 FIX: the warning still reads as configuration-only when the scheduler has no window (enabled: false) — sweep_cached_gc_risk alone decides whether it shows, but the WORDING must not claim anything is currently running", () => {
  // sweep_cached_gc_risk is computed server-side from sweep_include_cached/
  // auto_gc alone (vault_api/scheduler.py::cached_sweep_gc_risk) — it can be
  // true even with no schedule window at all, the shipped default. A
  // sentence that says refreshing/growing is IN PROGRESS would be false in
  // exactly this case.
  const msg = cachedSweepGcRiskWarning({ enabled: false, window: null, sweep_cached_gc_risk: true });
  assert.ok(msg);
  assert.doesNotMatch(msg, /are being refreshed/i);
  assert.doesNotMatch(msg, /is being refreshed/i);
  assert.doesNotMatch(msg, /currently/i);
});

test("MUTATION PIN: only a literal boolean true triggers the warning, never a truthy non-boolean", () => {
  assert.equal(cachedSweepGcRiskWarning({ sweep_cached_gc_risk: 1 }), null);
  assert.equal(cachedSweepGcRiskWarning({ sweep_cached_gc_risk: "true" }), null);
});
