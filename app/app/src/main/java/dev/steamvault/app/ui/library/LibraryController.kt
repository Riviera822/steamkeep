package dev.steamvault.app.ui.library

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.polling.PollingIntervals
import dev.steamvault.app.repo.CacheRepository
import dev.steamvault.app.repo.GamesRepository
import dev.steamvault.app.repo.JobsRepository
import dev.steamvault.app.repo.MappingRepository
import dev.steamvault.app.repo.SteamIdentityRepository
import dev.steamvault.app.storage.LibraryPreferences
import dev.steamvault.app.ui.library.logic.LibraryLayout
import dev.steamvault.app.ui.library.logic.MultiPlan
import dev.steamvault.app.ui.library.logic.StatusActionType
import dev.steamvault.app.ui.library.logic.buildMultiPlan
import dev.steamvault.app.ui.library.logic.classifyBulkSelection
import dev.steamvault.app.ui.library.logic.findLiveJob
import dev.steamvault.app.ui.library.logic.formatBytesGB
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Everything the Library screen needs, stateful glue kept as thin as
 * possible (WP 4b.4 brief: "extract logic so what ships untested-on-JVM is
 * thin" -- every DECISION this class makes delegates to `ui/library/logic/`
 * pure functions; this class only owns Compose state and orchestrates
 * suspend repository calls, mirroring `web/js/views/library.js`'s
 * `state`/`syncBulk`/`onAction` shapes one-to-one action-for-action).
 *
 * Not an `androidx.lifecycle.ViewModel` -- same house pattern `MainActivity`
 * already uses for [dev.steamvault.app.repo.SteamIdentityRepository]'s
 * screen state (a plain `mutableStateOf` field, no ViewModel dependency
 * anywhere in this project yet). Held via `remember` in `LibraryScreen.kt`,
 * so it does not survive leaving the Library tab -- consistent with the
 * flat, backstack-less navigation `ui/nav/Destination.kt` documents; a
 * fresh poll on next visit is an acceptable, deliberate trade for staying
 * dependency-light.
 */
class LibraryController(
    private val gamesRepository: GamesRepository,
    private val jobsRepository: JobsRepository,
    private val mappingRepository: MappingRepository,
    private val cacheRepository: CacheRepository,
    private val identityRepository: SteamIdentityRepository,
    private val libraryPreferences: LibraryPreferences,
    private val strings: LibraryStrings,
) {
    var games by mutableStateOf<List<GameSummary>>(emptyList())
        private set
    var jobs by mutableStateOf<List<JobSummary>>(emptyList())
        private set
    var ownedGames by mutableStateOf<List<OwnedGame>?>(null)
        private set
    var loadError by mutableStateOf<String?>(null)
        private set

    var query by mutableStateOf("")
    var filterKey by mutableStateOf("all")
    var layout by mutableStateOf(libraryPreferences.getLayout())
        private set

    var selecting by mutableStateOf(false)
        private set
    var selectedAppids by mutableStateOf<Set<Int>>(emptySet())
        private set

    var toast by mutableStateOf<String?>(null)
        private set

    var deletePlan by mutableStateOf<DeletePlanUiState>(DeletePlanUiState.Hidden)
        private set

    // ---- layout -----------------------------------------------------------

    fun selectLayout(newLayout: LibraryLayout) {
        layout = newLayout
        libraryPreferences.setLayout(newLayout)
    }

    // ---- selection ----------------------------------------------------

    fun enterSelect(appid: Int? = null) {
        selecting = true
        if (appid != null) selectedAppids = selectedAppids + appid
    }

    fun exitSelect() {
        selecting = false
        selectedAppids = emptySet()
    }

    fun toggleSelect(appid: Int) {
        selectedAppids = if (appid in selectedAppids) selectedAppids - appid else selectedAppids + appid
        if (selectedAppids.isEmpty()) selecting = false
    }

    fun dismissToast() {
        toast = null
    }

    // ---- polling (called from LaunchedEffect + repeatOnLifecycle in LibraryScreen.kt) --

    /** Foreground-only fixed-cadence poll (WP 4b.4 brief: "foreground-only
     * polling -- WorkManager background is 4b.8"). Runs until its own
     * coroutine is cancelled -- `delay` is a cooperative suspension point,
     * so cancelling the caller (e.g. `repeatOnLifecycle` dropping below
     * STARTED) unwinds this loop via normal structured-concurrency
     * cancellation, no manual `isActive` bookkeeping needed. */
    suspend fun pollGamesForever() {
        while (true) {
            refreshGamesOnce()
            delay(PollingIntervals.GAMES_MS)
        }
    }

    /** Adaptive-cadence poll (fast while a job is active, same decision
     * [PollingIntervals.nextJobsIntervalMs] already makes for the rest of
     * the app). */
    suspend fun pollJobsForever() {
        while (true) {
            refreshJobsOnce()
            delay(PollingIntervals.nextJobsIntervalMs(jobs))
        }
    }

    suspend fun refreshGamesOnce() {
        try {
            games = gamesRepository.list()
            loadError = null
        } catch (e: VaultApiError) {
            loadError = e.message
        }
    }

    suspend fun refreshJobsOnce() {
        try {
            jobs = jobsRepository.list()
        } catch (_: VaultApiError) {
            // Jobs poll failures don't blank the grid (games poll owns the
            // error banner) -- a transient jobs-endpoint hiccup should not
            // make cards flicker back to their non-live state.
        }
    }

    /** One-shot; `null`/empty [SteamIdentityRepository.ownedGames] failures
     * are swallowed on purpose -- see `LibraryMerge.kt`'s kdoc: "the
     * vault-only view must be fully functional" when there is no Steam
     * identity/key, which is exactly what leaving [ownedGames] `null`
     * achieves. */
    suspend fun refreshOwnedGamesOnce() {
        ownedGames = identityRepository.ownedGames().getOrNull()
    }

    /** Mirrors `store.refreshNow()`: an out-of-cadence poll right after a
     * mutation, so the grid doesn't wait out the ambient poll interval to
     * reflect what the user just did. Safe to call alongside the ongoing
     * [pollGamesForever]/[pollJobsForever] loops -- this only adds one extra
     * one-off fetch, it never spawns a second overlapping loop (the poll
     * loops are plain sequential `while` bodies, not self-re-arming
     * timers -- the async-poll-fork failure class docs/LEARNINGS.md's WP
     * 4a.2 entry describes does not apply to this shape). */
    private fun refreshNow(scope: CoroutineScope) {
        scope.launch { refreshGamesOnce() }
        scope.launch { refreshJobsOnce() }
    }

    // ---- per-card action (capsule pill / list-row icon) -------------------

    fun onCardAction(scope: CoroutineScope, appid: Int, actionType: StatusActionType) {
        scope.launch {
            try {
                when (actionType) {
                    StatusActionType.DOWNLOAD, StatusActionType.RETRY -> {
                        jobsRepository.prefill(listOf(appid))
                        toast = strings.queuedForDownload()
                    }
                    StatusActionType.PAUSE -> {
                        val job = findLiveJob(jobs, appid) ?: return@launch
                        jobsRepository.pause(job.id)
                        toast = strings.pauseRequested()
                    }
                    StatusActionType.RESUME -> {
                        val job = findLiveJob(jobs, appid) ?: return@launch
                        jobsRepository.resume(job.id)
                        toast = strings.resuming()
                    }
                }
                refreshNow(scope)
            } catch (e: VaultApiError) {
                toast = e.message ?: strings.actionFailedFallback()
            }
        }
    }

    // ---- bulk download ------------------------------------------------

    fun startBulkDownload(scope: CoroutineScope, appids: List<Int>) {
        if (appids.isEmpty()) return
        scope.launch {
            try {
                jobsRepository.prefill(appids)
                toast = strings.jobsQueued(appids.size)
                exitSelect()
                refreshNow(scope)
            } catch (e: VaultApiError) {
                toast = e.message ?: strings.actionFailedFallback()
            }
        }
    }

    // ---- bulk delete ----------------------------------------------------
    // Eligibility itself (has-cache-content, not busy) is computed where the
    // bulk bar renders it (`LibraryBulkBar.kt`, `classifyBulkDeleteEligibility`)
    // against the FULL merged library, not just `games` -- see that file's
    // kdoc for why the selected set must be resolved against the merged
    // list rather than the raw vault snapshot.

    fun openDeleteConfirm(scope: CoroutineScope, ids: List<Int>) {
        if (ids.isEmpty()) return
        deletePlan = DeletePlanUiState.Loading(ids)
        scope.launch {
            try {
                val details: List<GameDetail> = ids.map { gamesRepository.detail(it) }
                val mapping = mappingRepository.list()
                val gamesByAppid = games.associateBy { it.appid }
                val activeJobAppids = classifyBulkSelection(games, jobs).busy.mapTo(HashSet()) { it.appid }
                val plan = buildMultiPlan(ids, details, mapping, gamesByAppid, activeJobAppids)
                deletePlan = DeletePlanUiState.Ready(ids, plan)
            } catch (e: VaultApiError) {
                deletePlan = DeletePlanUiState.Error(ids, e.message ?: strings.deletePlanErrorFallback())
            }
        }
    }

    fun closeDeleteConfirm() {
        deletePlan = DeletePlanUiState.Hidden
    }

    /**
     * Issues one `DELETE /v1/cache/{appid}` per id (mirrors `library.js`'s
     * `confirmDelete`: "no batch endpoint... one DELETE per selected app",
     * mockup-notes.md round 6's documented backend-implication note). The
     * toast reports what the SERVER actually freed
     * (`CacheDeletionOut.total_bytes_freed`), not [DeletePlanUiState.Ready]'s
     * preview -- the server re-checks every depot's co-owner state
     * immediately before removing it and is the authority, per
     * `MultiPlan.kt`'s kdoc.
     */
    fun confirmDelete(scope: CoroutineScope, ids: List<Int>) {
        scope.launch {
            var freedTotal = 0L
            var failedCount = 0
            for (id in ids) {
                try {
                    freedTotal += cacheRepository.delete(id).total_bytes_freed
                } catch (_: VaultApiError) {
                    failedCount++
                }
            }
            deletePlan = DeletePlanUiState.Hidden
            exitSelect()
            refreshNow(scope)
            toast = strings.freed(formatFreed(freedTotal), failedCount)
        }
    }

    private fun formatFreed(bytes: Long): String = formatBytesGB(bytes) ?: ZERO_GB_LABEL
}

/** "0 GB" is not localizable copy in the same sense the rest of this file's
 * strings are -- it is [formatBytesGB]'s own unit suffix (already a plain
 * Kotlin literal there, ported from `web/js/lib/format.js`'s `gb()`), used
 * here only as the floor value when nothing was freed. Kept as a constant
 * next to its only caller rather than a resource for that reason. */
private const val ZERO_GB_LABEL = "0 GB"

/** Bulk-delete confirm dialog state -- mirrors `library.js`'s
 * `openDeleteConfirm`/`renderDeletePlan` three phases (calculating,
 * ready-with-plan, calculation failed). */
sealed class DeletePlanUiState {
    data object Hidden : DeletePlanUiState()
    data class Loading(val ids: List<Int>) : DeletePlanUiState()
    data class Ready(val ids: List<Int>, val plan: MultiPlan) : DeletePlanUiState()
    data class Error(val ids: List<Int>, val message: String) : DeletePlanUiState()
}
