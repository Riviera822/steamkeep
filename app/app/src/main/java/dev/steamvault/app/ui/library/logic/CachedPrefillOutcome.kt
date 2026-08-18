package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.PrefillJobRef
import kotlinx.coroutines.CancellationException

/**
 * "Check & update all cached games" — the Phase 4c Android trigger's pure
 * decision logic (WP 4c-app), a Kotlin port of `web/js/lib/
 * cached-prefill-outcome.js` (WP 4c-web) consuming `POST /v1/prefill/cached`
 * (Phase 4c, WP 4c-api — see api/README.md "Check & update all cached
 * games" for the full, reviewer-verified server contract this module is
 * built against, quoted rather than re-derived). The web module's own
 * header is reproduced below near-verbatim; only the language changes.
 *
 * **The response is a flat `PrefillJobRef[]`** — one entry per selected
 * app, `{appid, job_id, status, deduplicated}` — that silently conflates
 * FOUR different real outcomes if a caller reports only its length:
 *   - a brand-new job just queued (`deduplicated: false`, `status:
 *     "queued"` always, per the contract).
 *   - an already in-flight job that is ALSO still `queued` right now
 *     (`deduplicated: true`, `status: "queued"`) — `enqueue_prefill`
 *     returns the existing job with ITS OWN status, and a job the single
 *     worker has not yet claimed is a completely ordinary thing to dedupe
 *     onto (a double-press before the worker gets to it is the common
 *     case, not an edge case) — reporting this as "already in progress"
 *     would be false, it is still waiting in the FIFO queue.
 *   - an already in-flight job that is RUNNING right now (`deduplicated:
 *     true`, `status: "running"`, or any other in-flight status that is
 *     neither `queued` nor `paused` — the catch-all keeps a future/unknown
 *     status VISIBLE instead of dropping it, the WP 4b.5 lesson pinned on
 *     both frontends) — this press changed nothing for that app, work is
 *     genuinely happening.
 *   - an already in-flight job that is PAUSED (`deduplicated: true`,
 *     `status: "paused"`) — an earlier pause is still in the way and
 *     **nothing starts** for this app until the user resumes or cancels it
 *     (WP 3.12: a paused prefill's on-disk chunks ARE its progress store —
 *     deliberate, not a bug). Reporting this as "queued" or "started" would
 *     be a lie.
 *
 * Plus the empty-selection case (`[]`, still a normal `202`, never an
 * error).
 *
 * [partitionCachedPrefillOutcome]/[summarizeCachedPrefillOutcome] sort a raw
 * response into these buckets as pure functions (no Android framework, no
 * network) so the claims that would otherwise silently mislead a user are
 * each independently testable (`CachedPrefillOutcomeTest`): a `paused`
 * dedupe entry must never be worded as "queued"/"started", a `queued`
 * dedupe entry must never be worded as "already in progress", and an empty
 * response must never be worded as a failure.
 *
 * **The forced-run note is scoped to what THIS press actually queued
 * fresh** (web's own review round 1 blocker, ported here rather than
 * re-discovered): computing it from the WHOLE `GET /v1/games` snapshot
 * instead of [CachedPrefillOutcomePartition.queued] can (and, in the web
 * incarnation, did) claim forced work was starting when the selection was
 * EMPTY, or credit a forced app to a press that only deduplicated (an
 * already-`running`/`queued`/`paused` job's force decision was made
 * whenever IT was first queued, not by this later press). The composition
 * lives here, gated on `partition.queued.isNotEmpty()`, and scoped to only
 * the appids actually in that bucket ([countForcedCachedGames]'s
 * `queuedRefs` parameter) — never in untested view glue.
 *
 * [describeCachedPrefillError] covers the mirror-image failure case: per
 * api/README.md, each app is enqueued in its own committed transaction, so
 * a mid-loop `5xx` can leave the first K apps durably `queued` before the
 * response ever arrives. The honest recovery is re-reading `GET /v1/jobs`,
 * never "nothing happened" — this function is what tells the caller
 * ([dev.steamvault.app.ui.library.LibraryController]) to do that, and only
 * for that one error kind.
 *
 * [CheckAndUpdateAction] is the in-flight guard: the button-lock
 * requirement reduced to something Android-framework-free and testable,
 * the same shape as web's `createCheckAndUpdateAction` — a second [run]
 * while the first is still pending is a no-op rather than firing a second
 * concurrent request.
 *
 * **String-resource rule, narrow exception invoked (see app/README.md's
 * "String resources" conventions — the same exception `BulkPlan.kt`/
 * `LibraryFilters.kt` already use).** Every message literal built in
 * [summarizeCachedPrefillOutcome] and [describeCachedPrefillError] stays a
 * Kotlin string here, NOT a `strings.xml` resource: the wording is
 * "whatever `web/js/lib/cached-prefill-outcome.js` already decided", not an
 * independent Android UI copy decision, and its entire value is the ability
 * to diff line-for-line against that file. `CachedPrefillOutcomeWordingContractTest`
 * pins every one of these literals by hand-transcribed STRING EQUALITY,
 * never derived from this file itself — the two-condition test this
 * exception requires. The BUTTON LABEL that triggers this action
 * (`library_check_update_button`/`_busy` in `strings.xml`) is ordinary
 * static UI chrome and does NOT fall under this exception — it is an
 * independent Android string-resource decision, worded to match the
 * project-wide "check & update, never bare check" honesty rule
 * (`docs/PROJECT_PLAN.md` §7 Phase 4c) rather than ported verbatim from any
 * one web literal.
 */

/**
 * @param queued brand-new jobs (`deduplicated: false`).
 * @param alreadyQueued deduplicated against a job that is STILL `queued`
 *   (not yet claimed by the single worker).
 * @param alreadyRunning deduplicated against a job that is `running` (or
 *   any other non-`paused`/non-`queued` in-flight status, defensively).
 * @param alreadyPaused deduplicated against a `paused` job specifically —
 *   kept as its OWN bucket rather than folded into [alreadyRunning],
 *   because unlike a running OR queued dedupe (something is genuinely
 *   going to happen) a paused dedupe means nothing will happen until the
 *   user acts.
 */
data class CachedPrefillOutcomePartition(
    val queued: List<PrefillJobRef>,
    val alreadyQueued: List<PrefillJobRef>,
    val alreadyRunning: List<PrefillJobRef>,
    val alreadyPaused: List<PrefillJobRef>,
    val total: Int,
)

/**
 * @param refs `POST /v1/prefill/cached`'s raw `202` response body. `null`
 *   is accepted defensively (mirrors the web port's `Array.isArray` guard)
 *   even though the production client never actually returns `null` for a
 *   2xx response — `PrefillJobRef` fields themselves cannot be individually
 *   missing/malformed the way a raw JS object's could, since kotlinx.serialization
 *   already rejects a response that doesn't decode into the list shape
 *   before this function ever sees it.
 */
fun partitionCachedPrefillOutcome(refs: List<PrefillJobRef>?): CachedPrefillOutcomePartition {
    val list = refs ?: emptyList()
    val queued = mutableListOf<PrefillJobRef>()
    val alreadyQueued = mutableListOf<PrefillJobRef>()
    val alreadyRunning = mutableListOf<PrefillJobRef>()
    val alreadyPaused = mutableListOf<PrefillJobRef>()
    for (ref in list) {
        if (!ref.deduplicated) {
            queued.add(ref)
        } else if (ref.status == "paused") {
            alreadyPaused.add(ref)
        } else if (ref.status == "queued") {
            alreadyQueued.add(ref)
        } else {
            alreadyRunning.add(ref)
        }
    }
    return CachedPrefillOutcomePartition(queued, alreadyQueued, alreadyRunning, alreadyPaused, list.size)
}

/**
 * Cached games among ONLY the given [queuedRefs] (a
 * [partitionCachedPrefillOutcome] bucket — the appids THIS press actually
 * enqueued fresh, review round 1 blocker fix ported from web) that carry
 * `needs_force = true` right now. Deliberately NOT a function of the whole
 * `GET /v1/games` snapshot: a deduplicated entry's force decision was
 * already made whenever that job was first queued, not by this press, so
 * crediting it here would misreport work this press did not start.
 * Client-side estimate only — `GET /v1/games` is polled independently of
 * this action and the server re-decides `needs_force` per app at
 * job-claim time, not at selection time (api/README.md "needs_force" — GC
 * execute and deletion can flip it between polls) — a heads-up, not a
 * guarantee, which is why [summarizeCachedPrefillOutcome] phrases it as
 * "may take longer" rather than a promise.
 */
fun countForcedCachedGames(queuedRefs: List<PrefillJobRef>?, games: List<GameSummary>?): Int {
    val gamesByAppid = (games ?: emptyList()).associateBy { it.appid }
    var count = 0
    for (ref in queuedRefs ?: emptyList()) {
        val game = gamesByAppid[ref.appid]
        if (game != null && game.needs_force) count += 1
    }
    return count
}

/**
 * @param warn true only when a paused dedupe is present — that is the one
 *   case that needs the user to go DO something (resume or cancel) rather
 *   than just wait. Deliberately NOT set by [CachedPrefillOutcomePartition.alreadyQueued]
 *   or [CachedPrefillOutcomePartition.alreadyRunning] — both mean "something
 *   is genuinely going to happen", the opposite of "you must act".
 */
data class CachedPrefillSummary(val message: String, val warn: Boolean)

/**
 * The toast text for a successful `POST /v1/prefill/cached` call, built
 * from [partitionCachedPrefillOutcome] so every one of the outcomes reads
 * honestly on its own — including a MIXED result (e.g. some new, one
 * paused) reporting each part distinctly instead of collapsing to one
 * misleading number.
 *
 * @param games `GET /v1/games` snapshot, for the forced-run heads-up note —
 *   optional; omitting it simply omits the note (never throws, never
 *   fabricates a count).
 */
fun summarizeCachedPrefillOutcome(refs: List<PrefillJobRef>?, games: List<GameSummary>? = null): CachedPrefillSummary {
    val p = partitionCachedPrefillOutcome(refs)
    if (p.total == 0) {
        // The empty-selection case (api/README.md: "No cached apps ⇒ [] with a
        // normal 202, never an error") — must read as a normal, unremarkable
        // outcome, never as a failure, and (review round 1 blocker, ported)
        // NEVER followed by a forced-run note: nothing was queued, so there
        // is no "those run full, disk-speed re-checks" to warn about, no
        // matter what `games` says about some unrelated app's `needs_force`.
        return CachedPrefillSummary("Nothing cached to check.", warn = false)
    }

    val parts = mutableListOf<String>()
    if (p.queued.isNotEmpty()) {
        // "check & update", never bare "checking" (docs/PROJECT_PLAN.md §7
        // Phase 4c's honesty rule applies to every string this action
        // produces, not just the button label).
        parts.add("${p.queued.size} queued for check & update")
    }
    if (p.alreadyQueued.isNotEmpty()) {
        // Deliberately distinct from alreadyRunning's wording below: a job
        // still sitting in the FIFO queue is not "in progress" yet.
        parts.add("${p.alreadyQueued.size} already queued")
    }
    if (p.alreadyRunning.isNotEmpty()) {
        parts.add("${p.alreadyRunning.size} already in progress")
    }
    if (p.alreadyPaused.isNotEmpty()) {
        // Deliberately NOT "queued"/"started" — see this file's header and
        // partitionCachedPrefillOutcome's kdoc: nothing happens for these
        // until the user acts.
        val pronoun = if (p.alreadyPaused.size > 1) "them" else "it"
        parts.add("${p.alreadyPaused.size} paused — resume or cancel $pronoun first")
    }
    var message = parts.joinToString(" · ")

    // Forced-run heads-up — gated on p.queued.isNotEmpty() (review round 1
    // blocker, ported: an all-deduplicated outcome queues nothing fresh, so
    // there is nothing here for a forced run to apply to) and scoped to
    // ONLY the appids p.queued actually names (never the whole games
    // snapshot).
    //
    // These are two independent layers and each one alone would suffice:
    // with an empty p.queued the scoping already yields 0. The gate is
    // deliberate belt-and-braces for the blocker the web port shipped with
    // once — a later "simplification" should remove THIS gate, never the
    // scoping, since the scoping is what carries a standalone mutation pin.
    if (p.queued.isNotEmpty()) {
        val forcedCount = countForcedCachedGames(p.queued, games)
        if (forcedCount > 0) {
            message += " ($forcedCount forced — those run full, disk-speed re-checks and may take longer)"
        }
    }

    return CachedPrefillSummary(message, warn = p.alreadyPaused.isNotEmpty())
}

/**
 * How to react to a FAILED `POST /v1/prefill/cached` call.
 *
 * @param refresh true only for the one case that must never read as
 *   "nothing happened": a `5xx`. Per api/README.md ("A mid-loop 5xx leaves
 *   a partial, unreported result — same as POST /v1/prefill"), the route
 *   loops over app ids inside one open connection and enqueues one at a
 *   time, so a `5xx` partway through can still have left the first K apps
 *   durably `queued` before the response body was ever sent. The correct
 *   recovery is re-reading `GET /v1/jobs`, never a blind "retry" or a
 *   message implying the button press did nothing —
 *   [dev.steamvault.app.ui.library.LibraryController] maps `refresh: true`
 *   to the same out-of-cadence re-poll every other action in this screen
 *   already triggers on success.
 *
 *   Every other error kind (401 wrong/missing key, a validation-shaped
 *   4xx, a network failure that never reached the server) genuinely means
 *   no job was queued by THIS call — `refresh: false` for those.
 */
data class CachedPrefillErrorDescription(val message: String, val warn: Boolean, val refresh: Boolean)

/** @param err never assumed to be a [VaultApiError] (defensive: an
 *   unexpected thrown value still gets a safe fallback message). */
fun describeCachedPrefillError(err: Throwable?): CachedPrefillErrorDescription {
    if (err is VaultApiError.Server) {
        return CachedPrefillErrorDescription(
            message = "The server had trouble partway through — some games may already be queued. " +
                "Re-checking Downloads for the real state…",
            warn = true,
            refresh = true,
        )
    }
    val detail = (err as? VaultApiError)?.detail?.takeIf { it.isNotBlank() }
    val message = detail ?: err?.message?.takeIf { it.isNotBlank() } ?: "Could not start the check."
    return CachedPrefillErrorDescription(message, warn = true, refresh = false)
}

/** [CheckAndUpdateAction.run]'s outcome. */
sealed class CheckAndUpdateResult {
    /** A previous call is still pending — `fetcher` was NOT invoked again. */
    data object Skipped : CheckAndUpdateResult()
    data class Success(val refs: List<PrefillJobRef>) : CheckAndUpdateResult()
    data class Failure(val err: Throwable) : CheckAndUpdateResult()
}

/**
 * A run-at-most-one-at-a-time guard around [fetcher]. [run] called while a
 * previous call is still pending returns [CheckAndUpdateResult.Skipped]
 * immediately WITHOUT invoking [fetcher] again — this is "disable the
 * button while in flight" with Compose removed from the picture, the same
 * in-flight guarantee web's `store.js` `ResourceLoop` gives its poll loop,
 * scoped here to one fire-and-settle press instead of a repeating timer
 * chain. [dev.steamvault.app.ui.library.LibraryController] ALSO disables
 * the real button for the same window (belt and suspenders) — this guard
 * is what makes that guarantee provable without a device.
 */
class CheckAndUpdateAction(private val fetcher: suspend () -> List<PrefillJobRef>) {
    @Volatile
    private var inFlight = false

    fun isInFlight(): Boolean = inFlight

    suspend fun run(): CheckAndUpdateResult {
        if (inFlight) return CheckAndUpdateResult.Skipped
        inFlight = true
        try {
            val refs = fetcher()
            return CheckAndUpdateResult.Success(refs)
        } catch (e: CancellationException) {
            throw e // structured concurrency: never swallow cancellation as a "failure"
        } catch (e: Exception) {
            return CheckAndUpdateResult.Failure(e)
        } finally {
            inFlight = false
        }
    }
}
