package dev.steamvault.app.ui.detail.logic

import dev.steamvault.app.net.model.JobSummary
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DetailJobTest {

    private fun job(id: Int, appid: Int, type: String, status: String) = JobSummary(
        id = id, appid = appid, type = type, status = status, created_at = "2026-08-01T00:00:00Z",
    )

    @Test
    fun `finds a queued prefill job for the app -- broader than the grid card's findLiveJob`() {
        val jobs = listOf(job(1, 440, "prefill", "queued"))
        assertEquals(jobs[0], findTrackedJob(jobs, 440))
    }

    @Test
    fun `finds a running and a paused prefill job`() {
        assertEquals("running", findTrackedJob(listOf(job(1, 440, "prefill", "running")), 440)?.status)
        assertEquals("paused", findTrackedJob(listOf(job(1, 440, "prefill", "paused")), 440)?.status)
    }

    @Test
    fun `ignores a GC job for the same app -- GC jobs are not job-control targets here`() {
        val jobs = listOf(job(1, 440, "gc", "running"))
        assertNull(findTrackedJob(jobs, 440))
    }

    @Test
    fun `ignores a finished job and a job for a different app`() {
        assertNull(findTrackedJob(listOf(job(1, 440, "prefill", "done")), 440))
        assertNull(findTrackedJob(listOf(job(1, 730, "prefill", "running")), 440))
    }

    @Test
    fun `job control table -- queued offers cancel only`() {
        assertEquals(setOf(DetailJobAction.CANCEL), detailJobActions(job(1, 440, "prefill", "queued")))
    }

    @Test
    fun `job control table -- running offers pause and cancel`() {
        assertEquals(setOf(DetailJobAction.PAUSE, DetailJobAction.CANCEL), detailJobActions(job(1, 440, "prefill", "running")))
    }

    @Test
    fun `job control table -- paused offers resume and cancel`() {
        assertEquals(setOf(DetailJobAction.RESUME, DetailJobAction.CANCEL), detailJobActions(job(1, 440, "prefill", "paused")))
    }

    @Test
    fun `job control table -- no job, or a finished job, offers nothing`() {
        assertEquals(emptySet<DetailJobAction>(), detailJobActions(null))
        assertEquals(emptySet<DetailJobAction>(), detailJobActions(job(1, 440, "prefill", "done")))
    }
}
