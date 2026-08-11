package dev.steamvault.app.notifications

import dev.steamvault.app.net.model.ClientOut
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pins [NotificationPollLogic]'s idempotency contract (WP 4b.8 brief:
 * "idempotency decision pin (crash-before-persist ⇒ same events re-derived,
 * crash-after ⇒ none)") end to end against a fake
 * [NotificationSnapshotStore], simulating the two crash windows
 * [NotificationPollWorker.doWork]'s "notify, THEN persist" ordering is
 * designed around (see that class's kdoc). No Android/WorkManager
 * involved -- [NotificationPollLogic.evaluate] plus
 * [InMemoryNotificationSnapshotStore] are enough to exercise the real
 * decision + storage contract.
 */
class NotificationPollLogicTest {

    private fun job(id: Int, appid: Int = 42, status: String) =
        JobSummary(id = id, appid = appid, type = "prefill", status = status, created_at = "2026-08-10T00:00:00Z")

    private fun games(): List<GameSummary> = emptyList()
    private fun clients(): List<ClientOut> = emptyList()

    @Test
    fun `crash-before-persist re-derives the same events on the next run`() {
        val store = InMemoryNotificationSnapshotStore()

        // Run 1: job 1 transitions running -> done. Simulate a crash AFTER
        // computing events (they would have been posted) but BEFORE
        // store.save() runs -- i.e. simply never call save().
        val run1Jobs = listOf(job(1, status = "running"))
        val prevForRun1 = store.load() // null: first-ever poll
        val result1 = NotificationPollLogic.evaluate(prevForRun1, run1Jobs, games(), clients())
        assertEquals(emptyList<NotificationEvent>(), result1.events) // first poll is silent (correct, not the case under test)

        // Seed a "previous" snapshot as if an EARLIER successful run had
        // already seen job 1 as running (so run 2 is a genuine transition).
        store.save(NotificationSnapshot(jobs = listOf(JobSnapshotEntry(1, 42, "prefill", "running"))))

        // Run 2: job 1 is now done. Crash happens AFTER evaluate (events
        // computed, would be posted) but BEFORE save() -- never call it.
        val run2Jobs = listOf(job(1, status = "done"))
        val prevForRun2 = store.load()
        val result2 = NotificationPollLogic.evaluate(prevForRun2, run2Jobs, games(), clients())
        assertEquals(1, result2.events.size)
        assertTrue(result2.events[0] is NotificationEvent.JobFinished)
        // Deliberately NOT calling store.save(result2.snapshotToPersist) --
        // this is the simulated crash.

        // Run 3 ("the next real poll after the crash"): store still holds
        // run 2's PREVIOUS snapshot (job running), because the crash meant
        // save() never happened. Nothing changed server-side since run 2
        // (still done) -- evaluate() must RE-DERIVE the identical event.
        val prevForRun3 = store.load()
        assertEquals(prevForRun2, prevForRun3) // proves the crash really left the old snapshot in place
        val result3 = NotificationPollLogic.evaluate(prevForRun3, run2Jobs, games(), clients())
        assertEquals(result2.events, result3.events)
        assertEquals(1, result3.events.size)
    }

    @Test
    fun `crash-strictly-after-persist produces zero events on the next run`() {
        val store = InMemoryNotificationSnapshotStore()

        // Seed the "already seen running" baseline, as above.
        store.save(NotificationSnapshot(jobs = listOf(JobSnapshotEntry(1, 42, "prefill", "running"))))

        // Run: job 1 is now done -- a genuine transition.
        val jobs = listOf(job(1, status = "done"))
        val prev = store.load()
        val result = NotificationPollLogic.evaluate(prev, jobs, games(), clients())
        assertEquals(1, result.events.size)

        // Persist succeeds (the crash, if any, happens strictly AFTER this
        // point -- e.g. after doWork() has already returned Result.success()).
        store.save(result.snapshotToPersist)

        // Next run: nothing changed since. The persisted snapshot already
        // reflects "done" as the baseline, so evaluate() must derive ZERO
        // events -- no re-notification.
        val prevNext = store.load()
        val resultNext = NotificationPollLogic.evaluate(prevNext, jobs, games(), clients())
        assertEquals(emptyList<NotificationEvent>(), resultNext.events)
    }
}
