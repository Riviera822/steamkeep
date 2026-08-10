package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private fun game(appid: Int, status: String = "idle", sizeBytes: Long? = null): GameSummary = GameSummary(
    appid = appid,
    name = "Game $appid",
    status = status,
    last_prefill_at = null,
    last_manifest_check = null,
    depot_count = 0,
    size_bytes = sizeBytes,
    needs_force = false,
)

private fun job(appid: Int, type: String = "prefill", status: String = "queued"): JobSummary = JobSummary(
    id = appid,
    appid = appid,
    type = type,
    status = status,
    created_at = "2026-08-01T00:00:00Z",
)

private val NOT_CACHED = game(1, status = "idle", sizeBytes = null)
private val CACHED = game(2, status = "done", sizeBytes = 5_000_000_000)
private val ERRORED = game(3, status = "error", sizeBytes = null)
private val CACHED_2 = game(4, status = "done", sizeBytes = 1_000_000_000)
private val ERRORED_WITH_BYTES = game(5, status = "error", sizeBytes = 2_000_000_000)

class BulkPlanTest {

    // ---- classifyBulkSelection ------------------------------------------

    @Test
    fun `classifyBulkSelection busy (queued running paused prefill) is excluded from both buckets`() {
        val jobs = listOf(job(NOT_CACHED.appid, status = "queued"))
        val result = classifyBulkSelection(listOf(NOT_CACHED, CACHED), jobs)
        assertEquals(listOf(1), result.busy.map { it.appid })
        assertEquals(emptyList<Int>(), result.needsDownload.map { it.appid })
        assertEquals(listOf(2), result.current.map { it.appid })
    }

    @Test
    fun `classifyBulkSelection none AND error both land in needsDownload`() {
        val result = classifyBulkSelection(listOf(NOT_CACHED, ERRORED, CACHED), emptyList())
        assertEquals(listOf(1, 3), result.needsDownload.map { it.appid }.sorted())
        assertEquals(listOf(2), result.current.map { it.appid })
    }

    @Test
    fun `classifyBulkSelection a gc job for the appid does not count as busy`() {
        val jobs = listOf(job(NOT_CACHED.appid, type = "gc", status = "running"))
        val result = classifyBulkSelection(listOf(NOT_CACHED), jobs)
        assertTrue(result.busy.isEmpty())
        assertEquals(listOf(1), result.needsDownload.map { it.appid })
    }

    // ---- buildBulkDownloadPlan: the three visible outcomes ---------------

    @Test
    fun `plan something needs downloading -- primary targets exactly that, skip count spelled out`() {
        val classification = classifyBulkSelection(listOf(NOT_CACHED, CACHED), emptyList())
        val plan = buildBulkDownloadPlan(classification, 2)
        assertTrue(plan.primaryEnabled)
        assertEquals("Download 1 of 2", plan.primaryLabel)
        assertEquals(listOf(1), plan.primaryTargets)
        assertTrue(plan.note.contains("already cached"))
        assertNull(plan.secondaryLabel)
    }

    @Test
    fun `plan nothing needs downloading, some already cached -- disabled plus explicit re-download secondary`() {
        val classification = classifyBulkSelection(listOf(CACHED, CACHED_2), emptyList())
        val plan = buildBulkDownloadPlan(classification, 2)
        assertEquals(false, plan.primaryEnabled)
        assertEquals("All cached — nothing to download", plan.primaryLabel)
        assertEquals(emptyList<Int>(), plan.primaryTargets)
        assertEquals("Re-download 2", plan.secondaryLabel)
        assertEquals(listOf(2, 4), plan.secondaryTargets)
    }

    @Test
    fun `plan everything picked is already busy -- disabled Already downloading, no secondary`() {
        val jobs = listOf(job(NOT_CACHED.appid, status = "running"))
        val classification = classifyBulkSelection(listOf(NOT_CACHED), jobs)
        val plan = buildBulkDownloadPlan(classification, 1)
        assertEquals(false, plan.primaryEnabled)
        assertEquals("Already downloading", plan.primaryLabel)
        assertNull(plan.secondaryLabel)
    }

    @Test
    fun `plan a single not-cached game -- singular label, no skip note`() {
        val classification = classifyBulkSelection(listOf(NOT_CACHED), emptyList())
        val plan = buildBulkDownloadPlan(classification, 1)
        assertEquals("Download 1 game", plan.primaryLabel)
        assertEquals("", plan.note)
    }

    // ---- classifyBulkDeleteEligibility ------------------------------------

    @Test
    fun `eligibility a cached game is eligible`() {
        val eligible = classifyBulkDeleteEligibility(listOf(CACHED), emptyList())
        assertEquals(listOf(2), eligible.map { it.appid })
    }

    @Test
    fun `eligibility error with ZERO visible bytes is EXCLUDED -- would 404`() {
        val eligible = classifyBulkDeleteEligibility(listOf(ERRORED), emptyList())
        assertTrue(eligible.isEmpty())
    }

    @Test
    fun `eligibility error WITH visible bytes is INCLUDED -- half-deleted run has content to clean up`() {
        val eligible = classifyBulkDeleteEligibility(listOf(ERRORED_WITH_BYTES), emptyList())
        assertEquals(listOf(5), eligible.map { it.appid })
    }

    @Test
    fun `eligibility a not-cached game is excluded`() {
        val eligible = classifyBulkDeleteEligibility(listOf(NOT_CACHED), emptyList())
        assertTrue(eligible.isEmpty())
    }

    @Test
    fun `eligibility a busy game is excluded even if it has bytes`() {
        val jobs = listOf(job(CACHED.appid, status = "running"))
        val eligible = classifyBulkDeleteEligibility(listOf(CACHED), jobs)
        assertTrue(eligible.isEmpty())
    }

    @Test
    fun `eligibility mixed selection returns exactly the eligible subset`() {
        val eligible = classifyBulkDeleteEligibility(
            listOf(NOT_CACHED, CACHED, ERRORED, ERRORED_WITH_BYTES),
            emptyList(),
        )
        assertEquals(listOf(2, 5), eligible.map { it.appid }.sorted())
    }
}
