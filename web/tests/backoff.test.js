/**
 * Headless tests for web/js/backoff.js (WP 4a.2 DoD).
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { computeBackoffDelay, createBackoffState } from "../js/backoff.js";

const NO_JITTER = { jitterRatio: 0 };

test("growth: doubles per attempt with jitter disabled", () => {
  const opts = { baseMs: 100, maxMs: 1_000_000, ...NO_JITTER };
  assert.equal(computeBackoffDelay(0, opts), 100);
  assert.equal(computeBackoffDelay(1, opts), 200);
  assert.equal(computeBackoffDelay(2, opts), 400);
  assert.equal(computeBackoffDelay(3, opts), 800);
});

test("cap: never exceeds maxMs however large the attempt", () => {
  const opts = { baseMs: 100, maxMs: 1000, ...NO_JITTER };
  assert.equal(computeBackoffDelay(10, opts), 1000);
  assert.equal(computeBackoffDelay(60, opts), 1000);
  // Pathologically large attempt counts must not throw or produce NaN/Infinity
  // (2**attempt would overflow to Infinity well before this).
  assert.equal(computeBackoffDelay(500, opts), 1000);
});

test("cap holds even under jitter's upward swing", () => {
  const opts = { baseMs: 100, maxMs: 1000, jitterRatio: 0.9, random: () => 1 };
  const delay = computeBackoffDelay(10, opts); // exponential already >> maxMs
  assert.ok(delay <= 1000, `expected <= 1000, got ${delay}`);
});

test("jitter bounds: swings within +/- jitterRatio of the capped value", () => {
  // attempt=1 with base=1000 -> exponential=2000, well under maxMs, so the
  // cap does not interfere and the full jitter span is observable.
  const opts = { baseMs: 1000, maxMs: 100_000, jitterRatio: 0.5 };
  const capped = 2000;
  const span = capped * 0.5;

  const low = computeBackoffDelay(1, { ...opts, random: () => 0 });
  const mid = computeBackoffDelay(1, { ...opts, random: () => 0.5 });
  const high = computeBackoffDelay(1, { ...opts, random: () => 1 });

  assert.equal(low, capped - span);
  assert.equal(mid, capped);
  assert.equal(high, capped + span);
});

test("jitter never produces a negative delay (floor is load-bearing)", () => {
  // jitterRatio > 1 so the UNFLOORED math actually goes negative:
  // capped=100, span=150, swing=-150 -> jittered=-50. A mutant that deletes
  // the `Math.max(0, ...)` floor in backoff.js would return -50 here, not
  // merely "still happens to be >= 0" (jitterRatio=1 previously landed
  // exactly on 0 either way, which a removed floor could not be told apart
  // from — vacuous, WP 4a.2 review should-fix #1).
  const opts = { baseMs: 100, maxMs: 100_000, jitterRatio: 1.5, random: () => 0 };
  const delay = computeBackoffDelay(0, opts);
  assert.equal(delay, 0, `expected the floor to clamp to exactly 0, got ${delay}`);
});

test("rejects a negative or non-integer attempt", () => {
  assert.throws(() => computeBackoffDelay(-1), RangeError);
  assert.throws(() => computeBackoffDelay(1.5), RangeError);
  assert.throws(() => computeBackoffDelay(NaN), RangeError);
});

test("createBackoffState: next() advances the attempt counter", () => {
  const opts = { baseMs: 100, maxMs: 100_000, jitterRatio: 0 };
  const state = createBackoffState(opts);
  assert.equal(state.attempt, 0);
  assert.equal(state.next(), 100); // attempt 0
  assert.equal(state.attempt, 1);
  assert.equal(state.next(), 200); // attempt 1
  assert.equal(state.next(), 400); // attempt 2
  assert.equal(state.attempt, 3);
});

test("createBackoffState: reset() on success returns to the fast schedule", () => {
  const opts = { baseMs: 100, maxMs: 100_000, jitterRatio: 0 };
  const state = createBackoffState(opts);
  state.next();
  state.next();
  state.next();
  assert.equal(state.attempt, 3);
  state.reset();
  assert.equal(state.attempt, 0);
  assert.equal(state.next(), 100); // back to attempt-0 delay
});
