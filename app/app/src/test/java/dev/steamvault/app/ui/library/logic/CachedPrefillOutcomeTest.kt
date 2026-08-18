package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.PrefillJobRef
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * JVM port of `web/tests/cached-prefill-outcome.test.js` (WP 4c-app,
 * porting WP 4c-web's own pins) against
 * `ui/library/logic/CachedPrefillOutcome.kt`. Test names mirror the web
 * suite's own naming, including both review-round-1 "BLOCKER REGRESSION"
 * cases, so a diff against the web test file reads directly.
 */
class CachedPrefillOutcomeTest {

    private fun ref(appid: Int, status: String, deduplicated: Boolean, jobId: Int = appid) =
        PrefillJobRef(appid = appid, job_id = jobId, status = status, deduplicated = deduplicated)

    private fun game(appid: Int, needsForce: Boolean) =
        GameSummary(appid = appid, status = "idle", depot_count = 1, needs_force = needsForce)

    // ---------------------------------------------------------------------
    // partitionCachedPrefillOutcome
    // ---------------------------------------------------------------------

    @Test
    fun `partitionCachedPrefillOutcome sorts a mixed response into the four dedupe-shape buckets`() {
        val refs = listOf(
            ref(1, "queued", false),
            ref(2, "queued", true),
            ref(3, "running", true),
            ref(4, "paused", true),
            ref(5, "queued", false),
        )
        val p = partitionCachedPrefillOutcome(refs)
        assertEquals(listOf(1, 5), p.queued.map { it.appid })
        assertEquals(listOf(2), p.alreadyQueued.map { it.appid })
        assertEquals(listOf(3), p.alreadyRunning.map { it.appid })
        assertEquals(listOf(4), p.alreadyPaused.map { it.appid })
        assertEquals(5, p.total)
    }

    @Test
    fun `partitionCachedPrefillOutcome empty selection`() {
        val p = partitionCachedPrefillOutcome(emptyList())
        assertTrue(p.queued.isEmpty())
        assertTrue(p.alreadyQueued.isEmpty())
        assertTrue(p.alreadyRunning.isEmpty())
        assertTrue(p.alreadyPaused.isEmpty())
        assertEquals(0, p.total)
    }

    @Test
    fun `partitionCachedPrefillOutcome null input treated as empty (defensive)`() {
        assertEquals(0, partitionCachedPrefillOutcome(null).total)
    }

    @Test
    fun `partitionCachedPrefillOutcome a deduplicated QUEUED entry lands in alreadyQueued, not alreadyRunning`() {
        val p = partitionCachedPrefillOutcome(listOf(ref(9, "queued", true)))
        assertEquals(listOf(9), p.alreadyQueued.map { it.appid })
        assertTrue(p.alreadyRunning.isEmpty())
    }

    /** The unknown-status catch-all (WP brief: "the four-bucket partition incl.
     * the unknown-status catch-all"): only "queued"/"running"/"paused" are
     * contractually possible per api/README.md, but the contract says "never
     * a terminal status" without enumerating every non-terminal one forever —
     * a future in-flight status must stay VISIBLE (alreadyRunning), never
     * silently vanish from every bucket (same posture as the WP 4b.5 lesson
     * pinned on both frontends). */
    @Test
    fun `partitionCachedPrefillOutcome a deduplicated non-queued-non-paused status lands in alreadyRunning`() {
        val p = partitionCachedPrefillOutcome(listOf(ref(5, "some_future_status", true)))
        assertEquals(listOf(5), p.alreadyRunning.map { it.appid })
        assertTrue(p.alreadyPaused.isEmpty())
        assertTrue(p.alreadyQueued.isEmpty())
    }

    // ---------------------------------------------------------------------
    // summarizeCachedPrefillOutcome — the mutation-worthy pins the WP brief
    // asks for, plus the review round 1 blocker regression pins (ported).
    // ---------------------------------------------------------------------

    @Test
    fun `summarizeCachedPrefillOutcome empty selection reads as a normal outcome, not a failure`() {
        val summary = summarizeCachedPrefillOutcome(emptyList())
        assertEquals("Nothing cached to check.", summary.message)
        assertFalse(summary.warn)
        // Mutation pin: the message must not contain failure-shaped words.
        assertFalse(Regex("fail|error|nothing happened", RegexOption.IGNORE_CASE).containsMatchIn(summary.message))
    }

    // BLOCKER REGRESSION (web review round 1, live-reproduced in headless
    // Chrome, ported verbatim): an empty response used to still get a
    // "(N forced...)" note appended from the caller's OWN games snapshot,
    // regardless of what the server actually queued — "Nothing cached to
    // check. (1 forced...)" claims work that provably did not start. The
    // fix moved the note INSIDE this function, gated on
    // `partition.queued.isNotEmpty()`.
    @Test
    fun `BLOCKER REGRESSION empty response + a stale needs_force game in games results in message EXACTLY 'Nothing cached to check_'`() {
        val games = listOf(game(2010070, needsForce = true))
        val summary = summarizeCachedPrefillOutcome(emptyList(), games)
        assertEquals("Nothing cached to check.", summary.message)
        assertFalse(summary.warn)
    }

    // BLOCKER REGRESSION, second shape (ported): an ALL-DEDUPLICATED outcome
    // (nothing queued fresh) must not credit a forced note either, even when
    // one of the deduplicated apps happens to carry needs_force = true in
    // the caller's games snapshot — that app's force decision was made
    // whenever its existing job was first queued, not by this press.
    @Test
    fun `BLOCKER REGRESSION all-deduplicated response + a forced game among them yields no forced note`() {
        val games = listOf(game(2010030, needsForce = true))
        val refs = listOf(ref(2010030, "running", true, jobId = 900001))
        val summary = summarizeCachedPrefillOutcome(refs, games)
        assertEquals("1 already in progress", summary.message)
        assertFalse(Regex("forced", RegexOption.IGNORE_CASE).containsMatchIn(summary.message))
    }

    @Test
    fun `summarizeCachedPrefillOutcome all-new selection uses the check-and-update wording`() {
        val summary = summarizeCachedPrefillOutcome(listOf(ref(1, "queued", false), ref(2, "queued", false)))
        assertEquals("2 queued for check & update", summary.message)
        assertFalse(summary.warn)
    }

    /** MUTATION PIN (WP brief, explicit ask): "an explicit assertion that an
     * alreadyQueued-only outcome does not warn" — the web port shipped
     * without this and a widened `warn` expression (OR-ing in alreadyQueued)
     * passed every one of its 414 tests unnoticed. */
    @Test
    fun `MUTATION PIN -- a QUEUED dedupe is worded 'already queued', distinct from 'already in progress', and does NOT warn`() {
        val summary = summarizeCachedPrefillOutcome(listOf(ref(9, "queued", true)))
        assertEquals("1 already queued", summary.message)
        assertFalse(Regex("in progress", RegexOption.IGNORE_CASE).containsMatchIn(summary.message))
        assertFalse(
            "warn must be paused-only -- an alreadyQueued dedupe must never warn",
            summary.warn,
        )
    }

    @Test
    fun `MUTATION PIN -- a paused dedupe is NEVER worded as queued or started`() {
        val summary = summarizeCachedPrefillOutcome(listOf(ref(3, "paused", true)))
        assertTrue(Regex("paused", RegexOption.IGNORE_CASE).containsMatchIn(summary.message))
        assertFalse(Regex("queued for check", RegexOption.IGNORE_CASE).containsMatchIn(summary.message))
        assertFalse(Regex("already queued", RegexOption.IGNORE_CASE).containsMatchIn(summary.message))
        assertFalse(Regex("\\bstarted\\b", RegexOption.IGNORE_CASE).containsMatchIn(summary.message))
        assertTrue(summary.warn)
    }

    @Test
    fun `summarizeCachedPrefillOutcome multiple paused entries use plural wording`() {
        val summary = summarizeCachedPrefillOutcome(listOf(ref(3, "paused", true), ref(4, "paused", true)))
        assertTrue(summary.message.contains("2 paused — resume or cancel them first"))
    }

    @Test
    fun `summarizeCachedPrefillOutcome already-running only, no warn`() {
        val summary = summarizeCachedPrefillOutcome(listOf(ref(2, "running", true)))
        assertEquals("1 already in progress", summary.message)
        assertFalse(summary.warn)
    }

    @Test
    fun `summarizeCachedPrefillOutcome mixed outcome reports every bucket distinctly`() {
        val summary = summarizeCachedPrefillOutcome(
            listOf(ref(1, "queued", false), ref(2, "queued", true), ref(3, "running", true), ref(4, "paused", true)),
        )
        assertTrue(summary.message.contains("1 queued for check & update"))
        assertTrue(summary.message.contains("1 already queued"))
        assertTrue(summary.message.contains("1 already in progress"))
        assertTrue(summary.message.contains("1 paused"))
        assertTrue(summary.warn)
    }

    @Test
    fun `summarizeCachedPrefillOutcome forced note appears when a NEWLY queued app carries needs_force`() {
        val games = listOf(game(1, needsForce = true))
        val summary = summarizeCachedPrefillOutcome(listOf(ref(1, "queued", false)), games)
        assertTrue(summary.message.contains("1 queued for check & update (1 forced"))
    }

    @Test
    fun `summarizeCachedPrefillOutcome no games argument at all never throws and simply omits the note`() {
        val summary = summarizeCachedPrefillOutcome(listOf(ref(1, "queued", false)))
        assertEquals("1 queued for check & update", summary.message)
    }

    /** MUTATION PIN (S1, Opus review round on this WP): the forced-note
     * SCOPING (`countForcedCachedGames(p.queued, games)`, never the whole
     * snapshot) previously had no standalone kill -- the `p.queued.isNotEmpty()`
     * gate above masks a scoping mutant whenever the queued bucket itself
     * has nothing forced in it, which is exactly this shape: one fresh,
     * NON-forced app is queued, and an UNRELATED app elsewhere in the
     * snapshot carries `needs_force = true`. A mutant that swaps the scoped
     * call for a count over the entire `games` list wrongly appends
     * "(1 forced ...)" here -- the surviving half of the web port's own
     * round-1 blocker (`docs/PROJECT_PLAN.md` §7 Phase 4c). This is the
     * SAME failure class `CachedPrefillOutcome.kt`'s own kdoc warns a future
     * reader about: the gate and the scoping are independent layers
     * (docs/LEARNINGS.md "redundant defence layers cannot be pinned by one
     * end-to-end test" — WP 4b.2), and until this test existed, only the
     * gate half was pinned. */
    @Test
    fun `MUTATION PIN -- the forced note is scoped to the queued bucket, not the whole games snapshot`() {
        val refs = listOf(ref(1, "queued", false))
        val games = listOf(game(1, needsForce = false), game(2, needsForce = true))
        val summary = summarizeCachedPrefillOutcome(refs, games)
        assertEquals("1 queued for check & update", summary.message)
    }

    // ---------------------------------------------------------------------
    // countForcedCachedGames — scoped to a queuedRefs bucket, not the whole
    // games snapshot (review round 1 blocker fix, ported).
    // ---------------------------------------------------------------------

    @Test
    fun `countForcedCachedGames counts only appids present in queuedRefs AND needs_force in games`() {
        val queuedRefs = listOf(ref(1, "queued", false), ref(2, "queued", false))
        val games = listOf(game(1, needsForce = true), game(2, needsForce = false), game(3, needsForce = true))
        assertEquals(1, countForcedCachedGames(queuedRefs, games))
    }

    @Test
    fun `countForcedCachedGames an appid in queuedRefs with no matching game is not counted`() {
        assertEquals(0, countForcedCachedGames(listOf(ref(999, "queued", false)), emptyList()))
    }

    @Test
    fun `countForcedCachedGames empty or null input is 0`() {
        assertEquals(0, countForcedCachedGames(emptyList(), emptyList()))
        assertEquals(0, countForcedCachedGames(null, null))
    }

    // ---------------------------------------------------------------------
    // describeCachedPrefillError — the mid-loop 5xx honesty rule.
    // ---------------------------------------------------------------------

    @Test
    fun `MUTATION PIN -- describeCachedPrefillError a SERVER-kind error asks the caller to re-read jobs`() {
        val err = VaultApiError.Server("boom", status = 500)
        val desc = describeCachedPrefillError(err)
        assertTrue(desc.refresh)
        assertTrue(desc.warn)
        assertFalse(Regex("nothing happened", RegexOption.IGNORE_CASE).containsMatchIn(desc.message))
    }

    @Test
    fun `describeCachedPrefillError every non-SERVER kind does not force a refresh`() {
        val kinds = listOf(
            VaultApiError.Auth("nope", status = 401, detail = "denied"),
            VaultApiError.Validation("nope", status = 422, detail = "denied"),
            VaultApiError.NotFound("nope", status = 404, detail = "denied"),
            VaultApiError.Network("nope"),
            VaultApiError.Unknown("nope", status = 418, detail = "denied"),
        )
        for (err in kinds) {
            assertFalse("kind ${err.kind} must not force a refresh", describeCachedPrefillError(err).refresh)
        }
    }

    @Test
    fun `describeCachedPrefillError prefers the server's detail text when present`() {
        val err = VaultApiError.Validation("generic", status = 422, detail = "specific reason")
        assertEquals("specific reason", describeCachedPrefillError(err).message)
    }

    @Test
    fun `describeCachedPrefillError never throws on a non-VaultApiError input`() {
        val desc = describeCachedPrefillError(RuntimeException("plain"))
        assertFalse(desc.refresh)
        assertEquals("plain", desc.message)
    }

    @Test
    fun `describeCachedPrefillError a null error falls back to the fixed literal`() {
        val desc = describeCachedPrefillError(null)
        assertEquals("Could not start the check.", desc.message)
        assertFalse(desc.refresh)
    }

    // ---------------------------------------------------------------------
    // CheckAndUpdateAction — the in-flight guard (THE pin).
    //
    // Review round 1, S4 (web, ported): the guard check happens
    // SYNCHRONOUSLY inside run()'s pre-suspend prefix, so `calls` is already
    // deterministic (1 if the guard is intact, 2 if it was removed) the
    // instant both run() calls have reached their next suspension point —
    // asserted via testScheduler.runCurrent() below, before the gate
    // resolves, so a broken guard fails this assertion immediately rather
    // than needing a real hang/timeout to observe the mutation.
    // ---------------------------------------------------------------------

    @Test
    fun `CheckAndUpdateAction a second run while the first is pending is a no-op (fails fast, does not hang)`() = runTest {
        var calls = 0
        val gate = CompletableDeferred<List<PrefillJobRef>>()
        val action = CheckAndUpdateAction(fetcher = {
            calls++
            gate.await()
        })

        var resultOne: CheckAndUpdateResult? = null
        var resultTwo: CheckAndUpdateResult? = null

        launch { resultOne = action.run() }
        testScheduler.runCurrent()
        assertTrue(action.isInFlight())

        launch { resultTwo = action.run() }
        testScheduler.runCurrent()

        // Synchronous assertion — see kdoc above.
        assertEquals(1, calls)
        assertEquals(CheckAndUpdateResult.Skipped, resultTwo)

        val settled = listOf(ref(1, "queued", false))
        gate.complete(settled)
        testScheduler.advanceUntilIdle()

        assertEquals(CheckAndUpdateResult.Success(settled), resultOne)
        assertFalse(action.isInFlight())
    }

    @Test
    fun `CheckAndUpdateAction a run after the previous one settled calls the fetcher again`() = runTest {
        var calls = 0
        val action = CheckAndUpdateAction(fetcher = { calls++; emptyList() })
        action.run()
        action.run()
        assertEquals(2, calls)
    }

    @Test
    fun `CheckAndUpdateAction a rejected fetch clears the in-flight flag, reports Failure, and stays usable`() = runTest {
        val boom = IllegalStateException("network down")
        val action = CheckAndUpdateAction(fetcher = { throw boom })

        val result = action.run()

        assertEquals(CheckAndUpdateResult.Failure(boom), result)
        assertFalse(action.isInFlight())

        var calls = 0
        val action2 = CheckAndUpdateAction(fetcher = { calls++; emptyList() })
        action2.run()
        assertEquals(1, calls)
    }
}
