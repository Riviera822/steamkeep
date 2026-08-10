package dev.steamvault.app.net.model

import kotlinx.serialization.Serializable

/**
 * `GET /v1/games` row — mirrors `vault_api/routers/games.py::GameSummary`
 * field for field (api/README.md "Endpoints"). Field names are kept
 * VERBATIM (snake_case, matching the wire JSON) rather than renamed to
 * Kotlin camelCase — the same no-renaming-layer decision web/js/api.js
 * documents for the web client, so a payload can be compared against the
 * README table / the Pydantic model source without a mental mapping step.
 *
 * `appid`/`status`/`depot_count` have no sane default (a response missing
 * one of these is a real contract break, not a forward-compat gap) and are
 * therefore required; every field the apps table's schema history added
 * later (`last_manifest_check` schema v4, `needs_force` schema v5) is
 * nullable/defaulted so an older vault-api — or a future field this
 * client stops reading — still decodes (see VaultJson's kdoc).
 */
@Serializable
data class GameSummary(
    val appid: Int,
    val name: String? = null,
    val status: String,
    val last_prefill_at: String? = null,
    val last_manifest_check: String? = null,
    val depot_count: Int,
    val size_bytes: Long? = null,
    val needs_force: Boolean = false,
)

/** One entry of [GameDetail.depots] — `vault_api/routers/games.py::DepotEntry`. */
@Serializable
data class DepotEntry(
    val depotid: Int,
    val shared: Boolean,
    val size_bytes: Long? = null,
)

/** `GET /v1/games/{appid}` — `vault_api/routers/games.py::GameDetail`. */
@Serializable
data class GameDetail(
    val appid: Int,
    val name: String? = null,
    val status: String,
    val last_prefill_at: String? = null,
    val last_manifest_check: String? = null,
    val depots: List<DepotEntry> = emptyList(),
    val size_bytes: Long? = null,
    val needs_force: Boolean = false,
)
