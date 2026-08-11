/**
 * Headless tests for web/js/demo-data.js's WP 4a.4 additions:
 * `apps.last_manifest_check` on the games routes, and the
 * `POST /v1/cache/{appid}/gc` demo route.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { demoRequest, resetDemoData } from "../js/demo-data.js";
import { parseGcLogSummary } from "../js/lib/gc-log-summary.js";

beforeEach(() => {
  resetDemoData();
});

// Seed ids from demo-data.js's buildGames():
const AURORA = 2010010; // done, gcReclaimableBytes: 120_000_000
const COPPER = 2010020; // idle, needsForce, last_manifest_check set, last_prefill_at null
const FROSTLINE = 2010050; // done, last_manifest_check + last_prefill_at both set
const DRIFTWOOD = 2010030; // running (has an active prefill job)
const GLASS_MERIDIAN = 2010070; // error status

// ---------------------------------------------------------------------
// last_manifest_check on GET /v1/games and GET /v1/games/{appid}
// ---------------------------------------------------------------------

test("GET /v1/games exposes last_manifest_check per api/vault_api/routers/games.py's GameSummary", async () => {
  const games = await demoRequest("GET", "/v1/games");
  const byId = new Map(games.map((g) => [g.appid, g]));

  // Ordinary "done" game that was never confirmed by a non-forced run —
  // NEVER_CONFIRMED even though status is "done" (honest per api/README.md
  // "Job outcome honesty": an ordinary run that changed depots leaves it null).
  assert.equal(byId.get(AURORA).last_manifest_check, null);

  // Post-deletion shape: last_manifest_check survives, last_prefill_at does not.
  assert.equal(byId.get(COPPER).last_prefill_at, null);
  assert.notEqual(byId.get(COPPER).last_manifest_check, null);

  // The ordinary CONFIRMED case: both present.
  assert.notEqual(byId.get(FROSTLINE).last_prefill_at, null);
  assert.notEqual(byId.get(FROSTLINE).last_manifest_check, null);
});

test("GET /v1/games/{appid} carries the same last_manifest_check value as the list", async () => {
  const games = await demoRequest("GET", "/v1/games");
  const detail = await demoRequest("GET", `/v1/games/${COPPER}`);
  const summary = games.find((g) => g.appid === COPPER);
  assert.equal(detail.last_manifest_check, summary.last_manifest_check);
});

// ---------------------------------------------------------------------
// POST /v1/cache/{appid}/gc
// ---------------------------------------------------------------------

test("dry run is the default: omitting the body queues execute=false, mode dry-run", async () => {
  const ref = await demoRequest("POST", `/v1/cache/${AURORA}/gc`);
  assert.equal(ref.execute, false);
  assert.equal(ref.mode, "dry-run");
  assert.equal(ref.type, "gc");
  assert.equal(ref.appid, AURORA);
  assert.equal(typeof ref.job_id, "number");
  assert.equal(ref.deduplicated, false);
});

test("{execute: true} queues an executing job", async () => {
  const ref = await demoRequest("POST", `/v1/cache/${AURORA}/gc`, { body: { execute: true } });
  assert.equal(ref.execute, true);
  assert.equal(ref.mode, "execute");
});

test("404 for an unknown appid", async () => {
  await assert.rejects(() => demoRequest("POST", "/v1/cache/999999/gc"), (err) => {
    assert.equal(err.status, 404);
    return true;
  });
});

test("404 for an app with no depot mappings", async () => {
  // Copper Horizon has depots: [] in the seed data.
  await assert.rejects(() => demoRequest("POST", `/v1/cache/${COPPER}/gc`), (err) => {
    assert.equal(err.status, 404);
    return true;
  });
});

test("422 for an unrecognised body field", async () => {
  await assert.rejects(
    () => demoRequest("POST", `/v1/cache/${AURORA}/gc`, { body: { exceute: true } }),
    (err) => {
      assert.equal(err.status, 422);
      return true;
    },
  );
});

test("422 for a non-boolean execute (StrictBool posture — no lax coercion)", async () => {
  await assert.rejects(
    () => demoRequest("POST", `/v1/cache/${AURORA}/gc`, { body: { execute: "true" } }),
    (err) => {
      assert.equal(err.status, 422);
      return true;
    },
  );
});

test("no 409 for an active prefill job — GC is queued regardless (matches the real endpoint's documented non-guard)", async () => {
  // Demo simplification, same as POST /v1/prefill above: there is no
  // separate background worker to defer to, so the job is immediately
  // ticked to "running" rather than sitting at "queued" — the load-bearing
  // assertion here is that this call does NOT reject with a 409 at all.
  const ref = await demoRequest("POST", `/v1/cache/${DRIFTWOOD}/gc`);
  assert.equal(ref.status, "running");
});

test("dedupe is scoped to the SAME mode: a second dry-run call while one is queued returns the existing job", async () => {
  const first = await demoRequest("POST", `/v1/cache/${GLASS_MERIDIAN}/gc`);
  const second = await demoRequest("POST", `/v1/cache/${GLASS_MERIDIAN}/gc`);
  assert.equal(second.deduplicated, true);
  assert.equal(second.job_id, first.job_id);
});

test("a dry run and an execute run never dedupe into each other", async () => {
  const dryRun = await demoRequest("POST", `/v1/cache/${GLASS_MERIDIAN}/gc`);
  const executeRun = await demoRequest("POST", `/v1/cache/${GLASS_MERIDIAN}/gc`, { body: { execute: true } });
  assert.equal(executeRun.deduplicated, false);
  assert.notEqual(executeRun.job_id, dryRun.job_id);
});

test("a dry-run job ticks to done with a REAL 'GC totals (DRY RUN)' log line lib/gc-log-summary.js can parse", async () => {
  const ref = await demoRequest("POST", `/v1/cache/${AURORA}/gc`);
  let job;
  for (let i = 0; i < 5 && (!job || job.status !== "done"); i++) {
    job = await demoRequest("GET", `/v1/jobs/${ref.job_id}`);
  }
  assert.equal(job.status, "done");
  assert.equal(job.type, "gc");
  assert.equal(job.gc_execute, false);
  // Aurora Cascade was seeded with gcReclaimableBytes: 120_000_000.
  assert.match(job.log_excerpt, /GC totals \(DRY RUN\)/);
  const summary = parseGcLogSummary(job.log_excerpt);
  assert.equal(summary.wouldDeleteCount, 1);
  assert.equal(summary.wouldDeleteBytes, 120_000_000);
});

test("a game with nothing reclaimable reports would_delete=0", async () => {
  // Frostline Convoy has no gcReclaimableBytes override -> defaults to 0.
  const ref = await demoRequest("POST", `/v1/cache/${FROSTLINE}/gc`);
  let job;
  for (let i = 0; i < 5 && (!job || job.status !== "done"); i++) {
    job = await demoRequest("GET", `/v1/jobs/${ref.job_id}`);
  }
  const summary = parseGcLogSummary(job.log_excerpt);
  assert.equal(summary.wouldDeleteCount, 0);
  assert.equal(summary.wouldDeleteBytes, 0);
});

test("execute run frees the reclaimable bytes and a subsequent dry run reports nothing left", async () => {
  const executeRef = await demoRequest("POST", `/v1/cache/${AURORA}/gc`, { body: { execute: true } });
  let executeJob;
  for (let i = 0; i < 5 && (!executeJob || executeJob.status !== "done"); i++) {
    executeJob = await demoRequest("GET", `/v1/jobs/${executeRef.job_id}`);
  }
  assert.match(executeJob.log_excerpt, /GC totals \(EXECUTED\)/);
  const executedSummary = parseGcLogSummary(executeJob.log_excerpt);
  assert.equal(executedSummary.chunksRemoved, 1);
  assert.equal(executedSummary.bytesFreed, 120_000_000);
  assert.equal(executedSummary.totalBytesFreed, 120_000_000);

  // GC never touches apps.status/last_prefill_at (api/README.md "Garbage
  // collection": "What a GC job does to app state: nothing").
  const gameAfter = await demoRequest("GET", `/v1/games/${AURORA}`);
  assert.equal(gameAfter.status, "done");

  const dryRunRef = await demoRequest("POST", `/v1/cache/${AURORA}/gc`);
  let dryRunJob;
  for (let i = 0; i < 5 && (!dryRunJob || dryRunJob.status !== "done"); i++) {
    dryRunJob = await demoRequest("GET", `/v1/jobs/${dryRunRef.job_id}`);
  }
  const dryRunSummary = parseGcLogSummary(dryRunJob.log_excerpt);
  assert.equal(dryRunSummary.wouldDeleteCount, 0);
});

// ---------------------------------------------------------------------
// WP 4a.8: demo GC log seeds extended to the FULL real key set (both
// DRY RUN and EXECUTED lines, per api/vault_api/gc_execute.py's
// GcRunReport.log_text literals) so demo mode exercises the production
// parse path including `held_back` — before this WP the demo log
// hardcoded `held_back=0 (0 bytes)` in every scenario.
// ---------------------------------------------------------------------

test("a dry run reports held_back separately from would_delete (full key-set log line)", async () => {
  // Glass Meridian seeded with gcReclaimableBytes: 40_000_000,
  // gcHeldBackBytes: 15_000_000 (demo-data.js buildGames()).
  const ref = await demoRequest("POST", `/v1/cache/${GLASS_MERIDIAN}/gc`);
  let job;
  for (let i = 0; i < 5 && (!job || job.status !== "done"); i++) {
    job = await demoRequest("GET", `/v1/jobs/${ref.job_id}`);
  }
  assert.equal(job.status, "done");
  assert.match(job.log_excerpt, /GC totals \(DRY RUN\)/);
  // The full literal key set from gc_execute.py's log_text — orphans is the
  // SUM of would_delete + held_back, not just would_delete.
  assert.match(job.log_excerpt, /orphans=2 \(55000000 bytes\)/);
  assert.match(job.log_excerpt, /reclaimable_dedupe_bytes=0/);
  assert.match(job.log_excerpt, /planned_depots=\[2010071\]/);
  const summary = parseGcLogSummary(job.log_excerpt);
  assert.equal(summary.wouldDeleteCount, 1);
  assert.equal(summary.wouldDeleteBytes, 40_000_000);
  assert.equal(summary.heldBackCount, 1);
  assert.equal(summary.heldBackBytes, 15_000_000);
});

test("an execute run also reports held_back, and does NOT clear it on a follow-up dry run", async () => {
  const executeRef = await demoRequest("POST", `/v1/cache/${GLASS_MERIDIAN}/gc`, { body: { execute: true } });
  let executeJob;
  for (let i = 0; i < 5 && (!executeJob || executeJob.status !== "done"); i++) {
    executeJob = await demoRequest("GET", `/v1/jobs/${executeRef.job_id}`);
  }
  assert.match(executeJob.log_excerpt, /GC totals \(EXECUTED\)/);
  // The full literal key set from gc_execute.py's log_text.
  assert.match(executeJob.log_excerpt, /already_gone=0/);
  assert.match(executeJob.log_excerpt, /dedupe_removed=0 dedupe_bytes_freed=0/);
  assert.match(executeJob.log_excerpt, /problems=0 declined=0/);
  assert.match(executeJob.log_excerpt, /depots_touched=\[2010071\]/);
  assert.match(executeJob.log_excerpt, new RegExp(`needs_force_set_for=\\[${GLASS_MERIDIAN}\\]`));
  const executedSummary = parseGcLogSummary(executeJob.log_excerpt);
  assert.equal(executedSummary.chunksRemoved, 1);
  assert.equal(executedSummary.bytesFreed, 40_000_000);
  assert.equal(executedSummary.totalBytesFreed, 40_000_000);
  assert.equal(executedSummary.heldBackCount, 1);
  assert.equal(executedSummary.heldBackBytes, 15_000_000);

  const dryRunRef = await demoRequest("POST", `/v1/cache/${GLASS_MERIDIAN}/gc`);
  let dryRunJob;
  for (let i = 0; i < 5 && (!dryRunJob || dryRunJob.status !== "done"); i++) {
    dryRunJob = await demoRequest("GET", `/v1/jobs/${dryRunRef.job_id}`);
  }
  const dryRunSummary = parseGcLogSummary(dryRunJob.log_excerpt);
  // The reclaimable bytes were collected by the execute run above; the
  // held-back bytes were NOT — a time rule an execute run does not touch
  // (gc_execute.py: "time is a policy on top of [the plan], not part of
  // it"; finishGcJob's module header records the same choice for demo).
  assert.equal(dryRunSummary.wouldDeleteCount, 0);
  assert.equal(dryRunSummary.heldBackCount, 1);
  assert.equal(dryRunSummary.heldBackBytes, 15_000_000);
});
