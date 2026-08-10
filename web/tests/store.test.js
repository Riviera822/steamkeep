/**
 * Headless tests for the pure decision helpers in web/js/store.js
 * (hasActiveJob / nextJobsIntervalMs). The store's timer/DOM-visibility
 * wiring is glue code and is NOT covered here — see the header comment in
 * web/js/store.js and the WP 4a.2 report for why.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { hasActiveJob, nextJobsIntervalMs, DEFAULT_INTERVALS } from "../js/store.js";

test("hasActiveJob: true for queued, running or paused", () => {
  assert.equal(hasActiveJob([{ status: "queued" }]), true);
  assert.equal(hasActiveJob([{ status: "running" }]), true);
  assert.equal(hasActiveJob([{ status: "paused" }]), true);
});

test("hasActiveJob: false for only terminal jobs, or an empty/missing list", () => {
  assert.equal(hasActiveJob([{ status: "done" }, { status: "error" }, { status: "cancelled" }]), false);
  assert.equal(hasActiveJob([]), false);
  assert.equal(hasActiveJob(undefined), false);
  assert.equal(hasActiveJob(null), false);
});

test("nextJobsIntervalMs: fast while any job is active", () => {
  const jobs = [{ status: "done" }, { status: "running" }];
  assert.equal(nextJobsIntervalMs(jobs, DEFAULT_INTERVALS), DEFAULT_INTERVALS.jobsFastMs);
});

test("nextJobsIntervalMs: slow once nothing is active (or on the first-ever, empty poll)", () => {
  assert.equal(
    nextJobsIntervalMs([{ status: "done" }], DEFAULT_INTERVALS),
    DEFAULT_INTERVALS.jobsSlowMs,
  );
  assert.equal(nextJobsIntervalMs([], DEFAULT_INTERVALS), DEFAULT_INTERVALS.jobsSlowMs);
});
