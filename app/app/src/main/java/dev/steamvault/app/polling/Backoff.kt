package dev.steamvault.app.polling

import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.random.Random

/**
 * Exponential backoff with cap + jitter — a direct port of
 * `web/js/backoff.js`'s `computeBackoffDelay`/`createBackoffState` (same
 * defaults: 1 s base, 30 s cap, 20% jitter ratio) so a briefly-unreachable
 * vault-api is not hammered by either frontend, and both recover to the
 * fast schedule the instant a poll succeeds again. Pure and deterministic
 * given an injected random source — see `BackoffTest` for the growth/cap/
 * jitter math pinned in both directions (docs/LEARNINGS.md "Testing
 * discipline").
 */
data class BackoffOptions(
    val baseMs: Long = 1000,
    val maxMs: Long = 30000,
    val jitterRatio: Double = 0.2,
    val random: () -> Double = Random.Default::nextDouble,
)

object Backoff {
    /**
     * Delay (ms) for the given retry [attempt] (0-indexed: the delay
     * BEFORE the first retry after a failure).
     *
     * Growth is plain exponential (`baseMs * 2^attempt`), capped at
     * `maxMs`. Jitter is applied AFTER capping, as +/- `jitterRatio` of the
     * CAPPED value, then re-clamped to `[0, maxMs]` — the same order of
     * operations as web/js/backoff.js, so the cap is a true ceiling even
     * under jitter's upward swing, and a downward swing can never produce
     * a negative delay.
     */
    fun computeDelayMs(attempt: Int, options: BackoffOptions = BackoffOptions()): Long {
        require(attempt >= 0) { "attempt must be a non-negative integer, got $attempt" }

        // Guard 2^attempt against overflow for a pathologically large
        // attempt count (a store that failed for days) — once the
        // un-jittered value would already exceed maxMs many times over,
        // there is nothing more to compute (same reasoning as
        // web/js/backoff.js's Math.pow guard).
        val exponential =
            if (attempt > 62) Double.POSITIVE_INFINITY else options.baseMs * 2.0.pow(attempt)
        val capped = min(exponential, options.maxMs.toDouble())

        if (!(options.jitterRatio > 0)) return capped.toLong()

        val span = capped * options.jitterRatio
        val swing = (options.random() * 2 - 1) * span // in [-span, +span]
        val jittered = capped + swing
        return max(0.0, min(options.maxMs.toDouble(), Math.round(jittered).toDouble())).toLong()
    }
}

/** Stateful convenience wrapper around [Backoff.computeDelayMs]: tracks the attempt counter. */
class BackoffState(private val options: BackoffOptions = BackoffOptions()) {
    var attempt: Int = 0
        private set

    /** Delay for the next failure, then increments the internal counter. */
    fun next(): Long {
        val delay = Backoff.computeDelayMs(attempt, options)
        attempt += 1
        return delay
    }

    /** Call on every SUCCESSFUL poll — back to the fast schedule. */
    fun reset() {
        attempt = 0
    }
}
