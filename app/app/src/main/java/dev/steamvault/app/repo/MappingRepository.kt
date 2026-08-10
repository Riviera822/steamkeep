package dev.steamvault.app.repo

import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.model.MappingEntry

/**
 * Suspend-based repository over `GET /v1/mapping` (WP 4b.4 brief). Thin, one
 * method — same "typed name for the client call" shape as
 * [GamesRepository]/[JobsRepository]. Only caller today is the bulk-delete
 * confirm flow ([dev.steamvault.app.ui.library.logic.buildMultiPlan] needs
 * the full depot->app table to resolve co-owners; a game's own
 * `GET /v1/games/{appid}` response only says `shared: true/false`, never
 * WHICH other apps).
 */
interface MappingRepository {
    suspend fun list(): List<MappingEntry>
}

class VaultMappingRepository(private val client: VaultApiClient) : MappingRepository {
    override suspend fun list(): List<MappingEntry> = client.mapping()
}
