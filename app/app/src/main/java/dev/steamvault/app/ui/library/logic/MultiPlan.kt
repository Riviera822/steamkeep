package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.DepotEntry
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.MappingEntry

/**
 * Set-aware bulk-delete arithmetic (WP 4b.4) — Kotlin port of
 * `web/js/lib/multiplan.js`'s `buildMultiPlan` (docs/design/
 * vault-app-mockup.html, docs/design/vault-app-mockup-NOTES.md round 6
 * "Multi-delete... the shared-depot arithmetic is genuinely set-aware"):
 * the plan is computed for the WHOLE selection at once, so co-owners INSIDE
 * the batch stop counting as reasons to keep a depot, and a depot mapped by
 * two selected games is only counted once (the bytes exist on disk once).
 *
 * Real-data adaptation (identical to the web port's, same server shapes):
 * `GET /v1/games` has no per-depot breakdown, and `GET /v1/games/{appid}`
 * (per-game depot list + `shared: true/false`) doesn't name WHICH other
 * apps share a depot. This module is therefore pure arithmetic over data
 * the CALLER assembles from three real sources
 * (`ui/library/LibraryStateHolder.kt`'s bulk-delete confirm flow):
 *   - `details`: `GET /v1/games/{appid}` responses for every id being
 *     deleted;
 *   - `mapping`: the full `GET /v1/mapping` table — the only source of
 *     "which OTHER apps map this depot";
 *   - `gamesByAppid` + `activeJobAppids`: the already-polled games/jobs
 *     snapshots — no new poll loop, a one-off detail/mapping fetch only
 *     when the user opens a bulk-delete confirm is not a poll loop.
 *
 * "Has cache content" for judging whether an OTHER app protects a shared
 * depot mirrors the server's real predicate exactly
 * ([hasProtectedCacheContent], reused from `GameStatus.kt`, not
 * reimplemented a third time) — NOT the byte-based "is this visibly cached"
 * check the grid uses. An owner appid this module cannot resolve (missing
 * from `gamesByAppid`) is treated as protecting the depot (fail-closed,
 * mirrors the server's own "unreadable mapping row / no apps row ->
 * protected" rule, api/README.md "Last cached remnants").
 *
 * This is a PREVIEW for the confirm dialog, not the final word: the server
 * re-checks every depot's co-owner state immediately before removing it
 * and is the authority the caller reports back to the user after the real
 * `DELETE /v1/cache/{appid}` calls return, summing the SERVER's
 * `total_bytes_freed`, not this module's prediction.
 */

data class MultiPlanDepotRow(
    val depotid: Int,
    val sizeBytes: Long?,
    val shared: Boolean,
    val others: List<Int>,
    val holderAppids: List<Int>,
    val free: Boolean,
)

data class MultiPlan(
    val ids: List<Int>,
    val rows: List<MultiPlanDepotRow>,
    val sharedRows: List<MultiPlanDepotRow>,
    val freedBytes: Long,
    val keptBytes: Long,
    val occupiedBytes: Long,
)

/**
 * @param ids appids being deleted, as ONE batch.
 * @param details `GET /v1/games/{appid}` responses for every id in [ids].
 * @param mapping the full `GET /v1/mapping` table.
 * @param gamesByAppid the already-polled games snapshot, keyed by appid.
 * @param activeJobAppids appids with a queued/running/paused job right now.
 */
fun buildMultiPlan(
    ids: List<Int>,
    details: List<GameDetail>,
    mapping: List<MappingEntry>,
    gamesByAppid: Map<Int, GameSummary>,
    activeJobAppids: Set<Int>,
): MultiPlan {
    val idSet = ids.toSet()

    // De-dupe depots across the WHOLE selection. Keying by depotid means the
    // map itself already guarantees at most one entry per depot no matter
    // how many selected games list it -- the `if depotid !in depotsSeen`
    // check below is NOT what prevents double-counting; it only decides
    // WHICH game's depot object wins when two selected games both report the
    // same depotid (first one seen, arbitrarily but deterministically by
    // `details` order). See MultiPlanTest for a regression pin on the
    // one-entry-per-depot outcome itself (real depot arithmetic, not this
    // tie-break rule).
    val depotsSeen = LinkedHashMap<Int, DepotEntry>()
    for (detail in details) {
        for (d in detail.depots) {
            if (d.depotid !in depotsSeen) depotsSeen[d.depotid] = d
        }
    }

    val ownersByDepot = HashMap<Int, MutableList<Int>>()
    for (row in mapping) {
        ownersByDepot.getOrPut(row.depotid) { mutableListOf() }.add(row.appid)
    }

    fun protects(appid: Int): Boolean {
        val game = gamesByAppid[appid] ?: return true // fail-closed: unresolvable owner protects the depot
        return hasProtectedCacheContent(game, appid in activeJobAppids)
    }

    val rows = depotsSeen.values.map { d ->
        val owners = ownersByDepot[d.depotid] ?: emptyList()
        // Everyone mapping this depot EXCEPT the batch being deleted -- a
        // co-owner INSIDE the batch is never a reason to keep it (the whole
        // point of computing this per-SET rather than per-game).
        val others = owners.filter { it !in idSet }
        val holderAppids = others.filter { protects(it) }
        MultiPlanDepotRow(
            depotid = d.depotid,
            sizeBytes = d.size_bytes,
            shared = owners.size > 1,
            others = others,
            holderAppids = holderAppids,
            free = holderAppids.isEmpty(),
        )
    }

    fun sum(pred: (MultiPlanDepotRow) -> Boolean): Long =
        rows.filter(pred).sumOf { it.sizeBytes ?: 0L }

    return MultiPlan(
        ids = ids,
        rows = rows,
        sharedRows = rows.filter { it.shared },
        freedBytes = sum { it.free },
        keptBytes = sum { !it.free },
        occupiedBytes = rows.sumOf { it.sizeBytes ?: 0L },
    )
}
