package dev.steamvault.app.ui.downloads.logic

/**
 * History-row log-excerpt display selection + the lazy-fetch state machine
 * (WP 4b.5) — Kotlin port of `web/js/lib/log-excerpt.js`.
 *
 * `GET /v1/jobs` (the fast-polled list) omits `log_excerpt` on purpose
 * (api/README.md: "Omits log_excerpt on purpose — this is the polling
 * list"); only `GET /v1/jobs/{id}` carries it. A history row's excerpt is
 * therefore always a LAZY fetch triggered by expanding that one row, never
 * something the jobs poll already has.
 *
 * [selectExcerptDisplay] is pure DOM/Compose-free presentation logic,
 * exactly mirroring `log-excerpt.js`'s `selectExcerptDisplay` branch order.
 * [ExcerptCache] is the stateful half — "fetch once per job, cache for the
 * session, allow a retry after a failure" — kept independent of
 * [dev.steamvault.app.repo.JobsRepository] (it takes a plain suspend
 * fetch lambda instead) purely so it is testable with a canned fetcher, no
 * network/Android dependency at all.
 */
enum class ExcerptState { COLLAPSED, LOADING, ERROR, EMPTY, READY }

data class ExcerptDisplay(
    val state: ExcerptState,
    val lines: List<String> = emptyList(),
    val truncated: Boolean = false,
    val message: String? = null,
)

/**
 * Per-job fetch state. `excerpt == null` means "never successfully
 * fetched" — distinct from a job that genuinely produced no log output
 * (represented as `excerpt = ""`), mirroring `log-excerpt.js`'s
 * `undefined`-vs-`""` distinction with Kotlin's `null`-vs-`""`.
 */
data class ExcerptFetchState(
    val expanded: Boolean = false,
    val loading: Boolean = false,
    val error: String? = null,
    val excerpt: String? = null,
)

private const val TRUNCATION_MARKER = "[...truncated...]"

/** Pure: what a history row should render for [state]. Branch order matches
 * `log-excerpt.js::selectExcerptDisplay` exactly. */
fun selectExcerptDisplay(state: ExcerptFetchState): ExcerptDisplay {
    if (!state.expanded) return ExcerptDisplay(ExcerptState.COLLAPSED)
    if (state.loading) return ExcerptDisplay(ExcerptState.LOADING)
    if (state.error != null) return ExcerptDisplay(ExcerptState.ERROR, message = state.error)
    val text = state.excerpt ?: ""
    if (text.isBlank()) return ExcerptDisplay(ExcerptState.EMPTY)
    val truncated = text.startsWith(TRUNCATION_MARKER)
    val body = if (truncated) text.substring(TRUNCATION_MARKER.length).trimStart('\n') else text
    return ExcerptDisplay(ExcerptState.READY, lines = body.split("\n"), truncated = truncated)
}

/**
 * The stateful half: one [ExcerptFetchState] per job id, held in a plain
 * (non-Compose) map so this class is usable from a JVM unit test with no
 * Android/Compose runtime at all. `DownloadsController` wraps this and
 * bumps its own `mutableStateOf` version counter after every mutation to
 * drive Compose recomposition — see that class's kdoc.
 *
 * @param fetchExcerpt fetches `GET /v1/jobs/{id}` and returns its
 *   `log_excerpt` (empty string, never `null`, for "fetched successfully,
 *   nothing to show") — production wiring is
 *   `jobsRepository.detail(id).log_excerpt ?: ""` wrapped in a
 *   [VaultApiError][dev.steamvault.app.net.error.VaultApiError] ->
 *   [Result.failure] catch; tests supply a canned lambda instead.
 */
class ExcerptCache(private val fetchExcerpt: suspend (Int) -> Result<String>) {
    private val states = HashMap<Int, ExcerptFetchState>()

    fun stateFor(jobId: Int): ExcerptFetchState = states[jobId] ?: ExcerptFetchState()

    /** Flips `expanded` and returns the new state (so a caller can decide
     * whether to kick off [ensureLoaded] without a second map lookup). */
    fun toggleExpanded(jobId: Int): ExcerptFetchState {
        val next = stateFor(jobId).let { it.copy(expanded = !it.expanded) }
        states[jobId] = next
        return next
    }

    /**
     * Fetch [jobId]'s log excerpt exactly once per session, guarded by
     * "never successfully fetched and not already in flight" — mirrors
     * `log-excerpt.js::ensureExcerptLoaded`'s
     * `st.excerpt !== undefined || st.loading` guard. A prior FAILURE does
     * NOT poison the cache: `excerpt` stays `null` on failure, so the next
     * call (e.g. collapse then re-expand) retries rather than being stuck
     * showing a stale error forever.
     */
    suspend fun ensureLoaded(jobId: Int) {
        val current = stateFor(jobId)
        if (current.excerpt != null || current.loading) return
        states[jobId] = current.copy(loading = true)
        val result = fetchExcerpt(jobId)
        // Re-read rather than reusing `current`: `expanded` (or anything
        // else) may have changed while this suspend call was in flight.
        val afterFetch = stateFor(jobId)
        states[jobId] = result.fold(
            onSuccess = { text -> afterFetch.copy(loading = false, excerpt = text, error = null) },
            onFailure = { e -> afterFetch.copy(loading = false, error = e.message ?: "Request failed") },
        )
    }
}
