/**
 * Regression tests for WP 4a.2 review blocker B1 (store.js poll-loop
 * forking): a nudge (visibilitychange / refreshNow()) that raced an
 * in-flight fetch used to `clearTimeout` a dead timer id and start a
 * SECOND, independently self-re-arming timer chain — compounding on every
 * further nudge and, since both chains shared `ResourceLoop.prev`, firing
 * every notification event twice.
 *
 * No DOM, no jsdom, no real browser: `store.js` only reads `document`
 * lazily inside functions (never at module load), so a bare object with
 * the three members it touches is enough to run this fully in `node:test`.
 * Set BEFORE importing store.js to mirror how a real page's `<head>`
 * already has `document` by the time any module code runs — not load-
 * bearing here (see above), but keeps this test's setup honest about what
 * a real environment looks like.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 *
 * Every fake `apiClient` below now also stubs `cacheSummary` (WP 4e.6's
 * fourth loop, added to `createPollingStore` itself) — without it, each
 * `store.start()` call would immediately throw inside that loop's fetcher
 * (`apiClient.cacheSummary is not a function`), caught by `ResourceLoop`'s
 * own try/catch same as any other fetch error (so nothing here would have
 * FAILED), but it would silently start a real, backing-off retry chain this
 * file's fakes never intended to exercise. A plain `async () => null`
 * mirrors "no summary yet" — see the dedicated cache-loop tests near the
 * end of this file for what that loop actually needs to prove.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

const visibilityHandlers = [];
globalThis.document = {
  hidden: false,
  addEventListener(type, handler) {
    if (type === "visibilitychange") visibilityHandlers.push(handler);
  },
  removeEventListener(type, handler) {
    const i = visibilityHandlers.indexOf(handler);
    if (i !== -1) visibilityHandlers.splice(i, 1);
  },
};

const { createPollingStore } = await import("../js/store.js");

function fireVisibilityChange() {
  for (const handler of visibilityHandlers.slice()) handler();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** A fetcher whose promise only resolves when the test explicitly releases it. */
function makeGatedFetcher() {
  let callCount = 0;
  let release = null;
  return {
    fetcher() {
      callCount++;
      return new Promise((resolve) => {
        release = resolve;
      });
    },
    get callCount() {
      return callCount;
    },
    releaseWith(value) {
      const r = release;
      release = null;
      if (!r) throw new Error("releaseWith() called with no fetch in flight");
      r(value);
    },
  };
}

// Intervals large enough that nothing re-polls "naturally" during a test's
// short window — every extra fetcher call must come from a nudge (or a bug).
const QUIET_INTERVALS = {
  jobsFastMs: 50_000,
  jobsSlowMs: 50_000,
  gamesMs: 50_000,
  clientsMs: 50_000,
};

test("B1: nudging repeatedly while a fetch is in flight does not fork additional timer chains", async () => {
  const jobs = makeGatedFetcher();
  const apiClient = { jobs: jobs.fetcher, games: async () => [], clients: async () => [], cacheSummary: async () => null };
  const store = createPollingStore({ apiClient, intervals: QUIET_INTERVALS });

  document.hidden = false;
  store.start();
  await sleep(5); // let the immediately-scheduled first tick fire and call jobs.fetcher()
  assert.equal(jobs.callCount, 1, "expected exactly one fetch in flight before any nudge");

  // Race a burst of nudges against that first in-flight fetch — exactly
  // the visibility-toggle / pull-to-refresh storm the reviewer measured
  // compounding under (0 nudges -> 9 polls, 3 nudges -> 36, in their rig).
  for (let i = 0; i < 5; i++) {
    store.refreshNow();
    fireVisibilityChange();
  }
  await sleep(5);
  assert.equal(
    jobs.callCount,
    1,
    "nudges arriving while a fetch is in flight must not start new fetches",
  );

  jobs.releaseWith([]); // let the original fetch resolve (no active jobs)
  await sleep(5);

  // The five nudges must coalesce into exactly ONE immediate follow-up
  // poll — not zero (nudges must not be silently dropped) and not five
  // (that would be the fork, one chain per nudge).
  assert.equal(
    jobs.callCount,
    2,
    "five coalesced nudges must produce exactly one follow-up poll, not one per nudge",
  );

  jobs.releaseWith([]);
  await sleep(5);

  // Once that single follow-up settles with nothing pending, the loop must
  // go back to idle (waiting out QUIET_INTERVALS) — no further growth.
  assert.equal(jobs.callCount, 2, "no further growth once the coalesced follow-up settles");

  store.stop();
});

test("B1: a job finishing while nudges race its poll fires job_finished exactly once", async () => {
  const jobs = makeGatedFetcher();
  const apiClient = { jobs: jobs.fetcher, games: async () => [], clients: async () => [], cacheSummary: async () => null };
  const store = createPollingStore({ apiClient, intervals: QUIET_INTERVALS });

  const notifications = [];
  store.subscribe("notifications", (events) => notifications.push(...events));

  const RUNNING = {
    id: 1,
    appid: 42,
    type: "prefill",
    status: "running",
    created_at: "t0",
    started_at: "t0",
    finished_at: null,
    updated: null,
    up_to_date: null,
    summary_parse_ok: null,
    gc_execute: null,
    paused_at: null,
    stop_request: null,
  };
  const DONE = { ...RUNNING, status: "done", finished_at: "t1" };

  document.hidden = false;
  store.start();
  await sleep(5);
  assert.equal(jobs.callCount, 1);

  // Baseline poll: first poll must never itself fire a notification (see
  // notifications.test.js) — this just establishes "job 1 is running".
  jobs.releaseWith([RUNNING]);
  await sleep(5);
  assert.equal(notifications.length, 0);

  // Kick off the poll that will observe the job finishing...
  store.refreshNow();
  await sleep(5);
  assert.equal(jobs.callCount, 2, "the nudge above should have started exactly one new poll");

  // ...and race it with a burst of further nudges, exactly like a user
  // switching tabs back and forth while that poll is still in flight.
  for (let i = 0; i < 4; i++) {
    store.refreshNow();
    fireVisibilityChange();
  }

  jobs.releaseWith([DONE]);
  await sleep(5);

  const finished = notifications.filter((e) => e.type === "job_finished");
  assert.equal(
    finished.length,
    1,
    `expected exactly one job_finished notification, got ${finished.length}`,
  );

  store.stop();
});

test("nudge() before the very first poll has fired is a safe no-op (no fetch happens twice)", async () => {
  const jobs = makeGatedFetcher();
  const apiClient = { jobs: jobs.fetcher, games: async () => [], clients: async () => [], cacheSummary: async () => null };
  const store = createPollingStore({ apiClient, intervals: QUIET_INTERVALS });

  document.hidden = false;
  store.start();
  // Nudge immediately, before the scheduled first tick (delay 0) has had a
  // chance to run at all.
  store.refreshNow();
  await sleep(5);
  assert.equal(jobs.callCount, 1, "an early nudge must not double the very first poll");

  jobs.releaseWith([]);
  await sleep(5);
  store.stop();
});

// ---------------------------------------------------------------------
// "cache" resource (WP 4e.6): a single-snapshot loop with NO keyFn — the
// store.js module header explains why (no list to diff, no notification
// event). These pins are about the shape of what subscribers/snapshot()
// see, and that the no-keyFn path still gets the same race-safety
// properties (in-flight guard, generation token, backoff) every other loop
// gets from the surrounding ResourceLoop class, not from diffByKey.
// ---------------------------------------------------------------------

test('"cache" tick payload carries {item}, never a {diff} key (no keyFn — single snapshot, not a diffed list)', async () => {
  const cache = makeGatedFetcher();
  const apiClient = { jobs: async () => [], games: async () => [], clients: async () => [], cacheSummary: cache.fetcher };
  const store = createPollingStore({ apiClient, intervals: QUIET_INTERVALS });

  const ticks = [];
  store.subscribe("cache", (payload) => ticks.push(payload));

  document.hidden = false;
  store.start();
  await sleep(5);
  assert.equal(cache.callCount, 1);

  cache.releaseWith({ total_bytes: 5, free_disk_bytes: 10 });
  await sleep(5);

  assert.equal(ticks.length, 1);
  assert.deepEqual(ticks[0], { item: { total_bytes: 5, free_disk_bytes: 10 } }, "cache tick must be exactly {item: <raw summary>}");
  assert.equal("diff" in ticks[0], false, "a single-snapshot resource must never carry a diff key");

  store.stop();
});

test('store.snapshot("cache") is undefined before the first successful poll and the raw summary after one', async () => {
  const cache = makeGatedFetcher();
  const apiClient = { jobs: async () => [], games: async () => [], clients: async () => [], cacheSummary: cache.fetcher };
  const store = createPollingStore({ apiClient, intervals: QUIET_INTERVALS });

  document.hidden = false;
  assert.equal(store.snapshot("cache"), undefined, "before start(), nothing has ever been fetched");
  store.start();
  await sleep(5);
  assert.equal(store.snapshot("cache"), undefined, "the first fetch is still in flight — must stay undefined, never a fabricated placeholder");

  cache.releaseWith({ total_bytes: 42, free_disk_bytes: null });
  await sleep(5);
  assert.deepEqual(store.snapshot("cache"), { total_bytes: 42, free_disk_bytes: null });

  store.stop();
});

test('a "cache" fetch failure emits {error} and does not clear the last-known snapshot', async () => {
  let shouldFail = false;
  const apiClient = {
    jobs: async () => [],
    games: async () => [],
    clients: async () => [],
    cacheSummary: async () => {
      if (shouldFail) throw new Error("boom");
      return { total_bytes: 7, free_disk_bytes: 3 };
    },
  };
  // Real (non-QUIET) but still generous intervals: this test needs an
  // actual second tick to happen, driven by refreshNow() rather than a
  // real timer, so QUIET_INTERVALS' 50s would work identically — used here
  // too for consistency with every other test in this file.
  const store = createPollingStore({ apiClient, intervals: QUIET_INTERVALS });

  const errors = [];
  store.subscribe("cache", (payload) => {
    if (payload.error) errors.push(payload.error);
  });

  document.hidden = false;
  store.start();
  await sleep(5);
  assert.deepEqual(store.snapshot("cache"), { total_bytes: 7, free_disk_bytes: 3 });

  shouldFail = true;
  store.refreshNow();
  await sleep(5);

  assert.equal(errors.length, 1, "the failed poll must emit exactly one {error} payload");
  assert.deepEqual(
    store.snapshot("cache"),
    { total_bytes: 7, free_disk_bytes: 3 },
    "a failed poll must not clear the last-known-good snapshot",
  );

  store.stop();
});

// B1's exact race, replayed against the cache loop specifically — proves
// the in-flight guard/generation-token machinery is inherited from
// ResourceLoop for the no-keyFn path too, not accidentally specific to the
// diffed loops the original B1 tests above were written against.
test('"cache" loop: nudging repeatedly while its fetch is in flight does not fork additional timer chains', async () => {
  const cache = makeGatedFetcher();
  const apiClient = { jobs: async () => [], games: async () => [], clients: async () => [], cacheSummary: cache.fetcher };
  const store = createPollingStore({ apiClient, intervals: QUIET_INTERVALS });

  document.hidden = false;
  store.start();
  await sleep(5);
  assert.equal(cache.callCount, 1);

  for (let i = 0; i < 5; i++) {
    store.refreshNow();
    fireVisibilityChange();
  }
  await sleep(5);
  assert.equal(cache.callCount, 1, "nudges arriving while a fetch is in flight must not start new fetches");

  cache.releaseWith({ total_bytes: 1, free_disk_bytes: 1 });
  await sleep(5);
  assert.equal(cache.callCount, 2, "five coalesced nudges must produce exactly one follow-up poll, not one per nudge");

  cache.releaseWith({ total_bytes: 1, free_disk_bytes: 1 });
  await sleep(5);
  store.stop();
});

// Opus review should-fix S2 (WP 4e.6 review round): mutating store.js's
// `getIntervalMs: () => intervals.gamesMs` for the cache loop to
// `intervals.jobsFastMs` (2s in production) survived all other tests in
// this suite — none of them give the cache loop a DISTINCT cadence from
// every other interval, so nothing could tell "scheduled on the slow
// cadence" apart from "scheduled on the fast one". This is the one loop
// whose endpoint can trigger a cold, seek-bound depot walk on TTL expiry
// (SizeCache), so an accidental jobs-fast cadence would poll it 7.5x more
// often than intended — cheap insurance, named so a future refactor that
// swaps the constant is caught before anyone has to measure the walk cost
// on a real HDD vault to notice.
test('"cache" loop is scheduled on intervals.gamesMs (the slow/games cadence), never jobsFastMs/jobsSlowMs/clientsMs', async () => {
  const cache = makeGatedFetcher();
  const apiClient = { jobs: async () => [], games: async () => [], clients: async () => [], cacheSummary: cache.fetcher };
  // Every OTHER interval is deliberately huge (50s) and ONLY gamesMs is
  // short (15ms) — if the cache loop's next poll is scheduled on gamesMs,
  // a second fetch will already be in flight well within this test's
  // short real-time window; if it were scheduled on any of the other three
  // (all 50s here), it would not.
  const DISTINCT_INTERVALS = { jobsFastMs: 50_000, jobsSlowMs: 50_000, gamesMs: 15, clientsMs: 50_000 };
  const store = createPollingStore({ apiClient, intervals: DISTINCT_INTERVALS });

  // try/finally, not a bare sequence: `store.stop()` clears every loop's
  // pending timer (including the OTHER three loops' real 50s reschedules,
  // which exist regardless of which cadence the cache loop ends up on).
  // Without this, a genuine mutation making the assertion below fail would
  // throw BEFORE `store.stop()` ever ran, leaving that 50s timer alive and
  // hanging the whole test file's exit for up to 50 real seconds instead of
  // failing fast — measured live while developing this test (killed after
  // exceeding a 120s harness timeout with zero output) before this fix.
  try {
    document.hidden = false;
    store.start();
    await sleep(5);
    assert.equal(cache.callCount, 1, "expected the first cache fetch to already be in flight");
    cache.releaseWith({ total_bytes: 1, free_disk_bytes: 1 });

    await sleep(60); // >>15ms (gamesMs), <<50_000ms (every other interval)
    assert.equal(
      cache.callCount,
      2,
      "the cache loop must reschedule on intervals.gamesMs — a second fetch should already be in flight; if it used jobsFastMs/jobsSlowMs/clientsMs instead (all 50s here), none would have started yet",
    );
    cache.releaseWith({ total_bytes: 1, free_disk_bytes: 1 });
  } finally {
    store.stop();
  }
});
