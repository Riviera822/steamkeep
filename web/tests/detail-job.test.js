/**
 * Headless tests for web/js/lib/detail-job.js (WP 4a.4).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { findTrackedJob, detailJobActions, DETAIL_JOB_ACTION } from "../js/lib/detail-job.js";

function job(overrides) {
  return { id: 1, appid: 440, type: "prefill", status: "running", ...overrides };
}

test("findTrackedJob: finds a queued/running/paused PREFILL job for the appid", () => {
  const jobs = [job({ status: "queued" })];
  assert.equal(findTrackedJob(jobs, 440), jobs[0]);
});

test("findTrackedJob: deliberately BROADER than lib/game-status.js's findLiveJob -- includes queued", () => {
  const jobs = [job({ id: 5, status: "queued" })];
  assert.equal(findTrackedJob(jobs, 440).id, 5);
});

test("findTrackedJob: excludes GC jobs -- pause/resume/download are prefill-only concepts", () => {
  const jobs = [job({ id: 9, type: "gc", status: "running" })];
  assert.equal(findTrackedJob(jobs, 440), undefined);
});

test("findTrackedJob: excludes a different appid, and a finished job", () => {
  assert.equal(findTrackedJob([job({ appid: 999 })], 440), undefined);
  assert.equal(findTrackedJob([job({ status: "done" })], 440), undefined);
});

test("findTrackedJob: handles a missing/non-array jobs snapshot", () => {
  assert.equal(findTrackedJob(undefined, 440), undefined);
  assert.equal(findTrackedJob(null, 440), undefined);
});

test("detailJobActions: mirrors api/README.md's job control table exactly", () => {
  assert.deepEqual(detailJobActions(job({ status: "queued" })), new Set([DETAIL_JOB_ACTION.CANCEL]));
  assert.deepEqual(
    detailJobActions(job({ status: "running" })),
    new Set([DETAIL_JOB_ACTION.PAUSE, DETAIL_JOB_ACTION.CANCEL]),
  );
  assert.deepEqual(
    detailJobActions(job({ status: "paused" })),
    new Set([DETAIL_JOB_ACTION.RESUME, DETAIL_JOB_ACTION.CANCEL]),
  );
});

test("detailJobActions: no actions for a finished job or no job at all", () => {
  assert.deepEqual(detailJobActions(job({ status: "done" })), new Set());
  assert.deepEqual(detailJobActions(job({ status: "error" })), new Set());
  assert.deepEqual(detailJobActions(job({ status: "cancelled" })), new Set());
  assert.deepEqual(detailJobActions(null), new Set());
  assert.deepEqual(detailJobActions(undefined), new Set());
});
