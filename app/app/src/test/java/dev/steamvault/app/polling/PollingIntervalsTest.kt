package dev.steamvault.app.polling

import dev.steamvault.app.net.model.JobSummary
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * [PollingIntervals]' cadence decisions, mirroring
 * `web/tests/store.test.js`'s coverage of `hasActiveJob`/
 * `nextJobsIntervalMs` for the same three active statuses
 * (`queued`/`running`/`paused`).
 */
class PollingIntervalsTest {

    private fun job(status: String, id: Int = 1) = JobSummary(
        id = id,
        appid = 440,
        type = "prefill",
        status = status,
        created_at = "2026-08-10T10:00:00Z",
    )

    @Test
    fun `no jobs means no active job`() {
        assertFalse(PollingIntervals.hasActiveJob(emptyList()))
    }

    @Test
    fun `queued running and paused each count as active`() {
        assertTrue(PollingIntervals.hasActiveJob(listOf(job("queued"))))
        assertTrue(PollingIntervals.hasActiveJob(listOf(job("running"))))
        assertTrue(PollingIntervals.hasActiveJob(listOf(job("paused"))))
    }

    @Test
    fun `done error and cancelled do not count as active`() {
        assertFalse(PollingIntervals.hasActiveJob(listOf(job("done"))))
        assertFalse(PollingIntervals.hasActiveJob(listOf(job("error"))))
        assertFalse(PollingIntervals.hasActiveJob(listOf(job("cancelled"))))
    }

    @Test
    fun `one active job among several finished ones is enough`() {
        val jobs = listOf(job("done", 1), job("error", 2), job("running", 3))
        assertTrue(PollingIntervals.hasActiveJob(jobs))
    }

    @Test
    fun `nextJobsIntervalMs is fast while a job is active`() {
        assertEquals(
            PollingIntervals.JOBS_FAST_MS,
            PollingIntervals.nextJobsIntervalMs(listOf(job("running"))),
        )
    }

    @Test
    fun `nextJobsIntervalMs is slow once nothing is active, including the empty case`() {
        assertEquals(
            PollingIntervals.JOBS_SLOW_MS,
            PollingIntervals.nextJobsIntervalMs(listOf(job("done"))),
        )
        assertEquals(
            PollingIntervals.JOBS_SLOW_MS,
            PollingIntervals.nextJobsIntervalMs(emptyList()),
        )
    }

    @Test
    fun `default cadence constants match the web store exactly`() {
        // web/js/store.js DEFAULT_INTERVALS, hand-transcribed -- same
        // cross-frontend literal-pin reasoning as the status-icon and
        // error-taxonomy contract tests, not derived from this object.
        assertEquals(2000L, PollingIntervals.JOBS_FAST_MS)
        assertEquals(15000L, PollingIntervals.JOBS_SLOW_MS)
        assertEquals(15000L, PollingIntervals.GAMES_MS)
        assertEquals(20000L, PollingIntervals.CLIENTS_MS)
    }
}
