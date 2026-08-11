package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.status.StatusKind

/**
 * Game display-status logic (WP 4b.4) — Kotlin port of
 * `web/js/lib/game-status.js`'s `dispKind`/`statusAct`/`hasContent` trio
 * (docs/design/vault-app-mockup.html, docs/design/vault-app-mockup-NOTES.md
 * round 5/6) onto the REAL `GET /v1/games` / `GET /v1/jobs` shapes
 * (`net/model/Games.kt`, `net/model/Jobs.kt`), same as the web port. Pure
 * functions only — no Compose, no network — every branch is unit-testable
 * on the plain JVM (`GameStatusTest`).
 *
 * Reuses [StatusKind] (WP 4b.1's status-icon enum) rather than introducing
 * a parallel kind type the way the web `KIND` object does — this module
 * only ever returns [StatusKind.CACHED]/[StatusKind.NONE]/
 * [StatusKind.RUNNING]/[StatusKind.PAUSED]/[StatusKind.ERROR], the same
 * narrower set web/js/lib/game-status.js documents (no `stale` — see below).
 *
 * **Divergence 1 — no "stale" status, same as the web port.** `GameSummary`
 * (`net/model/Games.kt`) has no oracle/stale field folded in yet
 * (api/README.md: "a Phase-4 decision to make once the UI knows how it
 * wants to render it") — this WP ships without the "Update ready"
 * state/chip/glyph, matching the web's WP 4a.3 decision
 * (docs/WORKPACKAGES.md's recorded divergence). [StatusKind.STALE] exists
 * on the enum (WP 4b.1's fuller status-icon gallery) but [dispKind] never
 * returns it.
 *
 * **Divergence 2 — no live progress percentage.** `JobSummary` carries no
 * byte-level progress field (mirrors `web/js/lib/game-status.js`'s
 * documented divergence for the real `JobSummary` shape) — the capsule pill
 * shows the status icon alone while a job is running/paused, and the cached
 * size once `size_bytes` is real. `JobSummary`'s fields (`updated`/
 * `up_to_date`/`summary_parse_ok`) are only ever written once, at job
 * finish (`api/vault_api/jobs.py::finish_job`) — never mutated while a job
 * is `running`/`paused` — so, unlike the web port's `log_excerpt` concern
 * (that field isn't even in the polled list shape here — see
 * `net/model/Jobs.kt`'s `JobSummary` kdoc), two polls of a genuinely
 * unchanged running job already produce field-for-field IDENTICAL
 * [JobSummary] values. [isJobStateTransition] still exists (ported 1:1) as
 * the documented guard for the general case and to keep the two frontends'
 * decision logic symmetric — see `ui/library/logic/GameCardModel.kt`'s kdoc
 * for how this feeds Compose's own skip-on-equal-parameters mechanism.
 *
 * **Cache-content invariant, ported (mockup round 5, finding 6): "cached"
 * requires visible bytes.** A `GET /v1/games` row can legitimately report
 * `status: "done"` with `size_bytes: null` (api/README.md "Last cached
 * remnants") — [hasVisibleCacheContent] is what downgrades that to the
 * honest "Not cached" card instead of a green badge over nothing.
 *
 * Naming note (read before touching `MultiPlan.kt`): [hasVisibleCacheContent]
 * (BYTES-based, "does the grid show this as cached right now") is a
 * DIFFERENT predicate from [hasProtectedCacheContent] (STATUS-based, mirrors
 * `deletion._has_cache_content` / web's `hasProtectedCacheContent` — "does
 * this app's mapping protect a shared depot from deletion"). The two can
 * disagree (the remnant case above) — that is not a bug, it is why they are
 * two functions.
 */

/** Job statuses that occupy this app's card with a live indicator. Queued
 * jobs are deliberately excluded (mockup parity: a queued job shows in the
 * Downloads FIFO queue, WP 4b.5, not on the Library card). GC jobs are
 * excluded too: pause/resume and the download pill are prefill-only
 * concepts (api/README.md job control table: pause on a GC job is `409`),
 * so a GC job for this appid must never drive its library card into a
 * "running" download state. */
private val LIVE_JOB_STATUSES = setOf("running", "paused")

/** Find the job (if any) that should drive this app's library card. */
fun findLiveJob(jobs: List<JobSummary>, appid: Int): JobSummary? =
    jobs.firstOrNull { it.appid == appid && it.type == "prefill" && it.status in LIVE_JOB_STATUSES }

/** Build an `appid -> liveJob` lookup once per tick instead of re-scanning
 * the jobs list per card (O(n) instead of O(cards*jobs)). */
fun indexLiveJobsByAppid(jobs: List<JobSummary>): Map<Int, JobSummary> {
    val map = LinkedHashMap<Int, JobSummary>()
    for (j in jobs) {
        if (j.type == "prefill" && j.status in LIVE_JOB_STATUSES) map[j.appid] = j
    }
    return map
}

/** Byte-based: does the grid have real content to show as cached right now?
 * See module kdoc for why this is distinct from [hasProtectedCacheContent]. */
fun hasVisibleCacheContent(game: GameSummary): Boolean =
    (game.size_bytes ?: 0L) > 0L

/**
 * Status-based: mirrors the server's own shared-depot protection predicate
 * (`deletion._has_cache_content`, already ported once as web's
 * `hasProtectedCacheContent`): an app "has cache content" unless it is
 * `idle`, has never been prefilled, AND has no active job. Used by
 * `MultiPlan.kt` to decide whether an OTHER app protects a shared depot
 * from a bulk delete — never for what the grid displays.
 */
fun hasProtectedCacheContent(status: String, lastPrefillAt: String?, hasActiveJob: Boolean): Boolean {
    val idle = status == "idle"
    val neverPrefilled = lastPrefillAt == null
    return !(idle && neverPrefilled && !hasActiveJob)
}

/** Overload taking a [GameSummary] directly, for callers that already have one. */
fun hasProtectedCacheContent(game: GameSummary, hasActiveJob: Boolean): Boolean =
    hasProtectedCacheContent(game.status, game.last_prefill_at, hasActiveJob)

/**
 * Does this appid have a real `apps` table row at all (WP 4b.4's
 * [dev.steamvault.app.ui.library.logic.GameCardModel.isKnownToVault] kdoc:
 * "reserved for the detail sheet (WP 4b.6) to decide whether a depot list /
 * delete action exists at all" -- extracted here, WP 4b.6, so
 * [GameCardModel]'s own `buildGameCardModel` and the detail sheet's
 * `DetailController.open` share exactly one formula instead of two
 * hand-copied ones drifting apart). A synthetic [mergeLibrary] row for a
 * Steam-owned-but-never-prefilled game is built with `depot_count = 0`,
 * `last_prefill_at = null`, `status = "idle"` specifically so this returns
 * `false` for it (api/README.md: "the apps row is created at enqueue") —
 * `GET /v1/games/{appid}` would 404 for such an appid today.
 */
fun isKnownToVault(game: GameSummary): Boolean =
    game.depot_count > 0 || game.last_prefill_at != null || game.status != "idle"

/**
 * The status a card should SHOW: a live job overrides the cache state.
 * @param game GameSummary
 * @param liveJob from [indexLiveJobsByAppid], or `null`
 */
fun dispKind(game: GameSummary, liveJob: JobSummary?): StatusKind {
    if (liveJob != null) return if (liveJob.status == "paused") StatusKind.PAUSED else StatusKind.RUNNING
    if (game.status == "error") return StatusKind.ERROR
    return if (hasVisibleCacheContent(game)) StatusKind.CACHED else StatusKind.NONE
}

/** What tapping the capsule pill / list-row icon does — `null` when there is
 * no honest action (mirrors the mockup's rule: a non-actionable icon renders
 * as a plain, non-clickable icon, never a button). */
enum class StatusActionType { DOWNLOAD, RETRY, PAUSE, RESUME }

data class StatusAction(val type: StatusActionType)

/**
 * @param selecting `true` while multi-select is active — a tap must toggle
 *   selection instead of firing the action (mockup parity).
 */
fun statusAction(game: GameSummary, liveJob: JobSummary?, selecting: Boolean): StatusAction? {
    if (selecting) return null
    if (liveJob != null) {
        return when (liveJob.status) {
            "running" -> StatusAction(StatusActionType.PAUSE)
            "paused" -> StatusAction(StatusActionType.RESUME)
            else -> null
        }
    }
    return when (dispKind(game, null)) {
        StatusKind.NONE -> StatusAction(StatusActionType.DOWNLOAD)
        StatusKind.ERROR -> StatusAction(StatusActionType.RETRY)
        else -> null // cached — never a silent re-download (mockup round 5 rule)
    }
}

/**
 * Round-7 rule, ported: decide whether a job transition on this appid is a
 * genuine STATE change (rebuild warranted) or a no-op update that must NOT
 * touch the card. See module kdoc, Divergence 2, for why this real API
 * shape rarely needs to fire on a running job, and
 * `ui/library/logic/GameCardModel.kt` for the mechanism that actually
 * protects the on-screen animation.
 */
fun isJobStateTransition(prevJob: JobSummary?, currJob: JobSummary?): Boolean {
    if (currJob == null) return true // job disappeared (finished/cancelled/removed) -- always structural
    if (prevJob == null) return true // brand-new job row -- always structural
    return prevJob.status != currJob.status
}
