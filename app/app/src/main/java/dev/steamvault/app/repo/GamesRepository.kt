package dev.steamvault.app.repo

import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary

/**
 * Suspend-based repository over `GET /v1/games`[`/{appid}`] (WP 4b.2
 * brief). Deliberately thin — a typed name for "call the client" — because
 * WorkManager polling wiring is WP 4b.8, not this WP; this interface is
 * the seam that work schedules against, together with the pure cadence
 * decision in [dev.steamvault.app.polling.PollingIntervals] (games poll on
 * a fixed slow interval, no active/inactive distinction the way jobs has).
 */
interface GamesRepository {
    suspend fun list(): List<GameSummary>
    suspend fun detail(appid: Int): GameDetail
}

class VaultGamesRepository(private val client: VaultApiClient) : GamesRepository {
    override suspend fun list(): List<GameSummary> = client.games()
    override suspend fun detail(appid: Int): GameDetail = client.game(appid)
}
