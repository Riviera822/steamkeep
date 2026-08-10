package dev.steamvault.app.net.model

import kotlinx.serialization.Serializable

/**
 * One row of `GET /v1/mapping` — `vault_api/routers/mapping.py::MappingEntry`.
 * The only source of "which OTHER apps map this depot" (a game's own
 * `GET /v1/games/{appid}` response carries a `shared` boolean, but never
 * names the co-owners) — see [dev.steamvault.app.ui.library.logic.MultiPlan]'s
 * kdoc, ported from `web/js/lib/multiplan.js`'s identical real-data
 * adaptation note.
 */
@Serializable
data class MappingEntry(
    val depotid: Int,
    val appid: Int,
)
