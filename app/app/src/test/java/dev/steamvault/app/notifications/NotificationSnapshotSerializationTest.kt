package dev.steamvault.app.notifications

import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Serialize/deserialize round-trip for the persisted [NotificationSnapshot]
 * (WP 4b.8 brief: "snapshot round-trip (serialize/deserialize compact
 * snapshot)"). Exercises the exact `kotlinx.serialization` call
 * [SharedPreferencesNotificationSnapshotStore] uses, without needing a real
 * `Context`/`SharedPreferences` -- the JSON encode/decode pair is the whole
 * persistence contract; only the "where the string lives" part needs
 * Android.
 */
class NotificationSnapshotSerializationTest {

    @Test
    fun `round-trips a snapshot with all three domains populated`() {
        val original = NotificationSnapshot(
            jobs = listOf(
                JobSnapshotEntry(id = 1, appid = 42, type = "prefill", status = "running"),
                JobSnapshotEntry(id = 2, appid = 7, type = "gc", status = "done"),
            ),
            games = listOf(
                GameSnapshotEntry(appid = 42, name = "Aurora Cascade", status = "done", sizeBytes = 1000L),
                GameSnapshotEntry(appid = 43, name = null, status = "idle", sizeBytes = null),
            ),
            clients = listOf(
                ClientSnapshotEntry(clientId = "workshop-pc", bypassSuspected = true),
            ),
        )

        val json = Json.encodeToString(original)
        val decoded = Json.decodeFromString(NotificationSnapshot.serializer(), json)

        assertEquals(original, decoded)
    }

    @Test
    fun `round-trips an all-empty snapshot (distinct from null -- not the first-poll case)`() {
        val original = NotificationSnapshot()
        val json = Json.encodeToString(original)
        val decoded = Json.decodeFromString(NotificationSnapshot.serializer(), json)

        assertEquals(original, decoded)
        assertEquals(emptyList<JobSnapshotEntry>(), decoded.jobs)
    }

    @Test
    fun `round-trips null and empty-string game name fields distinctly`() {
        val original = NotificationSnapshot(
            games = listOf(
                GameSnapshotEntry(appid = 1, name = null, status = "idle", sizeBytes = null),
                GameSnapshotEntry(appid = 2, name = "", status = "idle", sizeBytes = 0L),
            ),
        )
        val decoded = Json.decodeFromString(NotificationSnapshot.serializer(), Json.encodeToString(original))
        assertNull(decoded.games[0].name)
        assertEquals("", decoded.games[1].name)
    }

    @Test
    fun `corrupted JSON is treated by the store as never-saved, not a crash`() {
        val store = InMemoryNotificationSnapshotStore()
        store.putRawForTest("{ this is not valid json")
        assertNull(store.load())
    }
}
