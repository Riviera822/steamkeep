package dev.steamvault.app.notifications

import android.content.Context
import android.content.SharedPreferences
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

private const val PREFS_FILE = "notification_snapshot_prefs"
private const val KEY_SNAPSHOT_JSON = "snapshot_json"

/**
 * Where the previous poll's [NotificationSnapshot] is persisted (WP 4b.8
 * brief: "decide storage: DataStore/SharedPreferences file, NOT the
 * encrypted store unless it carries secrets; document"). See
 * `NotificationSnapshot.kt`'s kdoc for why this is plain (non-encrypted)
 * `SharedPreferences`, same reasoning `LibraryPreferences.kt` documents for
 * the per-device layout choice -- nothing persisted here is a secret.
 *
 * Extracted as an interface for the same off-device-testability reason
 * every other storage seam in this app is (`CredentialStore`,
 * `LibraryPreferences`) -- `InMemoryNotificationSnapshotStore` (test
 * sources) backs `NotificationPollLogicTest`'s idempotency pin without a
 * real `Context`.
 *
 * [load] returning `null` means "no snapshot has ever been saved" (the
 * first-ever poll) -- distinct from a saved [NotificationSnapshot] whose
 * lists happen to be empty (a real, previously observed "nothing here").
 * A JSON value that fails to decode (corrupted preference, a future
 * incompatible schema change) is treated the SAME as "never saved" rather
 * than crashing the worker -- fail-soft, matching this WP's other
 * degrade-gracefully rules (no connection configured, fetch failure). The
 * actual decode-or-null logic lives in one place,
 * [decodeSnapshotOrNull] -- see its kdoc (review fix S1) for why this
 * store and the test-only in-memory fake must never each keep their own
 * copy of that try/catch.
 */
interface NotificationSnapshotStore {
    fun load(): NotificationSnapshot?
    fun save(snapshot: NotificationSnapshot)
}

class SharedPreferencesNotificationSnapshotStore(context: Context) : NotificationSnapshotStore {
    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences(PREFS_FILE, Context.MODE_PRIVATE)

    override fun load(): NotificationSnapshot? = decodeSnapshotOrNull(prefs.getString(KEY_SNAPSHOT_JSON, null))

    override fun save(snapshot: NotificationSnapshot) {
        val raw = Json.encodeToString(snapshot)
        prefs.edit().putString(KEY_SNAPSHOT_JSON, raw).apply()
    }
}
