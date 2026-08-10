package dev.steamvault.app.net

import dev.steamvault.app.net.model.CacheDeletionOut
import dev.steamvault.app.net.model.CacheSummaryOut
import dev.steamvault.app.net.model.ClientOut
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.GcJobRef
import dev.steamvault.app.net.model.HealthOut
import dev.steamvault.app.net.model.JobControlOut
import dev.steamvault.app.net.model.JobDetail
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.PrefillJobRef
import dev.steamvault.app.net.model.SettingsOut
import dev.steamvault.app.net.model.settingAsBooleanOrNull
import dev.steamvault.app.net.model.settingAsIntOrNull
import dev.steamvault.app.net.model.settingAsStringListOrNull
import dev.steamvault.app.net.model.settingAsStringOrNull
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Wire-shape round trips against fixtures MODELED on the documented
 * response shapes in api/README.md's "Endpoints" table and the Pydantic
 * models it mirrors (`vault_api/routers` sources, read at git HEAD for this
 * WP — see each fixture's comment for the exact source). Synthetic data
 * throughout (docs/LEARNINGS.md "Testing discipline": "Fixtures: synthetic
 * only, modeled on real structure").
 *
 * One deliberate deviation from the WP brief's "copied verbatim from
 * api/README.md examples": no single README curl transcript currently
 * shows every field together after the schema's v4->v13 history (e.g. the
 * WP 1.3-era `GET /v1/games` transcript predates `needs_force` and
 * `last_manifest_check` entirely) — a fixture built from one such stale
 * transcript would silently under-test the CURRENT shape. These fixtures
 * are instead built directly from the "Endpoints" table's field list and
 * the router source's field ORDER/types, which is the more current and
 * more complete source of truth; each fixture cites exactly which. One
 * fixture (`JobControlOut`'s anchor test, below) IS lifted verbatim from an
 * api test's own asserted response body, per the WP 4b.2 Opus review's
 * should-fix suggestion.
 *
 * **STRICT decoding, in addition to production `VaultJson` (WP 4b.2 Opus
 * review should-fix).** [VaultJson] sets `ignoreUnknownKeys = true`
 * deliberately (see its own kdoc) — but that same leniency means a TYPO'D
 * fixture key silently vanishes into "unknown, ignored" instead of failing
 * the test that is supposed to prove the fixture matches the real shape.
 * [strictJson] below is `ignoreUnknownKeys = false` and used ONLY here, as
 * an anti-drift check alongside (never instead of) the production
 * [VaultJson] decode: every fixture in this file except the one
 * DELIBERATELY-unknown-field test at the bottom is decoded through
 * [decodeStrictAndLenient], so a fixture key that does not match any
 * property name in the target data class throws immediately instead of
 * silently decoding as if that field had never been mentioned.
 *
 * **What strict decoding does NOT catch (documented, not silently
 * assumed away):** a fixture that OMITS a field the data class gives a
 * Kotlin default to (e.g. `GameSummary.needs_force: Boolean = false`) is
 * indistinguishable, to both `VaultJson` and `strictJson`, from a real
 * server response that genuinely omitted it — decoding still succeeds,
 * silently absorbing the default. Every such field in this client's
 * models is nullable/defaulted specifically so an OLDER or NEWER
 * vault-api can omit it without breaking decode (see `VaultJson`'s kdoc);
 * the same mechanism that makes forward/backward compatibility work also
 * means a fixture-authoring mistake that drops one of these fields is NOT
 * caught by either Json configuration here. Only a field with NO Kotlin
 * default (e.g. `GameSummary.appid`, `.depot_count`) fails loudly
 * (`MissingFieldException`) if a fixture omits it.
 */
class SerializationRoundTripTest {

    /** Test-only anti-drift check — see this file's class kdoc. Never used for production decoding. */
    private val strictJson = Json {
        ignoreUnknownKeys = false
        isLenient = false
    }

    /**
     * Decode [json] as [T] through BOTH [strictJson] (thrown away — its
     * only job is to fail on a fixture key that doesn't match any
     * property, i.e. a typo) and the production [VaultJson] (whose result
     * is what the caller actually asserts against).
     */
    private inline fun <reified T> decodeStrictAndLenient(json: String): T {
        strictJson.decodeFromString<T>(json)
        return VaultJson.decodeFromString(json)
    }

    @Test
    fun `HealthOut -- GET v1 health, api README Auth section`() {
        val json = """{"status":"ok"}"""
        val decoded = decodeStrictAndLenient<HealthOut>(json)
        assertEquals("ok", decoded.status)
    }

    @Test
    fun `GameSummary -- GET v1 games row, games py GameSummary`() {
        val json = """
            {"appid":440,"name":"Team Fortress 2","status":"done",
             "last_prefill_at":"2026-08-05T20:44:34Z",
             "last_manifest_check":"2026-08-06T03:00:01Z",
             "depot_count":2,"size_bytes":6000000,"needs_force":false}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<GameSummary>(json)

        assertEquals(440, decoded.appid)
        assertEquals("Team Fortress 2", decoded.name)
        assertEquals("done", decoded.status)
        assertEquals("2026-08-05T20:44:34Z", decoded.last_prefill_at)
        assertEquals("2026-08-06T03:00:01Z", decoded.last_manifest_check)
        assertEquals(2, decoded.depot_count)
        assertEquals(6000000L, decoded.size_bytes)
        assertEquals(false, decoded.needs_force)
    }

    @Test
    fun `GameSummary -- an uncached never-prefilled app decodes with nulls, not defaults masking them`() {
        val json = """
            {"appid":730,"name":null,"status":"idle","last_prefill_at":null,
             "last_manifest_check":null,"depot_count":0,"size_bytes":null,
             "needs_force":true}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<GameSummary>(json)

        assertNull(decoded.name)
        assertNull(decoded.last_prefill_at)
        assertNull(decoded.last_manifest_check)
        assertNull(decoded.size_bytes)
        assertTrue(decoded.needs_force)
    }

    @Test
    fun `GameDetail -- GET v1 games appid, games py GameDetail, with a shared depot`() {
        val json = """
            {"appid":440,"name":"Team Fortress 2","status":"done",
             "last_prefill_at":"2026-08-05T20:44:34Z","last_manifest_check":null,
             "depots":[{"depotid":441,"shared":false,"size_bytes":1000000},
                       {"depotid":900,"shared":true,"size_bytes":5000000}],
             "size_bytes":6000000,"needs_force":false}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<GameDetail>(json)

        assertEquals(2, decoded.depots.size)
        assertEquals(441, decoded.depots[0].depotid)
        assertEquals(false, decoded.depots[0].shared)
        assertEquals(900, decoded.depots[1].depotid)
        assertEquals(true, decoded.depots[1].shared)
        assertEquals(5000000L, decoded.depots[1].size_bytes)
    }

    @Test
    fun `PrefillJobRef list -- POST v1 prefill 202 response, jobs py PrefillJobRef`() {
        val json = """
            [{"appid":440,"job_id":1,"status":"queued","deduplicated":false},
             {"appid":440,"job_id":1,"status":"queued","deduplicated":true}]
        """.trimIndent()

        val decoded = decodeStrictAndLenient<List<PrefillJobRef>>(json)

        assertEquals(2, decoded.size)
        assertEquals(false, decoded[0].deduplicated)
        assertEquals(true, decoded[1].deduplicated)
        assertEquals(1, decoded[1].job_id)
    }

    @Test
    fun `JobSummary -- GET v1 jobs row, jobs py JobSummary, a paused prefill`() {
        val json = """
            {"id":7,"appid":440,"type":"prefill","status":"paused",
             "created_at":"2026-08-10T10:00:00Z","started_at":"2026-08-10T10:00:05Z",
             "finished_at":null,"updated":null,"up_to_date":null,
             "summary_parse_ok":null,"gc_execute":null,
             "paused_at":"2026-08-10T10:05:00Z","stop_request":null}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<JobSummary>(json)

        assertEquals("paused", decoded.status)
        assertEquals("2026-08-10T10:05:00Z", decoded.paused_at)
        assertNull(decoded.gc_execute)
        assertNull(decoded.stop_request)
    }

    @Test
    fun `JobSummary -- a finished GC execute run carries gc_execute true`() {
        val json = """
            {"id":8,"appid":440,"type":"gc","status":"done",
             "created_at":"2026-08-10T10:00:00Z","started_at":"2026-08-10T10:00:05Z",
             "finished_at":"2026-08-10T10:01:00Z","updated":null,"up_to_date":null,
             "summary_parse_ok":null,"gc_execute":true,"paused_at":null,"stop_request":null}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<JobSummary>(json)

        assertEquals("gc", decoded.type)
        assertEquals(true, decoded.gc_execute)
    }

    @Test
    fun `JobDetail -- GET v1 jobs id, jobs py JobDetail, adds log_excerpt over JobSummary`() {
        val json = """
            {"id":1,"appid":440,"type":"prefill","status":"done",
             "created_at":"2026-08-05T20:44:34Z","started_at":"2026-08-05T20:44:35Z",
             "finished_at":"2026-08-05T20:44:36Z","updated":2,"up_to_date":0,
             "summary_parse_ok":true,"gc_execute":null,"paused_at":null,"stop_request":null,
             "log_excerpt":"[vault-api] Depot mapping updated: added=[441, 442]"}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<JobDetail>(json)

        assertEquals(2, decoded.updated)
        assertEquals(0, decoded.up_to_date)
        assertEquals(true, decoded.summary_parse_ok)
        assertTrue(decoded.log_excerpt!!.contains("Depot mapping updated"))
    }

    @Test
    fun `JobControlOut -- DELETE v1 jobs id and pause-resume, jobs py JobControlOut`() {
        val json = """{"job_id":1,"status":"queued","outcome":"resumed","detail":"job re-queued at the front"}"""
        val decoded = decodeStrictAndLenient<JobControlOut>(json)
        assertEquals("resumed", decoded.outcome)
        assertEquals("job re-queued at the front", decoded.detail)
    }

    @Test
    fun `JobControlOut -- anchor fixture lifted verbatim from api tests test_job_control py`() {
        // Lifted verbatim from api/tests/test_job_control.py (cancelling a
        // queued job): `assert body == {"job_id": job_id, "status":
        // "cancelled", "outcome": "immediate", "detail":
        // jobs.CANCELLED_QUEUED_MESSAGE}` -- `job_id` is a test-local int
        // there (any positive int fixture value proves the same thing);
        // `CANCELLED_QUEUED_MESSAGE`'s literal string is copied verbatim
        // from vault_api/jobs.py.
        val json = """
            {"job_id":3,"status":"cancelled","outcome":"immediate",
             "detail":"[vault-api] Cancelled while queued: this job was never started, so nothing ran and nothing on disk was touched."}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<JobControlOut>(json)

        assertEquals(3, decoded.job_id)
        assertEquals("cancelled", decoded.status)
        assertEquals("immediate", decoded.outcome)
        assertTrue(decoded.detail.contains("never started"))
    }

    @Test
    fun `CacheSummaryOut -- GET v1 cache summary, cache py CacheSummaryOut`() {
        val json = """
            {"total_bytes":6250000,
             "top_consumers":[{"appid":440,"name":"Team Fortress 2","size_bytes":6000000},
                               {"appid":730,"name":"Counter-Strike 2","size_bytes":5000000}],
             "unmapped_depots":{"count":1,"size_bytes":250000},
             "free_disk_bytes":119640584192}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<CacheSummaryOut>(json)

        assertEquals(6250000L, decoded.total_bytes)
        assertEquals(2, decoded.top_consumers.size)
        assertEquals(1, decoded.unmapped_depots.count)
        assertEquals(119640584192L, decoded.free_disk_bytes)
    }

    @Test
    fun `CacheDeletionOut -- DELETE v1 cache appid, cache py CacheDeletionOut, a last-remnant plus a failure`() {
        val json = """
            {"appid":440,
             "deleted_depots":[{"depotid":441,"size_bytes_freed":1000000,"shared_with_uncached":[]},
                                {"depotid":900,"size_bytes_freed":50000,"shared_with_uncached":[730]}],
             "skipped_shared":[{"depotid":901,"shared_with":[730]}],
             "failed":[{"depotid":902,"error":"WinError 5: access is denied"}],
             "total_bytes_freed":1050000}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<CacheDeletionOut>(json)

        assertEquals(2, decoded.deleted_depots.size)
        assertEquals(listOf(730), decoded.deleted_depots[1].shared_with_uncached)
        assertEquals(1, decoded.skipped_shared.size)
        assertEquals(1, decoded.failed.size)
        assertEquals("WinError 5: access is denied", decoded.failed[0].error)
        assertEquals(1050000L, decoded.total_bytes_freed)
    }

    @Test
    fun `GcJobRef -- POST v1 cache appid gc 202 response, cache py GcJobRef, dry run by default`() {
        val json = """{"appid":440,"job_id":7,"status":"queued","type":"gc","mode":"dry-run","execute":false,"deduplicated":false}"""
        val decoded = decodeStrictAndLenient<GcJobRef>(json)
        assertEquals("dry-run", decoded.mode)
        assertEquals(false, decoded.execute)
    }

    @Test
    fun `ClientOut -- GET v1 clients row, clients py ClientOut, bypass suspected`() {
        val json = """
            {"client_id":"gaming-pc","first_seen":"2026-08-05T20:44:34Z",
             "last_reported_at":"2026-08-05T20:44:36Z","app_count":3,
             "source_addrs":["192.168.1.42"],"cache_hits":120,"cache_misses":4,
             "bytes_served":734003200,"last_seen_in_cache_log":"2026-08-10T09:00:00Z",
             "bypass_suspected":true}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<ClientOut>(json)

        assertEquals("gaming-pc", decoded.client_id)
        assertEquals(listOf("192.168.1.42"), decoded.source_addrs)
        assertEquals(true, decoded.bypass_suspected)
    }

    @Test
    fun `ClientOut -- event feed off decodes with the documented 0 null false defaults`() {
        val json = """
            {"client_id":"steam-deck","first_seen":"2026-08-05T20:44:36Z",
             "last_reported_at":"2026-08-05T20:44:36Z","app_count":1,
             "source_addrs":[],"cache_hits":0,"cache_misses":0,"bytes_served":0,
             "last_seen_in_cache_log":null,"bypass_suspected":false}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<ClientOut>(json)

        assertEquals(0, decoded.cache_hits)
        assertNull(decoded.last_seen_in_cache_log)
        assertEquals(false, decoded.bypass_suspected)
    }

    @Test
    fun `SettingsOut -- GET v1 settings, settings py SettingsOut, heterogeneous effective fallback values`() {
        val json = """
            {"readonly":false,
             "settings":[
               {"key":"vault_name","effective":"my-vault","source":"default","fallback":"","applies":"restart-required","env_only":false},
               {"key":"schedule_window","effective":null,"source":"default","fallback":null,"applies":"next_sweep","env_only":false},
               {"key":"schedule_interval_minutes","effective":30,"source":"env","fallback":30,"applies":"next_sweep","env_only":false},
               {"key":"auto_gc","effective":"dry-run","source":"db","fallback":"off","applies":"immediately","env_only":false},
               {"key":"webhook_events","effective":["job_finished","job_failed"],"source":"default","fallback":["job_finished","job_failed"],"applies":"restart-required","env_only":false},
               {"key":"vault_api_key_placeholder_never_sent","effective":null,"source":"env","fallback":null,"applies":"restart-required","env_only":true}
             ]}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<SettingsOut>(json)

        assertEquals(false, decoded.readonly)
        assertEquals(6, decoded.settings.size)

        val vaultName = decoded.settings[0]
        assertEquals("my-vault", vaultName.effective.settingAsStringOrNull())
        assertEquals("", vaultName.fallback.settingAsStringOrNull())

        val scheduleWindow = decoded.settings[1]
        assertNull(scheduleWindow.effective.settingAsStringOrNull())

        val intervalMinutes = decoded.settings[2]
        assertEquals(30, intervalMinutes.effective.settingAsIntOrNull())
        assertEquals("env", intervalMinutes.source)

        val autoGc = decoded.settings[3]
        assertEquals("dry-run", autoGc.effective.settingAsStringOrNull())

        val webhookEvents = decoded.settings[4]
        assertEquals(listOf("job_finished", "job_failed"), webhookEvents.effective.settingAsStringListOrNull())

        val envOnly = decoded.settings[5]
        assertEquals(true, envOnly.env_only)
        assertNull(envOnly.effective.settingAsIntOrNull())
        assertNull(envOnly.effective.settingAsBooleanOrNull())
    }

    @Test
    fun `unknown future field is ignored, not a decode failure -- VaultJson ignoreUnknownKeys`() {
        // Deliberately VaultJson-only, NOT decodeStrictAndLenient: this is
        // the one fixture that's supposed to have a field strictJson would
        // reject -- that rejection is the exact behavior being proven
        // absent from the production decode path.
        val json = """
            {"appid":440,"name":"Team Fortress 2","status":"idle","last_prefill_at":null,
             "last_manifest_check":null,"depot_count":1,"size_bytes":null,"needs_force":false,
             "a_field_this_client_has_never_heard_of":{"nested":["whatever"]}}
        """.trimIndent()

        val decoded = VaultJson.decodeFromString<GameSummary>(json)

        assertEquals(440, decoded.appid)
    }
}
