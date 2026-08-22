package dev.steamvault.app.demo

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.CacheDeletionOut
import dev.steamvault.app.net.model.ClientOut
import dev.steamvault.app.net.model.DeletedDepotOut
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.GcJobRef
import dev.steamvault.app.net.model.JobControlOut
import dev.steamvault.app.net.model.JobDetail
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.MappingEntry
import dev.steamvault.app.net.model.PrefillJobRef
import dev.steamvault.app.net.model.SettingInfoOut
import dev.steamvault.app.net.model.SettingsOut
import dev.steamvault.app.net.model.SkippedSharedDepotOut
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import java.time.Instant

/**
 * Demo mode's in-memory fixture data (WP APP-DEMO brief). The whole point of
 * this class -- mirroring `web/js/demo-data.js`'s module-level mutable state
 * exactly (see that file's own header) -- is that it is the ONLY place demo
 * data lives: no persistence, no network, reset to a fresh copy every time
 * [DemoState.fresh] is called (once per "enter demo mode" action in
 * [dev.steamvault.app.MainActivity], never reused across sessions -- WP
 * brief constraint 4: "re-entering must not carry stale state").
 *
 * **Shapes match the real API 1:1 by construction, not by convention.**
 * Every method below returns the SAME `@Serializable` data classes
 * [dev.steamvault.app.net.VaultApiClient]'s real endpoints return
 * (the `net/model` package) -- there is no separate "demo DTO" shape to drift from
 * the real one; adding a field to (say) [GameSummary] would fail to compile
 * here until this file's constructor call sites are updated too. This is a
 * stronger guarantee than a hand-checked "matches the docs" claim.
 *
 * **No Steam identity/library fixture on purpose (WP brief constraint 5:
 * "do not touch the credential store or the OpenID flow").** Demo mode
 * never builds a [dev.steamvault.app.net.VaultApiClient], so
 * [dev.steamvault.app.repo.SteamIdentityRepositoryImpl.ownedGames] already
 * fails closed on its own (`VaultRelayLibraryFetcher.requireClient` throws
 * `IllegalStateException` when there is no client) -- exactly the same
 * "vault-only view stays fully functional" path a real, never-connected
 * install takes today. Reusing that existing fail-closed behaviour, rather
 * than inventing a parallel Steam-relay fixture the way `web/js/demo-data.js`
 * has to (its identity model is entirely vault-api-mediated; this app's
 * OpenID sign-in is not), is what keeps this WP from ever touching identity
 * code at all. One consequence: the ADR-0010 "the two playtime fields are
 * absent by default" rule has no [dev.steamvault.app.net.model.OwnedGame]
 * surface in THIS fixture to violate -- there is no owned-games fixture,
 * full stop.
 *
 * Every job/game mutation is call-count driven ([tick]), never wall-clock --
 * same reasoning `web/js/demo-data.js`'s header gives ("demo mode needs no
 * timers of its own"). [tick] runs at the top of every jobs read
 * ([listJobSummaries]/[jobDetail]) so progress advances whether the caller
 * is polling `GET /v1/jobs` (Library/Downloads) or `GET /v1/jobs/{id}`
 * (the GC flow's own poll loop, `ui/detail/DetailController.kt`).
 */
class DemoState private constructor(
    private val games: MutableList<DemoGame>,
    private val jobs: MutableList<DemoJob>,
    private val clients: List<ClientOut>,
) {
    private var nextJobId = (jobs.maxOfOrNull { it.id } ?: 900_099) + 1
    private val settingsOverrides = mutableMapOf<String, String>()

    // ---- games ------------------------------------------------------------

    @Synchronized
    fun listGameSummaries(): List<GameSummary> = games.map { it.toSummary() }

    @Synchronized
    fun gameDetail(appid: Int): GameDetail {
        val game = games.find { it.appid == appid }
            ?: throw VaultApiError.NotFound("no such app in the demo library", 404, "appid $appid not found")
        return game.toDetail()
    }

    @Synchronized
    fun mapping(): List<MappingEntry> =
        games.flatMap { game -> game.depots.map { MappingEntry(depotid = it.depotid, appid = game.appid) } }

    // ---- jobs ---------------------------------------------------------------

    @Synchronized
    fun listJobSummaries(limit: Int): List<JobSummary> {
        tick()
        return jobs.sortedByDescending { it.id }.take(limit).map { it.toSummary() }
    }

    @Synchronized
    fun jobDetail(id: Int): JobDetail {
        tick()
        val job = jobs.find { it.id == id }
            ?: throw VaultApiError.NotFound("no such job in the demo library", 404, "job $id not found")
        return job.toDetail()
    }

    @Synchronized
    fun enqueuePrefill(appids: List<Int>): List<PrefillJobRef> = appids.map { appid -> enqueueOnePrefill(appid) }

    /** `POST /v1/prefill/cached` (WP 4c-app's real contract) — only games
     * already `done` (on the cache) get requeued for a check, mirroring
     * `web/js/demo-data.js`'s `enqueuePrefillForAppid` reuse for this route. */
    @Synchronized
    fun enqueuePrefillCached(): List<PrefillJobRef> =
        games.filter { it.status == STATUS_DONE }.map { enqueueOnePrefill(it.appid) }

    private fun enqueueOnePrefill(appid: Int): PrefillJobRef {
        val existing = jobs.find { it.appid == appid && it.status in ACTIVE_JOB_STATUSES }
        if (existing != null) {
            return PrefillJobRef(appid = appid, job_id = existing.id, status = existing.status, deduplicated = true)
        }
        val game = games.find { it.appid == appid }
        val job = DemoJob(
            id = nextJobId++,
            appid = appid,
            type = "prefill",
            status = STATUS_RUNNING,
            createdAt = nowIso(),
            startedAt = nowIso(),
            ticksLeft = PREFILL_TICKS,
        )
        jobs.add(job)
        game?.status = STATUS_RUNNING
        return PrefillJobRef(appid = appid, job_id = job.id, status = job.status, deduplicated = false)
    }

    /**
     * Nitpick fix (WP APP-DEMO review round 2): the real server refuses
     * pause/resume/cancel against a job that is not in the matching state
     * (a `409`, api/README.md "Job control") -- the original version of
     * this method accepted any action against any job, including a
     * terminal (`done`/`error`/already-`cancelled`) one, which is a shape
     * the real API cannot produce. Each branch below now guards on the
     * ONE prior status that action is valid from.
     */
    @Synchronized
    fun controlJob(id: Int, action: JobControlAction): JobControlOut {
        val job = jobs.find { it.id == id }
            ?: throw VaultApiError.NotFound("no such job in the demo library", 404, "job $id not found")
        return when (action) {
            JobControlAction.PAUSE -> {
                if (job.status != STATUS_RUNNING) {
                    throw VaultApiError.Validation("job is not running", 409, "job $id is '${job.status}', cannot pause")
                }
                job.status = STATUS_PAUSED
                job.pausedAt = nowIso()
                JobControlOut(job_id = id, status = job.status, outcome = "paused", detail = "paused on request")
            }
            JobControlAction.RESUME -> {
                if (job.status != STATUS_PAUSED) {
                    throw VaultApiError.Validation("job is not paused", 409, "job $id is '${job.status}', cannot resume")
                }
                job.status = STATUS_RUNNING
                job.pausedAt = null
                JobControlOut(job_id = id, status = job.status, outcome = "resumed", detail = "resumed on request")
            }
            JobControlAction.CANCEL -> {
                if (job.status !in ACTIVE_JOB_STATUSES) {
                    throw VaultApiError.Validation("job is not active", 409, "job $id is '${job.status}', cannot cancel")
                }
                job.status = STATUS_CANCELLED
                job.finishedAt = nowIso()
                games.find { it.appid == job.appid }?.let { if (it.status == STATUS_RUNNING) it.status = STATUS_IDLE }
                JobControlOut(job_id = id, status = job.status, outcome = "cancelled", detail = "cancelled on request")
            }
        }
    }

    /** Advances every still-active job by one tick — see class kdoc. */
    private fun tick() {
        for (job in jobs) {
            if (job.status != STATUS_RUNNING || job.ticksLeft <= 0) continue
            job.ticksLeft -= 1
            if (job.ticksLeft > 0) continue
            when (job.type) {
                "prefill" -> finishPrefillJob(job)
                "gc" -> finishGcJob(job)
            }
        }
    }

    private fun finishPrefillJob(job: DemoJob) {
        job.status = STATUS_DONE
        job.finishedAt = nowIso()
        job.updated = 1
        job.upToDate = 0
        job.summaryParseOk = true
        job.logExcerpt = "[vault-api] worker claimed job ${job.id}\n" +
            "Downloading depot ${job.appid + 1} ...\n" +
            "Prefilled 1 app. Done."
        val game = games.find { it.appid == job.appid } ?: return
        game.status = STATUS_DONE
        game.needsForce = false
        game.lastPrefillAt = nowIso()
        if (game.depots.isEmpty()) {
            game.depots.add(DemoDepot(depotid = game.appid + 1, shared = false, sizeBytes = 1_200_000_000L))
        }
    }

    private fun finishGcJob(job: DemoJob) {
        job.finishedAt = nowIso()
        job.status = STATUS_DONE
        val game = games.find { it.appid == job.appid }
        val wouldDeleteBytes = game?.gcReclaimableBytes ?: 0L
        val heldBackBytes = game?.gcHeldBackBytes ?: 0L
        job.logExcerpt = if (job.gcExecute == true) {
            job.summaryParseOk = true
            "GC totals (EXECUTED): chunks_removed=1 bytes_freed=$wouldDeleteBytes " +
                "held_back=1 (${heldBackBytes} bytes) total_bytes_freed=$wouldDeleteBytes"
        } else {
            job.summaryParseOk = true
            "GC totals (DRY RUN): would_delete=1 ($wouldDeleteBytes bytes) held_back=1 (${heldBackBytes} bytes)"
        }
        if (job.gcExecute == true) {
            game?.gcReclaimableBytes = 0L
        }
    }

    // ---- cache / gc -----------------------------------------------------------

    @Synchronized
    fun deleteCache(appid: Int): CacheDeletionOut {
        val game = games.find { it.appid == appid }
            ?: throw VaultApiError.NotFound("no such app in the demo library", 404, "appid $appid not found")

        val deleted = mutableListOf<DeletedDepotOut>()
        val skipped = mutableListOf<SkippedSharedDepotOut>()
        var freed = 0L
        // Deletion always clears THIS game's own cache membership for every
        // depot it lists (api/README.md "Per-game deletion": "deletion
        // clears cache content, not mapping rows") -- a depot only ends up
        // in `skipped` (informational: bytes were NOT double-freed because
        // another still-cached game holds the same physical chunk) rather
        // than `deleted`, never kept on THIS game's own list either way.
        // ADR-0003's protection is about the underlying chunk files, not
        // about whether the game that just deleted still "has" the depot.
        for (depot in game.depots) {
            val coOwners = games.filter { it.appid != appid && it.depots.any { d -> d.depotid == depot.depotid } }
            if (depot.shared && coOwners.isNotEmpty()) {
                skipped.add(SkippedSharedDepotOut(depotid = depot.depotid, shared_with = coOwners.map { it.appid }))
            } else {
                deleted.add(DeletedDepotOut(depotid = depot.depotid, size_bytes_freed = depot.sizeBytes))
                freed += depot.sizeBytes
            }
        }
        game.depots.clear()
        game.status = STATUS_IDLE
        game.needsForce = true
        return CacheDeletionOut(
            appid = appid,
            deleted_depots = deleted,
            skipped_shared = skipped,
            failed = emptyList(),
            total_bytes_freed = freed,
        )
    }

    @Synchronized
    fun gc(appid: Int, execute: Boolean): GcJobRef {
        if (games.none { it.appid == appid }) {
            throw VaultApiError.NotFound("no such app in the demo library", 404, "appid $appid not found")
        }
        val job = DemoJob(
            id = nextJobId++,
            appid = appid,
            type = "gc",
            status = STATUS_RUNNING,
            createdAt = nowIso(),
            startedAt = nowIso(),
            ticksLeft = GC_TICKS,
            gcExecute = execute,
        )
        jobs.add(job)
        return GcJobRef(
            appid = appid,
            job_id = job.id,
            status = job.status,
            type = "gc",
            mode = if (execute) "execute" else "dry_run",
            execute = execute,
            deduplicated = false,
        )
    }

    // ---- clients ------------------------------------------------------------

    @Synchronized
    fun clientsOut(): List<ClientOut> = clients

    // ---- settings (ADR-0009 db > env > default precedence) ------------------

    @Synchronized
    fun settingsOut(): SettingsOut = SettingsOut(readonly = false, settings = describeSettings())

    @Synchronized
    fun patchSettings(updates: Map<String, JsonElement?>): SettingsOut {
        for ((key, value) in updates) {
            val spec = SETTINGS_SPECS[key]
                ?: throw VaultApiError.Validation("unknown setting", 422, "'$key': unknown setting key")
            if (spec.envOnly) {
                throw VaultApiError.Validation("environment-only setting", 422, "'$key': this key is environment-only")
            }
            if (value == null || value is JsonNull) {
                settingsOverrides.remove(key)
            } else {
                settingsOverrides[key] = rawTextOf(value)
            }
        }
        return settingsOut()
    }

    private fun rawTextOf(value: JsonElement): String = when (value) {
        is kotlinx.serialization.json.JsonArray -> value.joinToString(",") { (it as? JsonPrimitive)?.content.orEmpty() }
        is JsonPrimitive -> value.content
        else -> ""
    }

    private fun describeSettings(): List<SettingInfoOut> {
        val overridable = SETTINGS_SPECS.values.filter { !it.envOnly }.map { spec ->
            val override = settingsOverrides[spec.key]
            val (effective, source) = when {
                override != null -> spec.parse(override) to "db"
                spec.env != null -> spec.env to "env"
                else -> spec.default to "default"
            }
            SettingInfoOut(
                key = spec.key,
                effective = effective,
                source = source,
                fallback = spec.env ?: spec.default,
                applies = spec.applies,
                env_only = false,
            )
        }
        val envOnly = SETTINGS_SPECS.values.filter { it.envOnly }.map { spec ->
            SettingInfoOut(
                key = spec.key,
                effective = spec.default,
                source = "env",
                fallback = spec.default,
                applies = spec.applies,
                env_only = true,
            )
        }
        return overridable + envOnly
    }

    companion object {
        internal const val STATUS_DONE = "done"
        internal const val STATUS_IDLE = "idle"
        internal const val STATUS_RUNNING = "running"
        internal const val STATUS_ERROR = "error"
        internal const val STATUS_PAUSED = "paused"
        internal const val STATUS_CANCELLED = "cancelled"
        private val ACTIVE_JOB_STATUSES = setOf(STATUS_RUNNING, STATUS_PAUSED, "queued")
        private const val PREFILL_TICKS = 2
        private const val GC_TICKS = 1

        private fun nowIso(): String = Instant.now().toString()

        /** A brand-new, unmutated fixture set — call once per "enter demo
         * mode" action, never reused across sessions (WP brief constraint 4). */
        fun fresh(): DemoState = DemoState(seedGames(), seedJobs(), seedClients())
    }
}

enum class JobControlAction { PAUSE, RESUME, CANCEL }

/** Env-only value pinned to demo mode's own SharedPreferences-free posture
 * (there is no real env for a demo session to read) — mirrors
 * `web/js/demo-data.js`'s `ENV_ONLY_DEMO`. */
private data class DemoSettingSpec(
    val key: String,
    val default: JsonElement,
    val env: JsonElement?,
    val applies: String,
    val envOnly: Boolean = false,
    val parse: (String) -> JsonElement = { JsonPrimitive(it) },
)

private val WEBHOOK_EVENTS_ALL = listOf(
    "job.done",
    "job.error",
    "job.cancelled",
    "client.bypass_suspected",
    "client.bypass_resolved",
)

private val SETTINGS_SPECS: Map<String, DemoSettingSpec> = listOf(
    DemoSettingSpec("vault_name", default = JsonPrimitive(""), env = JsonPrimitive("steamhangar-demo"), applies = "restart-required"),
    DemoSettingSpec("schedule_window", default = JsonNull, env = JsonPrimitive("22:00-06:00"), applies = "next_sweep"),
    DemoSettingSpec(
        "schedule_interval_minutes",
        default = JsonPrimitive(180),
        env = JsonPrimitive(180),
        applies = "next_sweep",
        parse = { it.toIntOrNull()?.let { n -> JsonPrimitive(n) } ?: JsonPrimitive(180) },
    ),
    DemoSettingSpec(
        "schedule_client_stale_days",
        default = JsonPrimitive(7),
        env = JsonPrimitive(7),
        applies = "next_sweep",
        parse = { it.toIntOrNull()?.let { n -> JsonPrimitive(n) } ?: JsonPrimitive(7) },
    ),
    DemoSettingSpec("auto_gc", default = JsonPrimitive("off"), env = JsonPrimitive("off"), applies = "immediately"),
    DemoSettingSpec("webhook_url", default = JsonPrimitive(""), env = JsonPrimitive(""), applies = "restart-required"),
    DemoSettingSpec(
        "webhook_events",
        default = kotlinx.serialization.json.JsonArray(WEBHOOK_EVENTS_ALL.map { JsonPrimitive(it) }),
        env = kotlinx.serialization.json.JsonArray(WEBHOOK_EVENTS_ALL.map { JsonPrimitive(it) }),
        applies = "restart-required",
        parse = { raw ->
            kotlinx.serialization.json.JsonArray(
                raw.split(",").map { it.trim() }.filter { it.isNotEmpty() }.map { JsonPrimitive(it) },
            )
        },
    ),
    DemoSettingSpec("db_path", default = JsonPrimitive("/data/vault.db"), env = null, applies = "restart-required", envOnly = true),
    DemoSettingSpec("cache_root", default = JsonPrimitive("/vault/cache"), env = null, applies = "restart-required", envOnly = true),
    DemoSettingSpec(
        "steamprefill_path",
        default = JsonPrimitive("/usr/local/bin/steamprefill"),
        env = null,
        applies = "restart-required",
        envOnly = true,
    ),
    DemoSettingSpec(
        "steamprefill_cache_dir",
        default = JsonPrimitive("/root/.local/share/SteamPrefill"),
        env = null,
        applies = "restart-required",
        envOnly = true,
    ),
    DemoSettingSpec(
        "manifest_archive_dir",
        default = JsonPrimitive("/vault/manifest-archive"),
        env = null,
        applies = "restart-required",
        envOnly = true,
    ),
    DemoSettingSpec("web_dir", default = JsonPrimitive("/app/web"), env = null, applies = "restart-required", envOnly = true),
    DemoSettingSpec("settings_readonly", default = JsonPrimitive(false), env = null, applies = "restart-required", envOnly = true),
    // ADR-0010: both default OFF, and (as this fixture has no owned-games
    // surface at all, see DemoState's class kdoc) there is nothing in this
    // WP's scope that could ever expose either field regardless.
    DemoSettingSpec("relay_expose_playtime", default = JsonPrimitive(false), env = null, applies = "restart-required", envOnly = true),
    DemoSettingSpec("relay_expose_last_played", default = JsonPrimitive(false), env = null, applies = "restart-required", envOnly = true),
).associateBy { it.key }
