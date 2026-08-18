/**
 * Headless tests for web/js/lib/cached-prefill-outcome.js (WP 4c-web).
 *
 * This module imports only errors.js (no `window`, `document` or `fetch`)
 * so it runs directly in bare Node.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  partitionCachedPrefillOutcome,
  summarizeCachedPrefillOutcome,
  describeCachedPrefillError,
  countForcedCachedGames,
  createCheckAndUpdateAction,
} from "../js/lib/cached-prefill-outcome.js";
import { ApiError, ERROR_KINDS } from "../js/errors.js";

// ---------------------------------------------------------------------
// partitionCachedPrefillOutcome
// ---------------------------------------------------------------------

test("partitionCachedPrefillOutcome sorts a mixed response into the four dedupe-shape buckets", () => {
  const refs = [
    { appid: 1, job_id: 101, status: "queued", deduplicated: false },
    { appid: 2, job_id: 102, status: "queued", deduplicated: true },
    { appid: 3, job_id: 103, status: "running", deduplicated: true },
    { appid: 4, job_id: 104, status: "paused", deduplicated: true },
    { appid: 5, job_id: 105, status: "queued", deduplicated: false },
  ];
  const p = partitionCachedPrefillOutcome(refs);
  assert.deepEqual(p.queued.map((r) => r.appid), [1, 5]);
  assert.deepEqual(p.alreadyQueued.map((r) => r.appid), [2]);
  assert.deepEqual(p.alreadyRunning.map((r) => r.appid), [3]);
  assert.deepEqual(p.alreadyPaused.map((r) => r.appid), [4]);
  assert.equal(p.total, 5);
});

test("partitionCachedPrefillOutcome: empty selection", () => {
  const p = partitionCachedPrefillOutcome([]);
  assert.deepEqual(p.queued, []);
  assert.deepEqual(p.alreadyQueued, []);
  assert.deepEqual(p.alreadyRunning, []);
  assert.deepEqual(p.alreadyPaused, []);
  assert.equal(p.total, 0);
});

test("partitionCachedPrefillOutcome: non-array input treated as empty (defensive)", () => {
  assert.equal(partitionCachedPrefillOutcome(null).total, 0);
  assert.equal(partitionCachedPrefillOutcome(undefined).total, 0);
});

// S1 (review round 1): a deduplicated entry that is STILL "queued" (the
// single worker has not claimed it yet — `enqueue_prefill` returns the
// existing job with ITS OWN status, and this is the common shape a
// double-press produces, not an edge case) must land in its OWN bucket,
// distinct from a genuinely running job.
test("partitionCachedPrefillOutcome: a deduplicated QUEUED entry lands in alreadyQueued, not alreadyRunning", () => {
  const p = partitionCachedPrefillOutcome([
    { appid: 9, job_id: 109, status: "queued", deduplicated: true },
  ]);
  assert.deepEqual(p.alreadyQueued.map((r) => r.appid), [9]);
  assert.deepEqual(p.alreadyRunning, []);
});

// A `deduplicated: true` entry with a status that is none of the three
// documented in-flight statuses (only "queued"/"running"/"paused" are
// contractually possible, but the contract says "never a terminal status")
// must still land somewhere honest rather than vanish from every bucket,
// same posture as job-partition.js's "unknown status routes to history,
// not oblivion".
test("partitionCachedPrefillOutcome: a deduplicated non-queued/non-paused status lands in alreadyRunning", () => {
  const p = partitionCachedPrefillOutcome([
    { appid: 5, job_id: 105, status: "some_future_status", deduplicated: true },
  ]);
  assert.deepEqual(p.alreadyRunning.map((r) => r.appid), [5]);
  assert.deepEqual(p.alreadyPaused, []);
  assert.deepEqual(p.alreadyQueued, []);
});

// ---------------------------------------------------------------------
// summarizeCachedPrefillOutcome — the mutation-worthy pins the WP brief
// asks for, plus the review round 1 blocker regression pins.
// ---------------------------------------------------------------------

test("summarizeCachedPrefillOutcome: empty selection reads as a normal outcome, not a failure", () => {
  const summary = summarizeCachedPrefillOutcome([]);
  assert.equal(summary.message, "Nothing cached to check.");
  assert.equal(summary.warn, false);
  // Mutation pin: the message must not contain failure-shaped words.
  assert.ok(!/fail|error|nothing happened/i.test(summary.message));
});

// BLOCKER REGRESSION (review round 1, live-reproduced in headless Chrome):
// an empty response used to still get a "(N forced...)" note appended from
// the caller's OWN games snapshot, regardless of what the server actually
// queued — "Nothing cached to check. (1 forced...)" claims work that
// provably did not start. The fix moved the note INSIDE this function,
// gated on `partition.queued.length > 0`.
test("BLOCKER REGRESSION: empty response + a stale needs_force game in `games` ⇒ message is EXACTLY 'Nothing cached to check.'", () => {
  const games = [{ appid: 2010070, size_bytes: 2_400_000_000, needs_force: true }];
  const summary = summarizeCachedPrefillOutcome([], games);
  assert.equal(summary.message, "Nothing cached to check.");
  assert.equal(summary.warn, false);
});

// BLOCKER REGRESSION, second shape: an ALL-DEDUPLICATED outcome (nothing
// queued fresh — every selected app already had an in-flight job) must not
// credit a forced note either, even when one of the deduplicated apps
// happens to carry `needs_force: true` in the caller's games snapshot —
// that app's force decision was made whenever its existing job was first
// queued, not by this press.
test("BLOCKER REGRESSION: all-deduplicated response + a forced game among them ⇒ no forced note", () => {
  const games = [{ appid: 2010030, size_bytes: 1_100_000_000, needs_force: true }];
  const refs = [{ appid: 2010030, job_id: 900001, status: "running", deduplicated: true }];
  const summary = summarizeCachedPrefillOutcome(refs, games);
  assert.equal(summary.message, "1 already in progress");
  assert.ok(!/forced/i.test(summary.message));
});

test("summarizeCachedPrefillOutcome: all-new selection uses the check-&-update wording (S2)", () => {
  const summary = summarizeCachedPrefillOutcome([
    { appid: 1, job_id: 1, status: "queued", deduplicated: false },
    { appid: 2, job_id: 2, status: "queued", deduplicated: false },
  ]);
  assert.equal(summary.message, "2 queued for check & update");
  assert.equal(summary.warn, false);
});

test("summarizeCachedPrefillOutcome: a QUEUED dedupe is worded 'already queued', distinct from 'already in progress' (S1)", () => {
  const summary = summarizeCachedPrefillOutcome([
    { appid: 9, job_id: 109, status: "queued", deduplicated: true },
  ]);
  assert.equal(summary.message, "1 already queued");
  assert.ok(!/in progress/i.test(summary.message), "a job still waiting in the FIFO queue is not 'in progress'");
  // `warn` means "the user must go DO something", which is true of a paused job
  // and of nothing else. The shipped expression is already paused-only, but it
  // was unpinned: widening it to OR in alreadyQueued passed all 414 tests,
  // silently promoting this outcome to a warning styling and the 6 s toast
  // duration library.js derives from `warn`. Review round 2 found that gap.
  assert.equal(summary.warn, false);
});

test("summarizeCachedPrefillOutcome: a paused dedupe is NEVER worded as queued/started (THE pin)", () => {
  const summary = summarizeCachedPrefillOutcome([
    { appid: 3, job_id: 103, status: "paused", deduplicated: true },
  ]);
  // Must mention it needs action, must NOT claim it was queued/started.
  assert.ok(/paused/i.test(summary.message));
  assert.ok(!/queued for check/i.test(summary.message), "a paused entry must not be described as queued");
  assert.ok(!/already queued/i.test(summary.message), "a paused entry must not be described as already queued");
  assert.ok(!/\bstarted\b/i.test(summary.message), "a paused entry must not be described as started");
  assert.equal(summary.warn, true);
});

test("summarizeCachedPrefillOutcome: multiple paused entries use plural wording", () => {
  const summary = summarizeCachedPrefillOutcome([
    { appid: 3, job_id: 3, status: "paused", deduplicated: true },
    { appid: 4, job_id: 4, status: "paused", deduplicated: true },
  ]);
  assert.match(summary.message, /2 paused — resume or cancel them first/);
});

test("summarizeCachedPrefillOutcome: already-running only, no warn", () => {
  const summary = summarizeCachedPrefillOutcome([
    { appid: 2, job_id: 2, status: "running", deduplicated: true },
  ]);
  assert.equal(summary.message, "1 already in progress");
  assert.equal(summary.warn, false);
});

test("summarizeCachedPrefillOutcome: mixed outcome reports every bucket distinctly", () => {
  const summary = summarizeCachedPrefillOutcome([
    { appid: 1, job_id: 1, status: "queued", deduplicated: false },
    { appid: 2, job_id: 2, status: "queued", deduplicated: true },
    { appid: 3, job_id: 3, status: "running", deduplicated: true },
    { appid: 4, job_id: 4, status: "paused", deduplicated: true },
  ]);
  assert.match(summary.message, /1 queued for check & update/);
  assert.match(summary.message, /1 already queued/);
  assert.match(summary.message, /1 already in progress/);
  assert.match(summary.message, /1 paused/);
  assert.equal(summary.warn, true);
});

test("summarizeCachedPrefillOutcome: forced note appears when a NEWLY queued app carries needs_force", () => {
  const games = [{ appid: 1, size_bytes: 100, needs_force: true }];
  const summary = summarizeCachedPrefillOutcome(
    [{ appid: 1, job_id: 1, status: "queued", deduplicated: false }],
    games,
  );
  assert.match(summary.message, /1 queued for check & update \(1 forced/);
});

test("summarizeCachedPrefillOutcome: no `games` argument at all never throws and simply omits the note", () => {
  const summary = summarizeCachedPrefillOutcome([
    { appid: 1, job_id: 1, status: "queued", deduplicated: false },
  ]);
  assert.equal(summary.message, "1 queued for check & update");
});

// ---------------------------------------------------------------------
// countForcedCachedGames — now scoped to a `queuedRefs` bucket, not the
// whole games snapshot (review round 1 blocker fix).
// ---------------------------------------------------------------------

// MUTATION PIN — the COMPOSITION, not the helper. countForcedCachedGames has
// its own scoping test below, but nothing pinned that summarize() passes the
// `queued` bucket rather than the whole games snapshot: swapping the argument
// for `games` passed the entire suite, which is the surviving half of the
// round-1 blocker (the gate on a non-empty queued bucket masks it). The
// Android twin found this in review (WP 4c-app) and pinned it there; this is
// the same pin on this side, so the two frontends cannot drift apart again.
// Redundant defence layers cannot be pinned by one end-to-end test — see
// docs/LEARNINGS.md.
test("summarizeCachedPrefillOutcome: the forced note counts ONLY freshly queued apps, never the whole snapshot", () => {
  const summary = summarizeCachedPrefillOutcome(
    [{ appid: 1, job_id: 11, status: "queued", deduplicated: false }],
    [
      { appid: 1, size_bytes: 5_000_000, needs_force: false },
      { appid: 2, size_bytes: 9_000_000, needs_force: true },
    ],
  );
  assert.equal(summary.message, "1 queued for check & update");
  assert.ok(!/forced/i.test(summary.message), "an unrelated forced app in the snapshot must not be credited to this press");
});

test("countForcedCachedGames counts only appids present in queuedRefs AND needs_force in games", () => {
  const queuedRefs = [
    { appid: 1, job_id: 1, status: "queued", deduplicated: false },
    { appid: 2, job_id: 2, status: "queued", deduplicated: false },
  ];
  const games = [
    { appid: 1, size_bytes: 100, needs_force: true },
    { appid: 2, size_bytes: 100, needs_force: false },
    { appid: 3, size_bytes: 100, needs_force: true }, // not in queuedRefs — excluded
  ];
  assert.equal(countForcedCachedGames(queuedRefs, games), 1);
});

test("countForcedCachedGames: an appid in queuedRefs with no matching game is not counted", () => {
  assert.equal(countForcedCachedGames([{ appid: 999, job_id: 1, status: "queued", deduplicated: false }], []), 0);
});

test("countForcedCachedGames: empty/missing input is 0", () => {
  assert.equal(countForcedCachedGames([], []), 0);
  assert.equal(countForcedCachedGames(null, null), 0);
});

// ---------------------------------------------------------------------
// describeCachedPrefillError — the mid-loop 5xx honesty rule.
// ---------------------------------------------------------------------

test("describeCachedPrefillError: a SERVER-kind error asks the caller to re-read jobs (THE pin)", () => {
  const err = new ApiError(ERROR_KINDS.SERVER, "boom", { status: 500 });
  const desc = describeCachedPrefillError(err);
  assert.equal(desc.refresh, true);
  assert.equal(desc.warn, true);
  // Mutation pin: must not imply nothing happened.
  assert.ok(!/nothing happened/i.test(desc.message));
});

test("describeCachedPrefillError: every non-SERVER kind does not force a refresh", () => {
  for (const kind of [ERROR_KINDS.AUTH, ERROR_KINDS.VALIDATION, ERROR_KINDS.NOT_FOUND, ERROR_KINDS.NETWORK, ERROR_KINDS.UNKNOWN]) {
    const err = new ApiError(kind, "nope", { status: 401, detail: "denied" });
    const desc = describeCachedPrefillError(err);
    assert.equal(desc.refresh, false, `kind ${kind} must not force a refresh`);
  }
});

test("describeCachedPrefillError: prefers the server's detail text when present", () => {
  const err = new ApiError(ERROR_KINDS.VALIDATION, "generic", { status: 422, detail: "specific reason" });
  const desc = describeCachedPrefillError(err);
  assert.equal(desc.message, "specific reason");
});

test("describeCachedPrefillError: never throws on a non-ApiError input", () => {
  const desc = describeCachedPrefillError(new Error("plain"));
  assert.equal(desc.refresh, false);
  assert.equal(desc.message, "plain");
});

// ---------------------------------------------------------------------
// createCheckAndUpdateAction — the in-flight guard, DOM-free (THE pin).
//
// Review round 1, S4: the FIRST version of the "no-op while pending" test
// below awaited the second `run()` call before ever resolving the first
// fetch — under the mutation (guard removed), the second `run()` calls
// `fetcher()` again and awaits the SAME never-yet-resolved deferred, so the
// whole suite hung indefinitely instead of failing (the reviewer needed
// `--test-timeout=5000` to observe the kill at all). Fixed by asserting the
// call count SYNCHRONOUSLY, before either `run()` promise is awaited and
// before the deferred is resolved: the guard check happens synchronously
// inside `run()`'s pre-`await` prefix, so `calls` is already deterministic
// (1 if the guard is intact, 2 if it was removed) the instant both `run()`
// calls have been issued — no awaiting required to observe the mutation,
// so a broken guard now fails FAST instead of hanging.
// ---------------------------------------------------------------------

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

test("createCheckAndUpdateAction: a second run() while the first is pending is a no-op (fails fast, does not hang)", async () => {
  let calls = 0;
  const first = deferred();
  const action = createCheckAndUpdateAction({
    fetcher: () => {
      calls += 1;
      return first.promise;
    },
  });

  const runOnePromise = action.run(); // starts the fetch, does not resolve yet
  assert.equal(action.isInFlight(), true);

  const runTwoPromise = action.run(); // must NOT call fetcher again

  // Synchronous assertion — see the module-header note above for why this
  // must happen BEFORE resolving `first` or awaiting either promise.
  assert.equal(calls, 1, "fetcher must be called exactly once while the first call is pending");

  first.resolve(["ok"]);
  const [resultOne, resultTwo] = await Promise.all([runOnePromise, runTwoPromise]);
  assert.deepEqual(resultTwo, { skipped: true });
  assert.deepEqual(resultOne, { skipped: false, ok: true, refs: ["ok"] });
  assert.equal(action.isInFlight(), false);
});

test("createCheckAndUpdateAction: a run AFTER the previous one settled calls the fetcher again", async () => {
  let calls = 0;
  const action = createCheckAndUpdateAction({
    fetcher: async () => {
      calls += 1;
      return [];
    },
  });
  await action.run();
  await action.run();
  assert.equal(calls, 2);
});

test("createCheckAndUpdateAction: a rejected fetch clears the in-flight flag and reports ok:false", async () => {
  const boom = new Error("network down");
  const action = createCheckAndUpdateAction({ fetcher: () => Promise.reject(boom) });
  const result = await action.run();
  assert.deepEqual(result, { skipped: false, ok: false, err: boom });
  assert.equal(action.isInFlight(), false);
  // The guard must be usable again immediately after a failure.
  let calls = 0;
  const action2 = createCheckAndUpdateAction({
    fetcher: async () => {
      calls += 1;
      return [];
    },
  });
  await action2.run();
  assert.equal(calls, 1);
});
