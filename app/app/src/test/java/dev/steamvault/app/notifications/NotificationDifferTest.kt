package dev.steamvault.app.notifications

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * JVM port of `web/tests/notifications.test.js` onto the compact snapshot
 * entries (WP 4b.8 brief: "the differ port with the web's named cases").
 * Test names deliberately mirror the web file's `test(...)` descriptions so
 * the correspondence is auditable side by side.
 */
class NotificationDifferTest {

    private fun job(id: Int = 1, appid: Int = 42, type: String = "prefill", status: String = "running") =
        JobSnapshotEntry(id, appid, type, status)

    private fun game(appid: Int = 42, name: String? = "Aurora Cascade", status: String = "done", sizeBytes: Long? = 1000L) =
        GameSnapshotEntry(appid, name, status, sizeBytes)

    private fun client(clientId: String = "workshop-pc", bypassSuspected: Boolean = false) =
        ClientSnapshotEntry(clientId, bypassSuspected)

    // -----------------------------------------------------------------
    // First poll: no baseline => zero events, however alarming curr looks.
    // -----------------------------------------------------------------

    @Test
    fun `first poll never fires a notification storm (jobs)`() {
        val curr = listOf(job(id = 1, status = "done"), job(id = 2, status = "error"))
        assertEquals(emptyList<NotificationEvent>(), diffJobs(null, curr))
    }

    @Test
    fun `first poll never fires a notification storm (games)`() {
        val curr = listOf(game(status = "stale"))
        assertEquals(emptyList<NotificationEvent>(), diffGames(null, curr))
    }

    @Test
    fun `first poll never fires a notification storm (clients)`() {
        val curr = listOf(client(bypassSuspected = true))
        assertEquals(emptyList<NotificationEvent>(), diffClients(null, curr))
    }

    @Test
    fun `first poll never fires across the combined helper either`() {
        val prev = null
        val curr = NotificationSnapshot(
            jobs = listOf(job(id = 1, status = "done")),
            games = listOf(game(status = "stale")),
            clients = listOf(client(bypassSuspected = true)),
        )
        assertEquals(emptyList<NotificationEvent>(), diffSnapshots(prev, curr))
    }

    // -----------------------------------------------------------------
    // No-change case
    // -----------------------------------------------------------------

    @Test
    fun `no-change poll produces zero events`() {
        val jobs = listOf(job(id = 1, status = "running"))
        val games = listOf(game(status = "done"))
        val clients = listOf(client(bypassSuspected = false))
        assertEquals(emptyList<NotificationEvent>(), diffJobs(jobs, jobs.map { it.copy() }))
        assertEquals(emptyList<NotificationEvent>(), diffGames(games, games.map { it.copy() }))
        assertEquals(emptyList<NotificationEvent>(), diffClients(clients, clients.map { it.copy() }))
    }

    // -----------------------------------------------------------------
    // job_finished
    // -----------------------------------------------------------------

    @Test
    fun `job_finished- an active job transitioning to done fires exactly one event`() {
        val prev = listOf(job(id = 1, status = "running"))
        val curr = listOf(job(id = 1, status = "done"))
        val events = diffJobs(prev, curr)
        assertEquals(1, events.size)
        assertEquals(NotificationEvent.JobFinished(jobId = 1, appid = 42, jobType = "prefill"), events[0])
        assertEquals("job:1:done", events[0].key)
    }

    @Test
    fun `job_finished- a queued job that finished between two polls (added, not updated) still fires`() {
        val prev = listOf(job(id = 99, status = "running"))
        val curr = listOf(job(id = 99, status = "running"), job(id = 1, status = "done"))
        val events = diffJobs(prev, curr)
        assertEquals(1, events.size)
        assertTrue(events[0] is NotificationEvent.JobFinished)
        assertEquals(1, (events[0] as NotificationEvent.JobFinished).jobId)
    }

    @Test
    fun `job_finished- a job that was ALREADY done does not re-fire on an unrelated update`() {
        val prev = listOf(job(id = 1, status = "done"))
        val curr = listOf(job(id = 1, status = "done"))
        assertEquals(emptyList<NotificationEvent>(), diffJobs(prev, curr))
    }

    @Test
    fun `a job aging out of the GET v1jobs limit (removed, not updated) fires no event`() {
        val prev = listOf(job(id = 1, status = "done"), job(id = 2, status = "running"))
        val curr = listOf(job(id = 2, status = "running")) // id 1 aged out of the window
        assertEquals(emptyList<NotificationEvent>(), diffJobs(prev, curr))
    }

    // -----------------------------------------------------------------
    // job_failed
    // -----------------------------------------------------------------

    @Test
    fun `job_failed- an active job transitioning to error fires exactly one event`() {
        val prev = listOf(job(id = 5, status = "running"))
        val curr = listOf(job(id = 5, status = "error"))
        val events = diffJobs(prev, curr)
        assertEquals(1, events.size)
        assertEquals(NotificationEvent.JobFailed(jobId = 5, appid = 42, jobType = "prefill"), events[0])
        assertEquals("job:5:error", events[0].key)
    }

    @Test
    fun `job_failed- fires from 'queued' too (never started before failing)`() {
        val prev = listOf(job(id = 5, status = "queued"))
        val curr = listOf(job(id = 5, status = "error"))
        val events = diffJobs(prev, curr)
        assertEquals(1, events.size)
        assertTrue(events[0] is NotificationEvent.JobFailed)
    }

    @Test
    fun `job_failed- fires from 'paused' too (shutdown-during-pause edge case)`() {
        val prev = listOf(job(id = 5, status = "paused"))
        val curr = listOf(job(id = 5, status = "error"))
        val events = diffJobs(prev, curr)
        assertEquals(1, events.size)
        assertTrue(events[0] is NotificationEvent.JobFailed)
    }

    // -----------------------------------------------------------------
    // cancelled is deliberately silent
    // -----------------------------------------------------------------

    @Test
    fun `a job cancelled by the operator produces no event`() {
        val prev = listOf(job(id = 7, status = "running"))
        val curr = listOf(job(id = 7, status = "cancelled"))
        assertEquals(emptyList<NotificationEvent>(), diffJobs(prev, curr))
    }

    // -----------------------------------------------------------------
    // Android improvement: unrecognized PREVIOUS status still reports a
    // genuine transition to a terminal status (see NotificationDiffer.kt's
    // kdoc, "Divergence from the web port").
    // -----------------------------------------------------------------

    @Test
    fun `an unrecognized previous status transitioning to done still fires (Android improvement)`() {
        val prev = listOf(job(id = 3, status = "frobnicated"))
        val curr = listOf(job(id = 3, status = "done"))
        val events = diffJobs(prev, curr)
        assertEquals(1, events.size)
        assertTrue(events[0] is NotificationEvent.JobFinished)
    }

    @Test
    fun `an unrecognized previous status transitioning to error still fires (Android improvement)`() {
        val prev = listOf(job(id = 3, status = "frobnicated"))
        val curr = listOf(job(id = 3, status = "error"))
        val events = diffJobs(prev, curr)
        assertEquals(1, events.size)
        assertTrue(events[0] is NotificationEvent.JobFailed)
    }

    // -----------------------------------------------------------------
    // update_ready
    // -----------------------------------------------------------------

    @Test
    fun `update_ready- a game turning stale fires exactly one event`() {
        val prev = listOf(game(status = "done"))
        val curr = listOf(game(status = "stale"))
        val events = diffGames(prev, curr)
        assertEquals(1, events.size)
        assertEquals(NotificationEvent.UpdateReady(appid = 42, name = "Aurora Cascade"), events[0])
        assertEquals("game:42:stale", events[0].key)
    }

    @Test
    fun `update_ready- does NOT fire for a 'stale' game with zero cache content (NOTES finding 6)`() {
        val prev = listOf(game(status = "done", sizeBytes = 1000L))
        val curr = listOf(game(status = "stale", sizeBytes = 0L))
        assertEquals(emptyList<NotificationEvent>(), diffGames(prev, curr))
    }

    @Test
    fun `update_ready- does NOT fire for a 'stale' game with null sizeBytes`() {
        val prev = listOf(game(status = "done", sizeBytes = 1000L))
        val curr = listOf(game(status = "stale", sizeBytes = null))
        assertEquals(emptyList<NotificationEvent>(), diffGames(prev, curr))
    }

    @Test
    fun `update_ready- does not fire for a game that was already stale`() {
        val prev = listOf(game(status = "stale"))
        val curr = listOf(game(status = "stale", sizeBytes = 2000L))
        assertEquals(emptyList<NotificationEvent>(), diffGames(prev, curr))
    }

    @Test
    fun `update_ready- does not fire for status changes that aren't 'stale'`() {
        val prev = listOf(game(status = "idle"))
        val curr = listOf(game(status = "running"))
        assertEquals(emptyList<NotificationEvent>(), diffGames(prev, curr))
    }

    // -----------------------------------------------------------------
    // bypass_suspected / bypass_resolved
    // -----------------------------------------------------------------

    @Test
    fun `bypass_suspected- false to true fires exactly one event`() {
        val prev = listOf(client(bypassSuspected = false))
        val curr = listOf(client(bypassSuspected = true))
        val events = diffClients(prev, curr)
        assertEquals(1, events.size)
        assertEquals(NotificationEvent.BypassSuspected(clientId = "workshop-pc"), events[0])
        assertEquals("client:workshop-pc:suspected", events[0].key)
    }

    @Test
    fun `bypass_resolved- true to false fires exactly one event (symmetric transition)`() {
        val prev = listOf(client(bypassSuspected = true))
        val curr = listOf(client(bypassSuspected = false))
        val events = diffClients(prev, curr)
        assertEquals(1, events.size)
        assertEquals(NotificationEvent.BypassResolved(clientId = "workshop-pc"), events[0])
        assertEquals("client:workshop-pc:resolved", events[0].key)
    }

    @Test
    fun `bypass state holding steady (true-true or false-false) fires nothing`() {
        val suspectedBoth = listOf(client(bypassSuspected = true))
        assertEquals(emptyList<NotificationEvent>(), diffClients(suspectedBoth, suspectedBoth.map { it.copy() }))

        val okBoth = listOf(client(bypassSuspected = false))
        assertEquals(emptyList<NotificationEvent>(), diffClients(okBoth, okBoth.map { it.copy() }))
    }

    // -----------------------------------------------------------------
    // diffSnapshots: combines all three, jobs/games/clients order
    // -----------------------------------------------------------------

    @Test
    fun `combined helper concatenates events from all three domains`() {
        val prev = NotificationSnapshot(
            jobs = listOf(job(id = 1, status = "running")),
            games = listOf(game(status = "done")),
            clients = listOf(client(bypassSuspected = false)),
        )
        val curr = NotificationSnapshot(
            jobs = listOf(job(id = 1, status = "done")),
            games = listOf(game(status = "stale")),
            clients = listOf(client(bypassSuspected = true)),
        )
        val events = diffSnapshots(prev, curr)
        assertEquals(3, events.size)
        assertTrue(events[0] is NotificationEvent.JobFinished)
        assertTrue(events[1] is NotificationEvent.UpdateReady)
        assertTrue(events[2] is NotificationEvent.BypassSuspected)
    }
}
