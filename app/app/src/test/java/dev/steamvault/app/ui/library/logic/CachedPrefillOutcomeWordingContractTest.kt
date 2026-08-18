package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.PrefillJobRef
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Literal cross-frontend wording contract for `CachedPrefillOutcome.kt`
 * (WP 4c-app brief: "a literal cross-frontend contract test for every
 * wording string, hand-transcribed in the test, never derived from the
 * enum under test"). Every expected string below is hand-transcribed
 * directly from `web/js/lib/cached-prefill-outcome.js`'s own literals — it
 * is never built via the same string-template pieces `CachedPrefillOutcome.kt`
 * itself uses, so a wording drift in either the joiner, the per-bucket
 * phrase, or the forced-note suffix fails one of these tests by a literal
 * mismatch rather than by two derivations happening to agree.
 *
 * Same technique `StatusIconCrossFrontendContractTest` already applies to
 * `StatusKind`'s wire names/labels (docs/LEARNINGS.md "Android (Phase
 * 4b)": "a derived round-trip is circular and cannot detect drift from the
 * other frontend").
 */
class CachedPrefillOutcomeWordingContractTest {

    private fun ref(appid: Int, status: String, deduplicated: Boolean, jobId: Int = appid) =
        PrefillJobRef(appid = appid, job_id = jobId, status = status, deduplicated = deduplicated)

    private fun game(appid: Int, needsForce: Boolean) =
        GameSummary(appid = appid, status = "idle", depot_count = 1, needs_force = needsForce)

    @Test
    fun `wording -- empty selection`() {
        assertEquals("Nothing cached to check.", summarizeCachedPrefillOutcome(emptyList()).message)
    }

    @Test
    fun `wording -- a single brand-new job`() {
        assertEquals(
            "1 queued for check & update",
            summarizeCachedPrefillOutcome(listOf(ref(1, "queued", false))).message,
        )
    }

    @Test
    fun `wording -- multiple brand-new jobs`() {
        assertEquals(
            "2 queued for check & update",
            summarizeCachedPrefillOutcome(listOf(ref(1, "queued", false), ref(2, "queued", false))).message,
        )
    }

    @Test
    fun `wording -- a single already-queued dedupe`() {
        assertEquals(
            "1 already queued",
            summarizeCachedPrefillOutcome(listOf(ref(9, "queued", true))).message,
        )
    }

    @Test
    fun `wording -- a single already-running dedupe`() {
        assertEquals(
            "1 already in progress",
            summarizeCachedPrefillOutcome(listOf(ref(2, "running", true))).message,
        )
    }

    @Test
    fun `wording -- a single paused dedupe uses the singular pronoun`() {
        assertEquals(
            "1 paused — resume or cancel it first",
            summarizeCachedPrefillOutcome(listOf(ref(3, "paused", true))).message,
        )
    }

    @Test
    fun `wording -- multiple paused dedupes use the plural pronoun`() {
        assertEquals(
            "2 paused — resume or cancel them first",
            summarizeCachedPrefillOutcome(listOf(ref(3, "paused", true), ref(4, "paused", true))).message,
        )
    }

    @Test
    fun `wording -- a mixed outcome joins every bucket with the middle-dot separator, in bucket order`() {
        val summary = summarizeCachedPrefillOutcome(
            listOf(ref(1, "queued", false), ref(2, "queued", true), ref(3, "running", true), ref(4, "paused", true)),
        )
        assertEquals(
            "1 queued for check & update · 1 already queued · 1 already in progress · " +
                "1 paused — resume or cancel it first",
            summary.message,
        )
    }

    @Test
    fun `wording -- the forced note is appended once at the end with the exact literal suffix`() {
        val games = listOf(game(1, needsForce = true))
        val summary = summarizeCachedPrefillOutcome(listOf(ref(1, "queued", false)), games)
        assertEquals(
            "1 queued for check & update (1 forced — those run full, disk-speed re-checks and may take longer)",
            summary.message,
        )
    }

    @Test
    fun `wording -- a forced note with more than one forced app pluralizes the count only, not the noun`() {
        val refs = listOf(ref(1, "queued", false), ref(2, "queued", false))
        val games = listOf(game(1, needsForce = true), game(2, needsForce = true))
        val summary = summarizeCachedPrefillOutcome(refs, games)
        assertEquals(
            "2 queued for check & update (2 forced — those run full, disk-speed re-checks and may take longer)",
            summary.message,
        )
    }

    @Test
    fun `wording -- the SERVER error message is the exact literal`() {
        val desc = describeCachedPrefillError(VaultApiError.Server("boom", status = 500))
        assertEquals(
            "The server had trouble partway through — some games may already be queued. " +
                "Re-checking Downloads for the real state…",
            desc.message,
        )
    }

    @Test
    fun `wording -- the generic fallback message when neither a detail nor a message is present`() {
        assertEquals("Could not start the check.", describeCachedPrefillError(null).message)
    }
}
