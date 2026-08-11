package dev.steamvault.app.notifications

import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

/**
 * JVM-only fake for [NotificationSnapshotStore] (same pattern as
 * `storage/InMemoryCredentialStore.kt` / `InMemoryLibraryPreferences.kt`) --
 * backs [NotificationPollLogicTest]'s idempotency pin and
 * [NotificationSnapshotSerializationTest]'s corrupted-JSON case.
 *
 * Deliberately stores the encoded JSON STRING internally, mirroring
 * [SharedPreferencesNotificationSnapshotStore]'s actual on-disk shape
 * (a `String` preference value) rather than holding a [NotificationSnapshot]
 * object directly -- so [putRawForTest] can inject a corrupted value and
 * exercise the real decode-failure path, not a shortcut around it.
 *
 * **[load] calls the exact same [decodeSnapshotOrNull] the production
 * [SharedPreferencesNotificationSnapshotStore] calls (review fix S1) --
 * this class does NOT keep its own parallel try/catch.** Before this fix it
 * did, which meant `NotificationSnapshotSerializationTest`'s corrupted-JSON
 * case only ever pinned THIS class's copy of the decode logic: deleting the
 * production store's try/catch survived the full suite untouched, because
 * no test path ever ran malformed input through the production code. Now
 * both callers share one implementation, so that mutation is fatal from
 * either side.
 */
class InMemoryNotificationSnapshotStore : NotificationSnapshotStore {
    private var raw: String? = null

    override fun load(): NotificationSnapshot? = decodeSnapshotOrNull(raw)

    override fun save(snapshot: NotificationSnapshot) {
        raw = Json.encodeToString(snapshot)
    }

    /** Test-only: inject an arbitrary raw string, including malformed JSON. */
    fun putRawForTest(value: String) {
        raw = value
    }
}
