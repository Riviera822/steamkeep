package dev.steamvault.app.polling

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Growth/cap/jitter math for [Backoff], both directions of every boundary
 * (docs/LEARNINGS.md "Testing discipline") — a direct port of
 * `web/tests/backoff.test.js`'s coverage for the same defaults
 * (1 s base, 30 s cap, 20% jitter ratio), since [Backoff] is a
 * line-for-line port of `web/js/backoff.js`.
 */
class BackoffTest {

    private val noJitter = BackoffOptions(jitterRatio = 0.0)

    @Test
    fun `attempt 0 with no jitter is exactly baseMs`() {
        assertEquals(1000L, Backoff.computeDelayMs(0, noJitter))
    }

    @Test
    fun `growth is exponential -- baseMs times 2 to the attempt, with no jitter`() {
        assertEquals(1000L, Backoff.computeDelayMs(0, noJitter))
        assertEquals(2000L, Backoff.computeDelayMs(1, noJitter))
        assertEquals(4000L, Backoff.computeDelayMs(2, noJitter))
        assertEquals(8000L, Backoff.computeDelayMs(3, noJitter))
    }

    @Test
    fun `growth is capped at maxMs, not left to grow unbounded`() {
        val delay = Backoff.computeDelayMs(10, noJitter) // 1000 * 2^10 = 1,024,000 uncapped
        assertEquals(30000L, delay)
    }

    @Test
    fun `a pathologically large attempt count does not overflow to a garbage delay`() {
        // 2^63 would overflow a Long; the >62 guard routes this through
        // Double.POSITIVE_INFINITY before min() clamps it back to maxMs.
        val delay = Backoff.computeDelayMs(1000, noJitter)
        assertEquals(30000L, delay)
    }

    @Test
    fun `attempt must be non-negative`() {
        assertThrows(IllegalArgumentException::class.java) {
            Backoff.computeDelayMs(-1, noJitter)
        }
    }

    @Test
    fun `jitter never pushes the delay above maxMs, even with a maximal upward swing`() {
        val alwaysMax = BackoffOptions(jitterRatio = 0.2, random = { 1.0 }) // swing = +span, the upward extreme
        val delay = Backoff.computeDelayMs(10, alwaysMax) // capped value is already maxMs
        assertEquals(30000L, delay)
    }

    @Test
    fun `jitter never pushes the delay below zero, even with a maximal downward swing`() {
        val alwaysMin = BackoffOptions(baseMs = 100, jitterRatio = 1.0, random = { 0.0 }) // swing = -span = -capped
        val delay = Backoff.computeDelayMs(0, alwaysMin)
        assertTrue(delay >= 0)
        assertEquals(0L, delay)
    }

    @Test
    fun `jitter floor is load-bearing -- jitterRatio greater than 1 would go NEGATIVE without it`() {
        // WP 4b.2 Opus review should-fix: the test above (jitterRatio=1.0)
        // is VACUOUS for pinning the floor -- capped(100) - span(100) lands
        // on exactly 0 whether or not `max(0.0, ...)` runs at all, so
        // deleting that floor from Backoff.kt still passes it. This test
        // mirrors web/tests/backoff.test.js's "jitter never produces a
        // negative delay (floor is load-bearing)" case exactly
        // (baseMs=100, maxMs=100_000, jitterRatio=1.5, random=0, attempt=0):
        // capped=100, span=150, swing=-150, unfloored jittered=-50. Only
        // the floor turns that -50 into the 0 asserted here -- deleting
        // `max(0.0, ...)` in Backoff.kt makes this test fail.
        val options = BackoffOptions(baseMs = 100, maxMs = 100_000, jitterRatio = 1.5, random = { 0.0 })
        val delay = Backoff.computeDelayMs(0, options)
        assertEquals(0L, delay)
    }

    @Test
    fun `zero jitterRatio returns the capped value exactly, no randomness involved`() {
        val options = BackoffOptions(jitterRatio = 0.0, random = { error("must not be called when jitterRatio is 0") })
        assertEquals(4000L, Backoff.computeDelayMs(2, options))
    }

    @Test
    fun `BackoffState next increments the attempt counter and reset zeroes it`() {
        val state = BackoffState(noJitter)

        assertEquals(1000L, state.next())
        assertEquals(2000L, state.next())
        assertEquals(4000L, state.next())
        assertEquals(3, state.attempt)

        state.reset()

        assertEquals(0, state.attempt)
        assertEquals(1000L, state.next())
    }
}
