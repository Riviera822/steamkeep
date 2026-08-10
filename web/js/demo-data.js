/**
 * Demo-mode fixtures (WP 4a.2).
 *
 * Serves the app entirely from in-memory, synthetic data so it works with
 * no vault present (NOTES open question 5: "real value for first-run and
 * screenshots"). Shapes are modeled 1:1 on the real vault-api responses
 * documented in api/README.md's "Endpoints" table and the Pydantic models
 * in api/vault_api/routers/{games,jobs,clients}.py — field names match
 * exactly, so api.js's callers and the polling store cannot tell demo mode
 * and a real server apart.
 *
 * Per docs/LEARNINGS.md ("Testing discipline": fixtures are synthetic,
 * modeled on real structure, never personal data): every title, id and
 * client name below is fictional; none of it is real Steam data.
 *
 * No network access of any kind (CSP compatibility, WP 4a.2 scope) — this
 * is local state, reset on every page load, not a mock HTTP server.
 *
 * Testability: this module imports only errors.js (no `window`, no
 * `document`, no `fetch`) — it is plain data plus plain functions and runs
 * in bare Node. `resetDemoData()` restores the module's mutable state to a
 * fresh copy of the seed data; tests call it between cases so scenarios
 * (e.g. "delete this game") don't leak into the next test (see
 * web/tests/demo-data.test.js).
 */

import { ApiError, ERROR_KINDS } from "./errors.js";

function isoAgo(msAgo) {
  return new Date(Date.now() - msAgo).toISOString();
}

// ---------------------------------------------------------------------
// Seed data
// ---------------------------------------------------------------------

function makeGame({ appid, name, status, needsForce = false, depots = [] }) {
  return {
    appid,
    name,
    status,
    last_prefill_at: status === "idle" ? null : isoAgo(3 * 3_600_000),
    needs_force: needsForce,
    // Doubles as both "mapping rows" and "cache state" for this depot in the
    // demo model (the real vault-api keeps those two facts separately —
    // api/README.md "Per-game deletion": deletion clears cache content, not
    // mapping rows). Simplification accepted for this WP: see the DELETE
    // /v1/cache/{appid} handler below for where that would matter and how
    // it is approximated.
    depots,
  };
}

function buildGames() {
  return [
    makeGame({
      appid: 2010010,
      name: "Aurora Cascade",
      status: "done",
      depots: [{ depotid: 2010011, shared: false, size_bytes: 4_200_000_000 }],
    }),
    makeGame({
      appid: 2010020,
      name: "Copper Horizon",
      status: "idle",
      needsForce: true,
      depots: [],
    }),
    makeGame({
      appid: 2010030,
      name: "Driftwood Signal",
      status: "running",
      depots: [{ depotid: 2010031, shared: false, size_bytes: 1_100_000_000 }],
    }),
    makeGame({
      appid: 2010040,
      name: "Emberreach",
      status: "done",
      depots: [
        { depotid: 2010041, shared: false, size_bytes: 800_000_000 },
        // Shared with Frostline Convoy (2010050) below — deleting Emberreach
        // alone must SKIP this depot (Frostline is still cached), and
        // deleting Frostline afterward must skip it right back (Emberreach
        // is still cached) — the "sole holder" case only fires once both
        // are gone. See web/tests/demo-data.test.js.
        { depotid: 2010060, shared: true, size_bytes: 300_000_000 },
      ],
    }),
    makeGame({
      appid: 2010050,
      name: "Frostline Convoy",
      status: "done",
      depots: [{ depotid: 2010060, shared: true, size_bytes: 300_000_000 }],
    }),
    makeGame({
      appid: 2010070,
      name: "Glass Meridian",
      status: "error",
      needsForce: true,
      depots: [{ depotid: 2010071, shared: false, size_bytes: 2_400_000_000 }],
    }),
  ];
}

function buildJobs() {
  return [
    {
      id: 900001,
      appid: 2010030,
      type: "prefill",
      status: "running",
      created_at: isoAgo(60_000),
      started_at: isoAgo(45_000),
      finished_at: null,
      updated: null,
      up_to_date: null,
      summary_parse_ok: null,
      gc_execute: null,
      paused_at: null,
      stop_request: null,
      log_excerpt:
        "[vault-api] worker claimed job 900001\nDownloading depot 2010031 ...",
      // Demo-only bookkeeping (never serialized into a response): how many
      // more GET /v1/jobs polls before this job "finishes". Call-count
      // driven rather than wall-clock so demo mode needs no timers of its
      // own.
      _demoTicksLeft: 3,
    },
    {
      id: 900000,
      appid: 2010070,
      type: "prefill",
      status: "error",
      created_at: isoAgo(3 * 3_600_000),
      started_at: isoAgo(3 * 3_600_000 - 5_000),
      finished_at: isoAgo(3 * 3_600_000 - 1_000),
      updated: 0,
      up_to_date: 0,
      summary_parse_ok: true,
      gc_execute: null,
      paused_at: null,
      stop_request: null,
      log_excerpt: "[vault-api] SteamPrefill exited 1 — see attached log.",
    },
    {
      id: 899999,
      appid: 2010010,
      type: "prefill",
      status: "done",
      created_at: isoAgo(6 * 3_600_000),
      started_at: isoAgo(6 * 3_600_000 - 5_000),
      finished_at: isoAgo(6 * 3_600_000 - 1_000),
      updated: 12,
      up_to_date: 0,
      summary_parse_ok: true,
      gc_execute: null,
      paused_at: null,
      stop_request: null,
      log_excerpt: "[vault-api] worker claimed job 899999\nPrefilled 12 apps.",
    },
  ];
}

function buildClients() {
  return [
    {
      client_id: "workshop-pc",
      first_seen: isoAgo(30 * 86_400_000),
      last_reported_at: isoAgo(3_600_000),
      app_count: 148,
      source_addrs: ["10.10.0.21"],
      cache_hits: 4213,
      cache_misses: 96,
      bytes_served: 812_345_678_912,
      last_seen_in_cache_log: isoAgo(120_000),
      bypass_suspected: false,
    },
    {
      client_id: "loft-laptop",
      first_seen: isoAgo(9 * 86_400_000),
      last_reported_at: isoAgo(7_200_000),
      app_count: 42,
      source_addrs: ["10.10.0.44"],
      cache_hits: 3,
      cache_misses: 1,
      bytes_served: 41_943_040,
      last_seen_in_cache_log: null,
      bypass_suspected: true,
    },
  ];
}

let games = buildGames();
let jobs = buildJobs();
let clients = buildClients();
let nextJobId = 900002;

/** Restore all demo state to a fresh copy of the seed data. Exported for
 * tests (web/tests/demo-data.test.js) so scenarios don't leak between
 * cases; the real app never calls this — a page reload does the same
 * thing by re-importing the module. */
export function resetDemoData() {
  games = buildGames();
  jobs = buildJobs();
  clients = buildClients();
  nextJobId = 900002;
}

// ---------------------------------------------------------------------
// Projections (internal seed shape -> exact wire shape)
// ---------------------------------------------------------------------

function appSizeBytes(depots) {
  if (depots.length === 0) return null; // unmapped
  return depots.reduce((sum, d) => sum + (d.size_bytes ?? 0), 0);
}

function gameSummary(g) {
  return {
    appid: g.appid,
    name: g.name,
    status: g.status,
    last_prefill_at: g.last_prefill_at,
    depot_count: g.depots.length,
    size_bytes: appSizeBytes(g.depots),
    needs_force: g.needs_force,
  };
}

function gameDetail(g) {
  return {
    appid: g.appid,
    name: g.name,
    status: g.status,
    last_prefill_at: g.last_prefill_at,
    depots: g.depots.map(({ depotid, shared, size_bytes }) => ({
      depotid,
      shared,
      size_bytes,
    })),
    size_bytes: appSizeBytes(g.depots),
    needs_force: g.needs_force,
  };
}

function jobSummary(j) {
  const {
    id,
    appid,
    type,
    status,
    created_at,
    started_at,
    finished_at,
    updated,
    up_to_date,
    summary_parse_ok,
    gc_execute,
    paused_at,
    stop_request,
  } = j;
  return {
    id,
    appid,
    type,
    status,
    created_at,
    started_at,
    finished_at,
    updated,
    up_to_date,
    summary_parse_ok,
    gc_execute,
    paused_at,
    stop_request,
  };
}

function jobDetail(j) {
  return { ...jobSummary(j), log_excerpt: j.log_excerpt };
}

// ---------------------------------------------------------------------
// Demo-only simulation
// ---------------------------------------------------------------------

function findGame(appid) {
  return games.find((g) => g.appid === appid);
}
function findJob(id) {
  return jobs.find((j) => j.id === id);
}

/** Advance one running job a step closer to "done" (see _demoTicksLeft above). */
function advanceJob(job) {
  if (job.status !== "running" || typeof job._demoTicksLeft !== "number") return;
  job._demoTicksLeft -= 1;
  if (job._demoTicksLeft > 0) return;

  delete job._demoTicksLeft;
  job.status = "done";
  job.finished_at = new Date().toISOString();
  job.updated = 1;
  job.up_to_date = 0;
  job.summary_parse_ok = true;
  job.log_excerpt += "\n[vault-api] worker: Prefilled 1 app.";

  const game = findGame(job.appid);
  if (game) {
    game.status = "done";
    game.last_prefill_at = job.finished_at;
    game.needs_force = false;
  }
}

function tickAllJobs() {
  for (const job of jobs) advanceJob(job);
}

// ---------------------------------------------------------------------
// Request routing
// ---------------------------------------------------------------------

function notFound(detail) {
  return new ApiError(ERROR_KINDS.NOT_FOUND, detail, { status: 404, detail });
}
function validationError(detail) {
  return new ApiError(ERROR_KINDS.VALIDATION, detail, { status: 422, detail });
}
function conflict(detail) {
  return new ApiError(ERROR_KINDS.VALIDATION, detail, { status: 409, detail });
}

const JOB_ID_RE = /^\/v1\/jobs\/(\d+)$/;
const JOB_PAUSE_RE = /^\/v1\/jobs\/(\d+)\/pause$/;
const JOB_RESUME_RE = /^\/v1\/jobs\/(\d+)\/resume$/;
const GAME_ID_RE = /^\/v1\/games\/(\d+)$/;
const CACHE_APPID_RE = /^\/v1\/cache\/(\d+)$/;

function jobControlResponse(job, { status, outcome, detail }) {
  job.status = status;
  return { job_id: job.id, status, outcome, detail };
}

/**
 * Handle one request against the demo dataset, mirroring the same
 * (data | throw ApiError) contract api.js's real `request()` offers, so
 * every caller above it is indifferent to which mode is active.
 *
 * @param {string} method
 * @param {string} path
 * @param {{body?: unknown, params?: Record<string, unknown>}} [opts]
 */
export async function demoRequest(method, path, { body, params } = {}) {
  if (method === "GET" && path === "/v1/health") {
    return { status: "ok" };
  }

  if (method === "GET" && path === "/v1/games") {
    return games.map(gameSummary);
  }

  let m;
  if (method === "GET" && (m = path.match(GAME_ID_RE))) {
    const appid = Number(m[1]);
    const game = findGame(appid);
    if (!game) throw notFound(`Unknown appid ${appid}`);
    return gameDetail(game);
  }

  if (method === "GET" && path === "/v1/jobs") {
    tickAllJobs();
    const limit = Number(params?.limit ?? 20);
    return jobs
      .slice()
      .sort((a, b) => b.id - a.id)
      .slice(0, limit)
      .map(jobSummary);
  }

  if (method === "GET" && (m = path.match(JOB_ID_RE))) {
    tickAllJobs();
    const id = Number(m[1]);
    const job = findJob(id);
    if (!job) throw notFound(`Unknown job id ${id}`);
    return jobDetail(job);
  }

  if (method === "POST" && path === "/v1/prefill") {
    const appids = Array.isArray(body?.appids) ? body.appids : [];
    if (appids.length === 0) throw validationError("appids must be a non-empty list");
    // All-or-nothing, BEFORE any job is created: real body validation is a
    // Pydantic model (PrefillRequest, api/vault_api/routers/jobs.py) that
    // rejects the whole request if any appid fails `AppId`'s `>= 1`
    // constraint — a 422 must never leave a partial side effect behind
    // (WP 4a.2 review fix: one bad id among several good ones used to still
    // queue a job for the good ones before the throw).
    for (const appid of appids) {
      if (!Number.isInteger(appid) || appid < 1) {
        throw validationError(`invalid appid ${appid}`);
      }
    }
    return appids.map((appid) => {
      const existing = jobs.find(
        (j) => j.appid === appid && ["queued", "running", "paused"].includes(j.status),
      );
      if (existing) {
        return { appid, job_id: existing.id, status: existing.status, deduplicated: true };
      }
      const game = findGame(appid);
      const job = {
        id: nextJobId++,
        appid,
        type: "prefill",
        status: "queued",
        created_at: new Date().toISOString(),
        started_at: null,
        finished_at: null,
        updated: null,
        up_to_date: null,
        summary_parse_ok: null,
        gc_execute: null,
        paused_at: null,
        stop_request: null,
        log_excerpt: "[vault-api] queued.",
        _demoTicksLeft: 3,
      };
      jobs.unshift(job);
      if (!game) {
        games.push(makeGame({ appid, name: `App ${appid}`, status: "idle", depots: [] }));
      }
      // First tick already flips it to "running" so a demo poll shortly
      // after enqueueing sees visible progress, matching the mockup's
      // "job start" transition rather than sitting at "queued" forever
      // (this module has no real worker draining a queue).
      job.status = "running";
      job.started_at = job.created_at;
      return { appid, job_id: job.id, status: job.status, deduplicated: false };
    });
  }

  if (method === "DELETE" && (m = path.match(JOB_ID_RE))) {
    const id = Number(m[1]);
    const job = findJob(id);
    if (!job) throw notFound(`Unknown job id ${id}`);
    if (["done", "error", "cancelled"].includes(job.status)) {
      throw conflict(`Job ${id} already finished`);
    }
    const outcome = job.status === "running" ? "requested" : "immediate";
    // Demo simplification: there is no separate background worker to defer
    // to, so a "requested" cancel of a running job settles immediately
    // rather than on some later tick. It MUST settle to 'cancelled', not
    // keep counting down toward 'done' (WP 4a.2 review fix: a cancelled
    // job used to keep ticking to completion in tickAllJobs() and fire the
    // very job_finished notification cancellation is supposed to suppress
    // — see notifications.js's "cancelled is deliberately silent").
    delete job._demoTicksLeft;
    job.finished_at = new Date().toISOString();
    return jobControlResponse(job, {
      status: "cancelled",
      outcome,
      detail:
        outcome === "requested"
          ? "Cancellation requested; poll GET /v1/jobs/{id}."
          : "Job cancelled.",
    });
  }

  if (method === "POST" && (m = path.match(JOB_PAUSE_RE))) {
    const id = Number(m[1]);
    const job = findJob(id);
    if (!job) throw notFound(`Unknown job id ${id}`);
    if (job.type !== "prefill" || job.status !== "running") {
      throw conflict(`Job ${id} cannot be paused from status ${job.status}`);
    }
    delete job._demoTicksLeft;
    job.paused_at = new Date().toISOString();
    return jobControlResponse(job, {
      status: "paused",
      outcome: "requested",
      detail: "Pause requested; poll GET /v1/jobs/{id}.",
    });
  }

  if (method === "POST" && (m = path.match(JOB_RESUME_RE))) {
    const id = Number(m[1]);
    const job = findJob(id);
    if (!job) throw notFound(`Unknown job id ${id}`);
    if (job.status !== "paused") throw conflict(`Job ${id} is not paused`);
    job.status = "running";
    job._demoTicksLeft = 2;
    return jobControlResponse(job, {
      status: "queued",
      outcome: "resumed",
      detail: "Job resumed at the front of the queue.",
    });
  }

  if (method === "DELETE" && (m = path.match(CACHE_APPID_RE))) {
    const appid = Number(m[1]);
    const game = findGame(appid);
    if (!game || game.depots.length === 0) {
      throw notFound(`App ${appid} has no depot mappings, so there is nothing to delete.`);
    }
    const activeJob = jobs.find(
      (j) => j.appid === appid && ["queued", "running", "paused"].includes(j.status),
    );
    if (activeJob) {
      const label = activeJob.type === "gc" ? "GC" : "Prefill";
      throw conflict(`${label} job ${activeJob.id} for app ${appid} is ${activeJob.status}.`);
    }

    // Mirrors api/vault_api/routers/cache.py's CacheDeletionOut exactly:
    //   deleted_depots:  {depotid, size_bytes_freed, shared_with_uncached}
    //   skipped_shared:  {depotid, shared_with}
    //   failed:          {depotid, error}   (never populated here — this
    //                     demo model has no filesystem to fail against)
    //
    // ADR-0003 shared-depot protection (the part the previous version of
    // this handler skipped entirely): a depot mapped by another game that
    // currently HAS cache content is never deleted — it is reported in
    // skipped_shared instead. A depot whose every other mapping is
    // currently uncached (a "last cached remnant") IS deleted, flagged via
    // shared_with_uncached rather than merged with an ordinary exclusive
    // deletion. Re-derived per depot against the CURRENT (not
    // request-start) state, same as the real endpoint's execute-time
    // recheck (`current_co_owners` there).
    //
    // "Has cache content" mirrors the real predicate exactly
    // (`deletion._has_cache_content`): it is a STATUS check
    // (status/last_prefill_at/active-job), not a live disk scan — a
    // depot's bytes being physically still present on a co-owner's shared
    // mapping does NOT by itself count, because `DELETE /v1/cache/{appid}`
    // unconditionally resets that app's own status to 'idle' and
    // last_prefill_at to null even when everything it mapped was
    // protected shared content. This is what actually lets a shared depot
    // become a "last cached remnant" on a LATER call — a depots-array-only
    // proxy (a co-owner's own retained mapping to the very depot being
    // evaluated) can never reach that state, since it is trivially always
    // "true" for the depot in question.
    function hasCacheContent(g) {
      const hasActiveJob = jobs.some(
        (j) => j.appid === g.appid && ["queued", "running", "paused"].includes(j.status),
      );
      const idle = g.status === "idle";
      const neverPrefilled = g.last_prefill_at === null;
      return !(idle && neverPrefilled && !hasActiveJob);
    }
    function otherOwners(depotid) {
      return games.filter(
        (g) => g.appid !== appid && g.depots.some((d) => d.depotid === depotid),
      );
    }

    const deletedDepots = [];
    const skippedShared = [];
    const remnantCoOwnerAppids = new Set();

    for (const depot of game.depots) {
      const others = otherOwners(depot.depotid);
      const cachedOthers = others.filter(hasCacheContent);
      if (cachedOthers.length > 0) {
        skippedShared.push({ depotid: depot.depotid, shared_with: others.map((g) => g.appid) });
        continue;
      }
      deletedDepots.push({
        depotid: depot.depotid,
        size_bytes_freed: depot.size_bytes ?? 0,
        shared_with_uncached: others.map((g) => g.appid),
      });
      for (const owner of others) remnantCoOwnerAppids.add(owner.appid);
    }

    // The bytes are actually gone from disk for EVERY game that mapped a
    // deleted depot, not just the one this request targeted — this demo
    // model keeps "mapping" and "on-disk size" in one list (see the
    // makeGame() note above) rather than the real schema's two separate
    // facts, so a deleted depot is dropped from every game's depot list,
    // not only the requested app's.
    const deletedIds = new Set(deletedDepots.map((d) => d.depotid));
    for (const g of games) {
      g.depots = g.depots.filter((d) => !deletedIds.has(d.depotid));
    }
    for (const coOwnerAppid of remnantCoOwnerAppids) {
      const coOwner = findGame(coOwnerAppid);
      if (coOwner) coOwner.needs_force = true;
    }

    // Real endpoint: `new_status = 'error' if failed else 'idle'`,
    // unconditionally — even a request that only hit skipped_shared (every
    // mapped depot protected, nothing actually removed) still resets to
    // 'idle', and last_prefill_at is cleared either way. This demo never
    // produces a filesystem failure, so it is always 'idle' here.
    game.status = "idle";
    game.last_prefill_at = null;
    // Real rule (ADR-0006 decision 2): set only when something in
    // deleted_depots or failed actually changed/left uncertain what's on
    // disk for THIS app; left untouched when everything mapped was
    // protected shared content (nothing exclusive to touch).
    if (deletedDepots.length > 0) game.needs_force = true;

    const totalBytesFreed = deletedDepots.reduce((sum, d) => sum + d.size_bytes_freed, 0);

    return {
      appid,
      deleted_depots: deletedDepots,
      skipped_shared: skippedShared,
      failed: [],
      total_bytes_freed: totalBytesFreed,
    };
  }

  if (method === "GET" && path === "/v1/cache/summary") {
    const allDepots = new Map();
    for (const g of games) for (const d of g.depots) allDepots.set(d.depotid, d.size_bytes ?? 0);
    const totalBytes = [...allDepots.values()].reduce((a, b) => a + b, 0);
    const topConsumers = games
      .map((g) => ({ appid: g.appid, name: g.name, size_bytes: appSizeBytes(g.depots) ?? 0 }))
      .sort((a, b) => b.size_bytes - a.size_bytes)
      .slice(0, 10);
    return {
      total_bytes: totalBytes,
      top_consumers: topConsumers,
      unmapped_depots: { count: 0, size_bytes: 0 },
      free_disk_bytes: 500_000_000_000,
    };
  }

  if (method === "GET" && path === "/v1/clients") {
    return clients.map((c) => ({ ...c }));
  }

  throw new ApiError(ERROR_KINDS.NOT_FOUND, `Demo mode has no route for ${method} ${path}`, {
    status: 404,
    detail: `${method} ${path}`,
  });
}
