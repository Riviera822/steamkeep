/**
 * Headless tests for web/js/notifications.js (WP 4a.2 DoD).
 *
 * Covers every event type in the taxonomy (job_finished, job_failed,
 * update_ready, bypass_suspected, bypass_resolved), the no-change case, and
 * the first-poll case ("first poll must NOT fire a notification storm" —
 * called out explicitly in the WP brief).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  diffJobsForNotifications,
  diffGamesForNotifications,
  diffClientsForNotifications,
  diffSnapshotsForNotifications,
} from "../js/notifications.js";

function job(overrides) {
  return {
    id: 1,
    appid: 42,
    type: "prefill",
    status: "running",
    created_at: "2026-08-10T00:00:00Z",
    started_at: "2026-08-10T00:00:01Z",
    finished_at: null,
    updated: null,
    up_to_date: null,
    summary_parse_ok: null,
    gc_execute: null,
    paused_at: null,
    stop_request: null,
    ...overrides,
  };
}

function game(overrides) {
  return {
    appid: 42,
    name: "Aurora Cascade",
    status: "done",
    last_prefill_at: "2026-08-10T00:00:00Z",
    depot_count: 1,
    size_bytes: 1000,
    needs_force: false,
    ...overrides,
  };
}

function client(overrides) {
  return {
    client_id: "workshop-pc",
    first_seen: "2026-08-01T00:00:00Z",
    last_reported_at: "2026-08-10T00:00:00Z",
    app_count: 10,
    source_addrs: ["10.10.0.21"],
    cache_hits: 5,
    cache_misses: 1,
    bytes_served: 1234,
    last_seen_in_cache_log: "2026-08-10T00:00:00Z",
    bypass_suspected: false,
    ...overrides,
  };
}

// ---------------------------------------------------------------------
// First poll: no baseline to compare against => zero events, however
// alarming curr looks on its own (done jobs, stale games, bypassed clients
// already present the first time the app ever polls).
// ---------------------------------------------------------------------

test("first poll never fires a notification storm (jobs)", () => {
  const curr = [job({ id: 1, status: "done" }), job({ id: 2, status: "error" })];
  assert.deepEqual(diffJobsForNotifications(undefined, curr), []);
  assert.deepEqual(diffJobsForNotifications(null, curr), []);
});

test("first poll never fires a notification storm (games)", () => {
  const curr = [game({ status: "stale" })];
  assert.deepEqual(diffGamesForNotifications(undefined, curr), []);
});

test("first poll never fires a notification storm (clients)", () => {
  const curr = [client({ bypass_suspected: true })];
  assert.deepEqual(diffClientsForNotifications(undefined, curr), []);
});

test("first poll never fires across the combined helper either", () => {
  const events = diffSnapshotsForNotifications({
    prevJobs: undefined,
    currJobs: [job({ id: 1, status: "done" })],
    prevGames: undefined,
    currGames: [game({ status: "stale" })],
    prevClients: undefined,
    currClients: [client({ bypass_suspected: true })],
  });
  assert.deepEqual(events, []);
});

// ---------------------------------------------------------------------
// No-change case
// ---------------------------------------------------------------------

test("no-change poll produces zero events", () => {
  const jobs = [job({ id: 1, status: "running" })];
  const games = [game({ status: "done" })];
  const clients = [client({ bypass_suspected: false })];
  assert.deepEqual(diffJobsForNotifications(jobs, jobs.map((j) => ({ ...j }))), []);
  assert.deepEqual(diffGamesForNotifications(games, games.map((g) => ({ ...g }))), []);
  assert.deepEqual(diffClientsForNotifications(clients, clients.map((c) => ({ ...c }))), []);
});

// ---------------------------------------------------------------------
// job_finished
// ---------------------------------------------------------------------

test("job_finished: an active job transitioning to done fires exactly one event", () => {
  const prev = [job({ id: 1, status: "running" })];
  const curr = [job({ id: 1, status: "done", finished_at: "2026-08-10T01:00:00Z" })];
  const events = diffJobsForNotifications(prev, curr);
  assert.equal(events.length, 1);
  assert.deepEqual(events[0], {
    type: "job_finished",
    key: "job:1:done",
    jobId: 1,
    appid: 42,
    jobType: "prefill",
  });
});

test("job_finished: a queued job that finished between two polls (added, not updated) still fires", () => {
  // Simulates a very short-lived job: absent on the previous poll, already
  // 'done' on this one (diffByKey puts it in `added`, not `updated`).
  const prev = [job({ id: 99, status: "running" })]; // some unrelated other job
  const curr = [job({ id: 99, status: "running" }), job({ id: 1, status: "done" })];
  const events = diffJobsForNotifications(prev, curr);
  assert.equal(events.length, 1);
  assert.equal(events[0].type, "job_finished");
  assert.equal(events[0].jobId, 1);
});

test("job_finished: a job that was ALREADY done does not re-fire on an unrelated update", () => {
  const prev = [job({ id: 1, status: "done", updated: 1 })];
  const curr = [job({ id: 1, status: "done", updated: 1 })]; // identical -> 'unchanged', not 'updated'
  assert.deepEqual(diffJobsForNotifications(prev, curr), []);
});

test("a job aging out of the GET /v1/jobs limit (removed, not updated) fires no event", () => {
  // GET /v1/jobs?limit=20 (api/README.md) is a bounded window, newest
  // first — an old job simply falling off the end on a later poll is
  // NOT a state transition and must not be reported as one (there is no
  // "job_removed" in the taxonomy on purpose).
  const prev = [job({ id: 1, status: "done" }), job({ id: 2, status: "running" })];
  const curr = [job({ id: 2, status: "running" })]; // id 1 aged out of the window
  assert.deepEqual(diffJobsForNotifications(prev, curr), []);
});

// ---------------------------------------------------------------------
// job_failed
// ---------------------------------------------------------------------

test("job_failed: an active job transitioning to error fires exactly one event", () => {
  const prev = [job({ id: 5, status: "running" })];
  const curr = [job({ id: 5, status: "error" })];
  const events = diffJobsForNotifications(prev, curr);
  assert.equal(events.length, 1);
  assert.deepEqual(events[0], {
    type: "job_failed",
    key: "job:5:error",
    jobId: 5,
    appid: 42,
    jobType: "prefill",
  });
});

test("job_failed: fires from 'queued' too (never started before failing)", () => {
  const prev = [job({ id: 5, status: "queued" })];
  const curr = [job({ id: 5, status: "error" })];
  const events = diffJobsForNotifications(prev, curr);
  assert.equal(events.length, 1);
  assert.equal(events[0].type, "job_failed");
});

test("job_failed: fires from 'paused' too (shutdown-during-pause edge case)", () => {
  const prev = [job({ id: 5, status: "paused" })];
  const curr = [job({ id: 5, status: "error" })];
  const events = diffJobsForNotifications(prev, curr);
  assert.equal(events.length, 1);
  assert.equal(events[0].type, "job_failed");
});

// ---------------------------------------------------------------------
// cancelled is deliberately silent (an operator's own action, not news)
// ---------------------------------------------------------------------

test("a job cancelled by the operator produces no event", () => {
  const prev = [job({ id: 7, status: "running" })];
  const curr = [job({ id: 7, status: "cancelled" })];
  assert.deepEqual(diffJobsForNotifications(prev, curr), []);
});

// ---------------------------------------------------------------------
// update_ready
// ---------------------------------------------------------------------

test("update_ready: a game turning stale fires exactly one event", () => {
  const prev = [game({ status: "done" })];
  const curr = [game({ status: "stale" })];
  const events = diffGamesForNotifications(prev, curr);
  assert.equal(events.length, 1);
  assert.deepEqual(events[0], {
    type: "update_ready",
    key: "game:42:stale",
    appid: 42,
    name: "Aurora Cascade",
  });
});

test("update_ready: does NOT fire for a 'stale' game with zero cache content (NOTES finding 6)", () => {
  // "Stale" requires cache content — a status rule, not a cosmetic. A
  // zero-byte app must never be reported as needing an update, even if
  // some upstream bug hands the differ a stale status for it.
  const prev = [game({ status: "done", size_bytes: 1000 })];
  const curr = [game({ status: "stale", size_bytes: 0 })];
  assert.deepEqual(diffGamesForNotifications(prev, curr), []);
});

test("update_ready: does NOT fire for a 'stale' game with null size_bytes", () => {
  const prev = [game({ status: "done", size_bytes: 1000 })];
  const curr = [game({ status: "stale", size_bytes: null })];
  assert.deepEqual(diffGamesForNotifications(prev, curr), []);
});

test("update_ready: does not fire for a game that was already stale", () => {
  const prev = [game({ status: "stale" })];
  const curr = [game({ status: "stale", size_bytes: 2000 })]; // some other field changed
  assert.deepEqual(diffGamesForNotifications(prev, curr), []);
});

test("update_ready: does not fire for status changes that aren't 'stale'", () => {
  const prev = [game({ status: "idle" })];
  const curr = [game({ status: "running" })];
  assert.deepEqual(diffGamesForNotifications(prev, curr), []);
});

// ---------------------------------------------------------------------
// bypass_suspected / bypass_resolved
// ---------------------------------------------------------------------

test("bypass_suspected: false -> true fires exactly one event", () => {
  const prev = [client({ bypass_suspected: false })];
  const curr = [client({ bypass_suspected: true })];
  const events = diffClientsForNotifications(prev, curr);
  assert.equal(events.length, 1);
  assert.deepEqual(events[0], {
    type: "bypass_suspected",
    key: "client:workshop-pc:suspected",
    clientId: "workshop-pc",
  });
});

test("bypass_resolved: true -> false fires exactly one event (symmetric transition)", () => {
  const prev = [client({ bypass_suspected: true })];
  const curr = [client({ bypass_suspected: false })];
  const events = diffClientsForNotifications(prev, curr);
  assert.equal(events.length, 1);
  assert.deepEqual(events[0], {
    type: "bypass_resolved",
    key: "client:workshop-pc:resolved",
    clientId: "workshop-pc",
  });
});

test("bypass state holding steady (true->true or false->false) fires nothing", () => {
  const suspectedBoth = [client({ bypass_suspected: true, cache_hits: 1 })];
  const suspectedBothChanged = [client({ bypass_suspected: true, cache_hits: 2 })];
  assert.deepEqual(diffClientsForNotifications(suspectedBoth, suspectedBothChanged), []);

  const okBoth = [client({ bypass_suspected: false })];
  assert.deepEqual(diffClientsForNotifications(okBoth, okBoth.map((c) => ({ ...c }))), []);
});

// ---------------------------------------------------------------------
// diffSnapshotsForNotifications: combines all three, order jobs/games/clients
// ---------------------------------------------------------------------

test("combined helper concatenates events from all three domains", () => {
  const events = diffSnapshotsForNotifications({
    prevJobs: [job({ id: 1, status: "running" })],
    currJobs: [job({ id: 1, status: "done" })],
    prevGames: [game({ status: "done" })],
    currGames: [game({ status: "stale" })],
    prevClients: [client({ bypass_suspected: false })],
    currClients: [client({ bypass_suspected: true })],
  });
  assert.equal(events.length, 3);
  assert.deepEqual(
    events.map((e) => e.type),
    ["job_finished", "update_ready", "bypass_suspected"],
  );
});
