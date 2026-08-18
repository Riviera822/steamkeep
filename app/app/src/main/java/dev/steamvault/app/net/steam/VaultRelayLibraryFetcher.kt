package dev.steamvault.app.net.steam

import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.net.model.SteamPersona

/**
 * The on-device counterpart to [dev.steamvault.app.net.model.OwnedGame]/
 * [SteamPersona] fetches, so [dev.steamvault.app.repo.SteamIdentityRepositoryImpl]
 * tests can fake it. Same interface shape WP 4b.3's original
 * `SteamWebApiClient`-backed version had -- only the ONE implementation
 * behind it changed (WP 4h.4).
 */
interface SteamLibraryFetcher {
    suspend fun getOwnedGames(steamId64: String): List<OwnedGame>
    suspend fun getPlayerSummary(steamId64: String): SteamPersona?
}

/**
 * **WP 4h.4 (ADR-0004's second addendum) — supersedes the device-local
 * `SteamWebApiClient` this class replaces.** Library/persona data now flows
 * exclusively through vault-api's own Steam relay
 * (`GET /v1/steam/owned-games`, `GET /v1/steam/player-summaries` --
 * `net/VaultApiClient.kt::steamOwnedGames`/`steamPlayerSummaries`),
 * authenticated the same way as every other vault-api call (`X-Api-Key`).
 * There is no fallback to a direct Valve call: a fallback would make WP
 * 4h.0's privacy gate bypassable the instant a Steam Web API key sits on a
 * phone again, and would leave two codepaths (one per source of truth) to
 * maintain forever -- see `app/README.md`'s "Steam library via the vault
 * relay" section and the ADR-0004 addendum for the full reasoning.
 *
 * [vaultApiClientProvider] mirrors the "read fresh on every call" pattern
 * [VaultApiClient]'s own `apiKeyProvider` already uses:
 * [dev.steamvault.app.MainActivity] constructs this class's production
 * instance once (via
 * [dev.steamvault.app.repo.SteamIdentityRepositoryImpl]'s lazy default),
 * long before a vault-api connection necessarily exists (Steam OpenID
 * sign-in, unlike library fetching, is reachable during onboarding, before
 * [dev.steamvault.app.storage.CredentialStore] has a base URL/API key at
 * all) -- so the CURRENT [VaultApiClient] (or `null`, if none is configured
 * yet) must be read at CALL time, never captured once at construction.
 *
 * Throws whatever [VaultApiClient] itself throws (a
 * [dev.steamvault.app.net.error.VaultApiError] -- `409` when no Steam Web
 * API key is configured on vault-api itself, `422` for a rejected
 * `steamid`, `502`/`Network`/etc. for every other upstream failure) --
 * deliberately unwrapped: "whatever the app already does for other vault
 * calls" (WP brief) means every existing `VaultApiError`-aware caller
 * (`ui/settings/logic/SteamLibraryStatus.kt`) already knows how to read it,
 * with no second error type to keep in sync.
 */
class VaultRelayLibraryFetcher(
    private val vaultApiClientProvider: () -> VaultApiClient?,
) : SteamLibraryFetcher {

    override suspend fun getOwnedGames(steamId64: String): List<OwnedGame> =
        requireClient().steamOwnedGames(steamId64).games

    override suspend fun getPlayerSummary(steamId64: String): SteamPersona? {
        val entry = requireClient().steamPlayerSummaries(steamId64).players
            // Cross-check, mirroring `vault_api/steam_relay.py::parse_player_summaries`'s
            // own rule (the relay already applies it server-side; kept here
            // too -- docs/LEARNINGS.md "everything returned is hostile
            // input" applies to a semi-trusted relay's answer as well, not
            // only to Valve's).
            .firstOrNull { it.steamid == steamId64 }
            ?: return null
        return SteamPersona(steamId64 = entry.steamid, personaName = entry.personaname)
    }

    private fun requireClient(): VaultApiClient = vaultApiClientProvider()
        ?: throw IllegalStateException("no vault-api connection configured")
}
