/**
 * Exponential backoff with cap + jitter (WP 4a.2).
 *
 * Used by the polling store (store.js) to back off a resource's poll loop
 * on consecutive failures and reset to the fast schedule the moment a poll
 * succeeds again — a vault-api that is briefly unreachable (container
 * restart, network blip) should not be hammered every couple of seconds,
 * but must recover immediately once it answers.
 *
 * Pure and deterministic given an injected `random` source, so the growth,
 * cap and jitter-bounds math is fully unit-testable headless (see
 * web/tests/backoff.test.js) without touching real timers.
 */

/** @typedef {{baseMs?: number, maxMs?: number, jitterRatio?: number, random?: () => number}} BackoffOptions */

const DEFAULTS = Object.freeze({
  baseMs: 1000,
  maxMs: 30000,
  jitterRatio: 0.2,
  random: Math.random,
});

/**
 * Delay (ms) for the given retry attempt (0-indexed: the delay BEFORE the
 * first retry after a failure).
 *
 * Growth is plain exponential (`baseMs * 2^attempt`), capped at `maxMs`.
 * Jitter is applied AFTER capping, as +/- `jitterRatio` of the capped value,
 * then re-clamped to `[0, maxMs]` — so the cap is a true ceiling even under
 * jitter's upward swing, and a canceled/negative swing can never produce a
 * negative delay.
 *
 * @param {number} attempt Non-negative integer retry count.
 * @param {BackoffOptions} [options]
 * @returns {number} Delay in whole milliseconds.
 */
export function computeBackoffDelay(attempt, options = {}) {
  if (!Number.isInteger(attempt) || attempt < 0) {
    throw new RangeError(`attempt must be a non-negative integer, got ${attempt}`);
  }
  const { baseMs, maxMs, jitterRatio, random } = { ...DEFAULTS, ...options };

  // Guard 2**attempt against overflow for pathologically large attempt
  // counts (a store that failed for days) — once the un-jittered value
  // would already exceed maxMs many times over, there is nothing more to
  // compute, and Math.pow(2, 1100) is Infinity, not a useful huge number.
  const exponential = attempt > 62 ? Infinity : baseMs * Math.pow(2, attempt);
  const capped = Math.min(exponential, maxMs);

  if (!(jitterRatio > 0)) return capped;

  const span = capped * jitterRatio;
  const swing = (random() * 2 - 1) * span; // in [-span, +span]
  const jittered = capped + swing;
  return Math.max(0, Math.min(maxMs, Math.round(jittered)));
}

/**
 * Stateful convenience wrapper around {@link computeBackoffDelay}: tracks
 * the attempt counter so callers don't have to.
 *
 * @param {BackoffOptions} [options]
 */
export function createBackoffState(options = {}) {
  let attempt = 0;
  return {
    /** Delay for the next failure, then increments the internal counter. */
    next() {
      const delay = computeBackoffDelay(attempt, options);
      attempt += 1;
      return delay;
    },
    /** Call on every SUCCESSFUL poll — back to the fast schedule. */
    reset() {
      attempt = 0;
    },
    get attempt() {
      return attempt;
    },
  };
}
