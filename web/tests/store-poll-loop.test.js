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
  const apiClient = { jobs: jobs.fetcher, games: async () => [], clients: async () => [] };
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
  const apiClient = { jobs: jobs.fetcher, games: async () => [], clients: async () => [] };
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
  const apiClient = { jobs: jobs.fetcher, games: async () => [], clients: async () => [] };
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
