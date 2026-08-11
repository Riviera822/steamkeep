package dev.steamvault.app.notifications

import dev.steamvault.app.net.model.ClientOut
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import kotlinx.serialization.SerializationException
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * The persisted poll snapshot (WP 4b.8 brief: "persist a compact snapshot").
 *
 * **Design decision: compact, not the raw API response.** [NotificationDiffer]
 * only ever reads four fields off a job (`id`/`appid`/`type`/`status`), four
 * off a game (`appid`/`name`/`status`/`size_bytes`) and two off a client
 * (`client_id`/`bypass_suspected`) -- see `web/js/notifications.js`'s own
 * differ functions, ported unchanged in [NotificationDiffer]. Persisting the
 * full [JobSummary]/[GameSummary]/[ClientOut] payloads (which also carry
 * `created_at`/`started_at`/`last_prefill_at`/`source_addrs`/byte counters
 * etc.) would (a) bloat the on-disk snapshot for every poll of a
 * multi-hundred-game library, and (b) risk false "updated" transitions on
 * fields the differ doesn't care about, which [DiffByKey.KeyDiff]'s
 * unchanged/updated split would still have to sort out correctly -- keeping
 * the persisted shape narrow to exactly what the differ reads makes "did
 * anything DIFFER call about actually change" and "did anything on disk
 * change" the same question by construction.
 *
 * Plain (non-encrypted) storage is correct here, same reasoning
 * `LibraryPreferences.kt`'s kdoc gives for the layout preference: nothing in
 * this snapshot is a secret (job ids, statuses, app ids/names, byte counts,
 * client ids/bypass flags -- all of it already round-trips through
 * unauthenticated-adjacent, locally-cached poll data). It must NOT go
 * through [dev.steamvault.app.storage.CredentialStore]/
 * `EncryptedCredentialStore` -- that store's one narrow guarantee (never a
 * plain-prefs fallback for the vault-api key) has nothing to do with this
 * unrelated, non-secret cache, and folding it in would dilute that
 * guarantee's scope for no confidentiality benefit.
 */
@Serializable
data class NotificationSnapshot(
    val jobs: List<JobSnapshotEntry> = emptyList(),
    val games: List<GameSnapshotEntry> = emptyList(),
    val clients: List<ClientSnapshotEntry> = emptyList(),
)

@Serializable
data class JobSnapshotEntry(val id: Int, val appid: Int, val type: String, val status: String)

@Serializable
data class GameSnapshotEntry(val appid: Int, val name: String?, val status: String, val sizeBytes: Long?)

@Serializable
data class ClientSnapshotEntry(val clientId: String, val bypassSuspected: Boolean)

fun JobSummary.toSnapshotEntry(): JobSnapshotEntry = JobSnapshotEntry(id = id, appid = appid, type = type, status = status)

fun GameSummary.toSnapshotEntry(): GameSnapshotEntry =
    GameSnapshotEntry(appid = appid, name = name, status = status, sizeBytes = size_bytes)

fun ClientOut.toSnapshotEntry(): ClientSnapshotEntry =
    ClientSnapshotEntry(clientId = client_id, bypassSuspected = bypass_suspected)

fun buildSnapshot(jobs: List<JobSummary>, games: List<GameSummary>, clients: List<ClientOut>): NotificationSnapshot =
    NotificationSnapshot(
        jobs = jobs.map { it.toSnapshotEntry() },
        games = games.map { it.toSnapshotEntry() },
        clients = clients.map { it.toSnapshotEntry() },
    )

/**
 * The ONE decode-or-null implementation for a persisted [NotificationSnapshot]
 * (review fix S1). Both [SharedPreferencesNotificationSnapshotStore] and the
 * test-only `InMemoryNotificationSnapshotStore` call this exact function --
 * before this fix, each had its OWN separate try/catch around
 * `Json.decodeFromString`, so `NotificationSnapshotSerializationTest`'s
 * corrupted-JSON case only ever exercised the FAKE's copy: deleting the
 * production store's try/catch survived the full 534-test suite untouched,
 * because nothing called the production code path with malformed input.
 * Routing both callers through one shared function makes that mutation
 * fatal (see this WP's review report for the confirmed kill).
 *
 * `null` covers three cases, all deliberately collapsed to the same "never
 * saved" outcome (see [NotificationSnapshotStore]'s kdoc): [raw] itself is
 * `null` (nothing stored yet), [raw] fails to parse as JSON at all, or
 * [raw] parses but doesn't match [NotificationSnapshot]'s shape (a stale
 * schema from a future/older app version). [StackOverflowError] is caught
 * alongside [SerializationException] for the same reason
 * `net/model/SteamWebApi.kt::decodeJsonOrThrow` documents: kotlinx.
 * serialization's JSON scanner recurses per nesting level
 * (`docs/LEARNINGS.md`'s Parsers section, "CPython's json scanner recurses
 * per nesting level" -- same failure class here), so a pathologically
 * nested stored string must degrade to "never saved", not crash the
 * worker (or, in a test, the fake).
 */
fun decodeSnapshotOrNull(raw: String?): NotificationSnapshot? {
    val value = raw ?: return null
    return try {
        Json.decodeFromString(NotificationSnapshot.serializer(), value)
    } catch (_: SerializationException) {
        null
    } catch (_: StackOverflowError) {
        null
    }
}
