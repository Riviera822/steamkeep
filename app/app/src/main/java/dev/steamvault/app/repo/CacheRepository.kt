package dev.steamvault.app.repo

import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.model.CacheDeletionOut

/**
 * Suspend-based repository over `DELETE /v1/cache/{appid}` (WP 4b.4 brief:
 * bulk delete). Same thin "typed name for the client call" shape as
 * [GamesRepository]/[JobsRepository]/[MappingRepository]. Deliberately just
 * [delete] for now -- `GET /v1/cache/summary` and the GC endpoints have no
 * caller yet (this WP's scope is the Library grid's bulk delete, not the
 * detail/GC flows, which are WP 4b.6), same "add it with the WP that needs
 * it" rule the other repositories document.
 */
interface CacheRepository {
    suspend fun delete(appid: Int): CacheDeletionOut
}

class VaultCacheRepository(private val client: VaultApiClient) : CacheRepository {
    override suspend fun delete(appid: Int): CacheDeletionOut = client.deleteCache(appid)
}
