package dev.steamvault.app.ui.detail.logic

import dev.steamvault.app.net.model.DepotEntry
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.MappingEntry
import dev.steamvault.app.ui.library.logic.buildMultiPlan
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The detail sheet's delete preview is literally `buildMultiPlan(listOf(appid), ...)`
 * (`DetailController`'s kdoc, `GameDetailSheet.kt`'s `DetailDeleteConfirmDialog`)
 * -- `ui/library/logic/MultiPlanTest.kt` already proves the general set-aware
 * arithmetic with 2-3 id batches. This file pins the SPECIFIC single-id
 * shape the detail sheet actually calls with (WP 4b.6 brief: "single-game
 * delete preview arithmetic ... the ADR-0003 flagship: preview never counts
 * a shared depot as freed [when a cached co-owner outside the batch exists]").
 */
class SingleGameDeletePlanTest {

    private val viewedApp = 440
    private val otherCachedApp = 730
    private val sharedDepot = 900
    private val exclusiveDepot = 441

    private val detail = GameDetail(
        appid = viewedApp,
        name = "Viewed Game",
        status = "done",
        last_prefill_at = "2026-08-01T00:00:00Z",
        last_manifest_check = null,
        depots = listOf(
            DepotEntry(exclusiveDepot, shared = false, size_bytes = 1_000_000_000),
            DepotEntry(sharedDepot, shared = true, size_bytes = 500_000_000),
        ),
        size_bytes = 1_500_000_000,
        needs_force = false,
    )

    private val mapping = listOf(
        MappingEntry(exclusiveDepot, viewedApp),
        MappingEntry(sharedDepot, viewedApp),
        MappingEntry(sharedDepot, otherCachedApp),
    )

    @Test
    fun `ADR-0003 flagship -- a shared depot with a cached co-owner outside the batch is NEVER counted as freed`() {
        val gamesByAppid = mapOf(
            viewedApp to GameSummary(viewedApp, "Viewed Game", "done", "2026-08-01T00:00:00Z", null, 2, 1_500_000_000, false),
            otherCachedApp to GameSummary(otherCachedApp, "Other Cached", "done", "2026-08-01T00:00:00Z", null, 1, 500_000_000, false),
        )
        val plan = buildMultiPlan(listOf(viewedApp), listOf(detail), mapping, gamesByAppid, activeJobAppids = emptySet())

        val sharedRow = plan.rows.first { it.depotid == sharedDepot }
        assertTrue("a cached co-owner outside the single-id batch must protect the depot", !sharedRow.free)
        assertEquals(listOf(otherCachedApp), sharedRow.holderAppids)
        // Only the exclusive depot is freed -- the shared one is KEPT.
        assertEquals(1_000_000_000L, plan.freedBytes)
        assertEquals(500_000_000L, plan.keptBytes)
    }

    @Test
    fun `the same shared depot IS freed once its only other owner is uncached`() {
        val gamesByAppid = mapOf(
            viewedApp to GameSummary(viewedApp, "Viewed Game", "done", "2026-08-01T00:00:00Z", null, 2, 1_500_000_000, false),
            otherCachedApp to GameSummary(otherCachedApp, "Other Cached", "idle", null, null, 1, null, false),
        )
        val plan = buildMultiPlan(listOf(viewedApp), listOf(detail), mapping, gamesByAppid, activeJobAppids = emptySet())

        val sharedRow = plan.rows.first { it.depotid == sharedDepot }
        assertTrue(sharedRow.free)
        assertEquals(1_500_000_000L, plan.freedBytes)
        assertEquals(0L, plan.keptBytes)
    }

    @Test
    fun `a single-element id list still de-dupes to exactly one row per depot`() {
        val gamesByAppid = mapOf(
            viewedApp to GameSummary(viewedApp, "Viewed Game", "done", "2026-08-01T00:00:00Z", null, 2, 1_500_000_000, false),
        )
        val plan = buildMultiPlan(listOf(viewedApp), listOf(detail), mapping, gamesByAppid, activeJobAppids = emptySet())
        assertEquals(2, plan.rows.size)
        assertEquals(1, plan.rows.count { it.depotid == sharedDepot })
    }
}
