package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.status.StatusKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private fun game(
    appid: Int = 1,
    status: String = "idle",
    lastPrefillAt: String? = null,
    sizeBytes: Long? = null,
    depotCount: Int = 0,
): GameSummary = GameSummary(
    appid = appid,
    name = "Game $appid",
    status = status,
    last_prefill_at = lastPrefillAt,
    last_manifest_check = null,
    depot_count = depotCount,
    size_bytes = sizeBytes,
    needs_force = false,
)

private fun job(
    appid: Int = 1,
    type: String = "prefill",
    status: String = "running",
    id: Int = 1,
): JobSummary = JobSummary(
    id = id,
    appid = appid,
    type = type,
    status = status,
    created_at = "2026-08-01T00:00:00Z",
)

class GameStatusTest {

    // ---- findLiveJob / indexLiveJobsByAppid ----------------------------

    @Test
    fun `findLiveJob matches a running prefill job for the appid`() {
        val jobs = listOf(job(appid = 42, status = "running"))
        assertEquals(jobs[0], findLiveJob(jobs, 42))
    }

    @Test
    fun `findLiveJob ignores a queued job`() {
        val jobs = listOf(job(appid = 42, status = "queued"))
        assertNull(findLiveJob(jobs, 42))
    }

    @Test
    fun `findLiveJob ignores a gc job even if running`() {
        val jobs = listOf(job(appid = 42, type = "gc", status = "running"))
        assertNull(findLiveJob(jobs, 42))
    }

    @Test
    fun `findLiveJob ignores a different appid`() {
        val jobs = listOf(job(appid = 1, status = "running"))
        assertNull(findLiveJob(jobs, 2))
    }

    @Test
    fun `indexLiveJobsByAppid only keeps live prefill jobs`() {
        val jobs = listOf(
            job(appid = 1, status = "running"),
            job(appid = 2, status = "queued"),
            job(appid = 3, type = "gc", status = "running"),
            job(appid = 4, status = "paused"),
        )
        val index = indexLiveJobsByAppid(jobs)
        assertEquals(setOf(1, 4), index.keys)
    }

    // ---- hasVisibleCacheContent / hasProtectedCacheContent -------------

    @Test
    fun `hasVisibleCacheContent requires positive size_bytes`() {
        assertFalse(hasVisibleCacheContent(game(sizeBytes = null)))
        assertFalse(hasVisibleCacheContent(game(sizeBytes = 0)))
        assertTrue(hasVisibleCacheContent(game(sizeBytes = 1)))
    }

    @Test
    fun `hasProtectedCacheContent is false only for idle plus never-prefilled plus no active job`() {
        assertFalse(hasProtectedCacheContent("idle", null, hasActiveJob = false))
        assertTrue(hasProtectedCacheContent("idle", null, hasActiveJob = true))
        assertTrue(hasProtectedCacheContent("idle", "2026-08-01T00:00:00Z", hasActiveJob = false))
        assertTrue(hasProtectedCacheContent("done", null, hasActiveJob = false))
        assertTrue(hasProtectedCacheContent("error", null, hasActiveJob = false))
    }

    // ---- dispKind -------------------------------------------------------

    @Test
    fun `dispKind a live running job overrides everything`() {
        val g = game(status = "error", sizeBytes = 5)
        assertEquals(StatusKind.RUNNING, dispKind(g, job(status = "running")))
    }

    @Test
    fun `dispKind a live paused job renders paused`() {
        val g = game(status = "done", sizeBytes = 5)
        assertEquals(StatusKind.PAUSED, dispKind(g, job(status = "paused")))
    }

    @Test
    fun `dispKind error status with no live job is error`() {
        assertEquals(StatusKind.ERROR, dispKind(game(status = "error"), null))
    }

    @Test
    fun `dispKind cached requires visible bytes -- a done status with null size is NOT cached`() {
        // Cache-content invariant (mockup round 5, finding 6): a "last
        // cached remnant" done row with size_bytes=null must render NONE.
        assertEquals(StatusKind.NONE, dispKind(game(status = "done", sizeBytes = null), null))
    }

    @Test
    fun `dispKind cached with real bytes`() {
        assertEquals(StatusKind.CACHED, dispKind(game(status = "done", sizeBytes = 5), null))
    }

    @Test
    fun `dispKind never-prefilled idle game is none`() {
        assertEquals(StatusKind.NONE, dispKind(game(status = "idle", sizeBytes = null), null))
    }

    // ---- statusAction -----------------------------------------------------

    @Test
    fun `statusAction is null while selecting, regardless of state`() {
        assertNull(statusAction(game(status = "idle"), null, selecting = true))
        assertNull(statusAction(game(status = "error"), null, selecting = true))
        assertNull(statusAction(game(status = "done", sizeBytes = 5), job(status = "running"), selecting = true))
    }

    @Test
    fun `statusAction running job offers pause`() {
        val action = statusAction(game(), job(status = "running"), selecting = false)
        assertEquals(StatusActionType.PAUSE, action?.type)
    }

    @Test
    fun `statusAction paused job offers resume`() {
        val action = statusAction(game(), job(status = "paused"), selecting = false)
        assertEquals(StatusActionType.RESUME, action?.type)
    }

    @Test
    fun `statusAction not-cached offers download`() {
        val action = statusAction(game(status = "idle"), null, selecting = false)
        assertEquals(StatusActionType.DOWNLOAD, action?.type)
    }

    @Test
    fun `statusAction error offers retry -- a deliberate extension over the mockup`() {
        val action = statusAction(game(status = "error"), null, selecting = false)
        assertEquals(StatusActionType.RETRY, action?.type)
    }

    @Test
    fun `statusAction cached is never actionable -- no silent re-download`() {
        val action = statusAction(game(status = "done", sizeBytes = 5), null, selecting = false)
        assertNull(action)
    }

    // ---- isJobStateTransition -------------------------------------------

    @Test
    fun `isJobStateTransition true when the job disappears`() {
        assertTrue(isJobStateTransition(job(status = "running"), null))
    }

    @Test
    fun `isJobStateTransition true for a brand-new job row`() {
        assertTrue(isJobStateTransition(null, job(status = "running")))
    }

    @Test
    fun `isJobStateTransition false when status is unchanged`() {
        assertFalse(isJobStateTransition(job(status = "running"), job(status = "running")))
    }

    @Test
    fun `isJobStateTransition true when status actually changed`() {
        assertTrue(isJobStateTransition(job(status = "running"), job(status = "paused")))
    }
}
