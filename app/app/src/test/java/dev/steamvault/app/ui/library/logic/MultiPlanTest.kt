package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.DepotEntry
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.MappingEntry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Mirrors `web/tests/multiplan.test.js` -- same worked example from
 * docs/design/vault-app-mockup-NOTES.md round 6 ("Deleting Nebula Drift +
 * Ironwood Hollow keeps shared depot 228990 because Tundra Protocol still
 * holds it; adding Tundra to the same selection frees it"), renamed for
 * this port but the arithmetic (and the reason it matters) is the same.
 */
class MultiPlanTest {

    private val gameA = 100 // exclusive depot 1001 + shared depot 9000
    private val gameB = 200 // exclusive depot 2001 + shared depot 9000
    private val gameC = 300 // only maps the shared depot 9000
    private val sharedDepot = 9000
    private val exclusiveA = 1001
    private val exclusiveB = 2001

    private fun detail(appid: Int, depots: List<DepotEntry>) = GameDetail(
        appid = appid,
        name = "Game $appid",
        status = "done",
        last_prefill_at = "2026-08-01T00:00:00Z",
        last_manifest_check = null,
        depots = depots,
        size_bytes = null,
        needs_force = false,
    )

    private fun gamesByAppid(overrides: Map<Int, GameSummary> = emptyMap()): Map<Int, GameSummary> {
        fun base(appid: Int) = GameSummary(
            appid = appid,
            name = "Game $appid",
            status = "done",
            last_prefill_at = "2026-08-01T00:00:00Z",
            last_manifest_check = null,
            depot_count = 1,
            size_bytes = null,
            needs_force = false,
        )
        val defaults = mapOf(gameA to base(gameA), gameB to base(gameB), gameC to base(gameC))
        return defaults + overrides
    }

    private val mapping = listOf(
        MappingEntry(exclusiveA, gameA),
        MappingEntry(exclusiveB, gameB),
        MappingEntry(sharedDepot, gameA),
        MappingEntry(sharedDepot, gameB),
        MappingEntry(sharedDepot, gameC),
    )

    private val details = listOf(
        detail(gameA, listOf(DepotEntry(exclusiveA, false, 1_000_000_000), DepotEntry(sharedDepot, true, 500_000_000))),
        detail(gameB, listOf(DepotEntry(exclusiveB, false, 2_000_000_000), DepotEntry(sharedDepot, true, 500_000_000))),
        detail(gameC, listOf(DepotEntry(sharedDepot, true, 500_000_000))),
    )

    @Test
    fun `round-6 scenario -- deleting A+B keeps the shared depot because C still holds it`() {
        val plan = buildMultiPlan(
            ids = listOf(gameA, gameB),
            details = details.filter { it.appid in setOf(gameA, gameB) },
            mapping = mapping,
            gamesByAppid = gamesByAppid(),
            activeJobAppids = emptySet(),
        )
        val sharedRow = plan.rows.first { it.depotid == sharedDepot }
        assertEquals(false, sharedRow.free)
        assertEquals(listOf(gameC), sharedRow.holderAppids)
        assertEquals(3_000_000_000L, plan.freedBytes)
        assertEquals(500_000_000L, plan.keptBytes)
    }

    @Test
    fun `MUTATION TARGET the real set-dedupe -- adding C to the SAME batch frees the shared depot`() {
        // Kill target: removing the `it !in idSet` filter on `others` in
        // MultiPlan.kt's buildMultiPlan makes this test fail (C would still
        // count as protecting the depot even while being deleted itself in
        // the same call).
        val plan = buildMultiPlan(
            ids = listOf(gameA, gameB, gameC),
            details = details,
            mapping = mapping,
            gamesByAppid = gamesByAppid(),
            activeJobAppids = emptySet(),
        )
        val sharedRow = plan.rows.first { it.depotid == sharedDepot }
        assertTrue(sharedRow.free)
        assertEquals(emptyList<Int>(), sharedRow.holderAppids)
        assertEquals(3_500_000_000L, plan.freedBytes)
        assertEquals(0L, plan.keptBytes)
    }

    @Test
    fun `regression pin -- the shared depot is counted exactly ONCE across the batch, not per game`() {
        val plan = buildMultiPlan(
            ids = listOf(gameA, gameB),
            details = details.filter { it.appid in setOf(gameA, gameB) },
            mapping = mapping,
            gamesByAppid = gamesByAppid(),
            activeJobAppids = emptySet(),
        )
        assertEquals(1, plan.rows.count { it.depotid == sharedDepot })
        assertEquals(1_000_000_000L + 2_000_000_000L + 500_000_000L, plan.occupiedBytes)
    }

    @Test
    fun `a co-owner outside the batch that is idle and never-prefilled does NOT protect the depot`() {
        val plan = buildMultiPlan(
            ids = listOf(gameA, gameB),
            details = details.filter { it.appid in setOf(gameA, gameB) },
            mapping = mapping,
            gamesByAppid = gamesByAppid(
                mapOf(gameC to GameSummary(gameC, "Game $gameC", "idle", null, null, 1, null, false)),
            ),
            activeJobAppids = emptySet(),
        )
        val sharedRow = plan.rows.first { it.depotid == sharedDepot }
        assertTrue(sharedRow.free)
        assertEquals(3_500_000_000L, plan.freedBytes)
    }

    @Test
    fun `a co-owner outside the batch with an ACTIVE job protects the depot even if status is idle`() {
        val plan = buildMultiPlan(
            ids = listOf(gameA, gameB),
            details = details.filter { it.appid in setOf(gameA, gameB) },
            mapping = mapping,
            gamesByAppid = gamesByAppid(
                mapOf(gameC to GameSummary(gameC, "Game $gameC", "idle", null, null, 1, null, false)),
            ),
            activeJobAppids = setOf(gameC),
        )
        val sharedRow = plan.rows.first { it.depotid == sharedDepot }
        assertEquals(false, sharedRow.free)
        assertEquals(listOf(gameC), sharedRow.holderAppids)
    }

    @Test
    fun `an unresolvable owner appid fails CLOSED -- the depot is protected`() {
        val games = gamesByAppid().toMutableMap()
        games.remove(gameC)
        val plan = buildMultiPlan(
            ids = listOf(gameA, gameB),
            details = details.filter { it.appid in setOf(gameA, gameB) },
            mapping = mapping,
            gamesByAppid = games,
            activeJobAppids = emptySet(),
        )
        val sharedRow = plan.rows.first { it.depotid == sharedDepot }
        assertEquals(false, sharedRow.free)
    }

    @Test
    fun `no shared depots at all -- everything is freed, sharedRows is empty`() {
        val plan = buildMultiPlan(
            ids = listOf(gameA),
            details = listOf(detail(gameA, listOf(DepotEntry(exclusiveA, false, 1_000_000_000)))),
            mapping = listOf(MappingEntry(exclusiveA, gameA)),
            gamesByAppid = gamesByAppid(),
            activeJobAppids = emptySet(),
        )
        assertTrue(plan.sharedRows.isEmpty())
        assertEquals(1_000_000_000L, plan.freedBytes)
        assertEquals(0L, plan.keptBytes)
    }
}
