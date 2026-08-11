package dev.steamvault.app.notifications

import dev.steamvault.app.ui.nav.Destination
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

/**
 * Literal-pinned event -> channel/destination mapping (WP 4b.8 brief:
 * "notification-routing mapping (event type → channel + destination,
 * literal-pinned)"). Expected values are hand-transcribed literals, never
 * derived from [NotificationRouting]/[NotificationChannelDef]/[Destination]
 * themselves -- same rule `docs/LEARNINGS.md`'s Android section requires
 * for every wire-format/status-word contract, applied here to an
 * intra-app contract that a careless enum rename could silently break.
 */
class NotificationRoutingTest {

    @Test
    fun `job_finished routes to the downloads channel and the Downloads destination`() {
        val event = NotificationEvent.JobFinished(jobId = 1, appid = 42, jobType = "prefill")
        assertEquals("downloads", NotificationRouting.channelFor(event).id)
        assertEquals(Destination.DOWNLOADS, NotificationRouting.destinationFor(event))
    }

    @Test
    fun `job_failed routes to the downloads channel and the Downloads destination`() {
        val event = NotificationEvent.JobFailed(jobId = 1, appid = 42, jobType = "prefill")
        assertEquals("downloads", NotificationRouting.channelFor(event).id)
        assertEquals(Destination.DOWNLOADS, NotificationRouting.destinationFor(event))
    }

    @Test
    fun `update_ready routes to the updates channel and the Library destination`() {
        val event = NotificationEvent.UpdateReady(appid = 42, name = "Aurora Cascade")
        assertEquals("updates", NotificationRouting.channelFor(event).id)
        assertEquals(Destination.LIBRARY, NotificationRouting.destinationFor(event))
    }

    @Test
    fun `bypass_suspected routes to the bypass channel and the Settings destination`() {
        val event = NotificationEvent.BypassSuspected(clientId = "workshop-pc")
        assertEquals("bypass", NotificationRouting.channelFor(event).id)
        assertEquals(Destination.SETTINGS, NotificationRouting.destinationFor(event))
    }

    @Test
    fun `bypass_resolved routes to the bypass channel and the Settings destination`() {
        val event = NotificationEvent.BypassResolved(clientId = "workshop-pc")
        assertEquals("bypass", NotificationRouting.channelFor(event).id)
        assertEquals(Destination.SETTINGS, NotificationRouting.destinationFor(event))
    }

    @Test
    fun `EXTRA_DESTINATION is the pinned literal string`() {
        assertEquals("dev.steamvault.app.notification.destination", NotificationRouting.EXTRA_DESTINATION)
    }

    @Test
    fun `notificationId is stable for the same event key and differs across event keys`() {
        val a = NotificationEvent.JobFinished(jobId = 1, appid = 42, jobType = "prefill")
        val b = NotificationEvent.JobFinished(jobId = 1, appid = 42, jobType = "prefill")
        val c = NotificationEvent.JobFinished(jobId = 2, appid = 42, jobType = "prefill")

        assertEquals(NotificationRouting.notificationId(a), NotificationRouting.notificationId(b))
        assertNotEquals(NotificationRouting.notificationId(a), NotificationRouting.notificationId(c))
    }
}
