package dev.steamvault.app.repo

import dev.steamvault.app.net.steam.SteamOpenIdCallback
import dev.steamvault.app.net.steam.SteamOpenIdClient
import dev.steamvault.app.net.steam.SteamOpenIdVerifier
import dev.steamvault.app.net.steam.SteamWebApiClient
import dev.steamvault.app.net.steam.SteamLibraryFetcher
import dev.steamvault.app.storage.CredentialStore

/** Everything the identity screen needs to render (WP 4b.3 brief: "signed-in state shows steamid/persona + sign-out"). */
data class SteamIdentityState(
    val steamId64: String?,
    val personaName: String?,
    val hasWebApiKey: Boolean,
) {
    val isSignedIn: Boolean get() = steamId64 != null
}

/** The outcome of [SteamIdentityRepository.completeLogin] -- never throws, always one of these two. */
sealed class SteamLoginResult {
    data class Success(val steamId64: String) : SteamLoginResult()

    /** [reason] is always a fixed, human-readable, key/secret-free string -- safe to show in the UI as-is. */
    data class Failure(val reason: String) : SteamLoginResult()
}

/**
 * Steam identity (OpenID sign-in state + the on-device Steam Web API key
 * and library) — the data layer WP 4b.3 asks for. Nothing here calls
 * vault-api; this repository and [dev.steamvault.app.net.VaultApiClient]
 * are entirely separate (ADR-0004 decision 2, `SteamKeyIsolationTest`
 * pins the isolation structurally).
 */
interface SteamIdentityRepository {
    /** Current persisted state, read fresh from [CredentialStore] every call. */
    fun state(): SteamIdentityState

    /** The `checkid_setup` URL to open in a Custom Tab. */
    fun buildLoginUrl(): String

    /**
     * Verifies [rawCallbackUrl] (the raw deep-link URL Valve redirected
     * back to, e.g. `Intent.dataString`) against Valve via
     * `check_authentication`, and on success persists the extracted
     * SteamID64. Never throws — every failure mode (malformed callback,
     * an unsigned `claimed_id`, a `check_authentication` rejection, an
     * invalid SteamID64) becomes [SteamLoginResult.Failure].
     */
    suspend fun completeLogin(rawCallbackUrl: String): SteamLoginResult

    /** Persists the user's own Steam Web API key (entered manually — never obtained via OpenID). */
    fun setWebApiKey(key: String)

    /**
     * Best-effort persona-name refresh via `GetPlayerSummaries` — WP brief:
     * "persona name optional". Requires both a signed-in [state] and a
     * configured Web API key; returns `false` (without throwing) if either
     * is missing or the call fails, `true` once the persona name is
     * persisted.
     */
    suspend fun refreshPersonaName(): Boolean

    /**
     * `GetOwnedGames`'s game count only — WP brief: "library fetch happens
     * in 4b.4 — expose the repository, render a count preview only". The
     * full [dev.steamvault.app.net.model.OwnedGame] list this pulls is
     * intentionally not exposed by this interface yet.
     */
    suspend fun ownedGamesCountPreview(): Result<Int>

    /** Clears everything Steam-identity-related (steamid, persona, Web API key) — WP brief: "sign-out clears everything". */
    fun signOut()
}

class SteamIdentityRepositoryImpl(
    private val credentialStore: CredentialStore,
    private val openIdVerifier: SteamOpenIdVerifier = SteamOpenIdClient(),
    private val libraryFetcher: SteamLibraryFetcher = SteamWebApiClient(
        apiKeyProvider = { credentialStore.getSteamWebApiKey().orEmpty() },
    ),
) : SteamIdentityRepository {

    override fun state(): SteamIdentityState = SteamIdentityState(
        steamId64 = credentialStore.getSteamId64(),
        personaName = credentialStore.getSteamPersonaName(),
        hasWebApiKey = !credentialStore.getSteamWebApiKey().isNullOrBlank(),
    )

    override fun buildLoginUrl(): String = openIdVerifier.buildLoginUrl()

    override suspend fun completeLogin(rawCallbackUrl: String): SteamLoginResult {
        val params = SteamOpenIdCallback.parse(rawCallbackUrl)
            ?: return SteamLoginResult.Failure("The Steam sign-in response was malformed or incomplete.")

        if (!SteamOpenIdCallback.signedCoversClaimedId(params.getValue("openid.signed"))) {
            return SteamLoginResult.Failure("Steam's response did not sign the account identifier — rejected.")
        }

        val verified = openIdVerifier.checkAuthentication(params)
        if (!verified) {
            return SteamLoginResult.Failure("Steam did not confirm this sign-in — please try again.")
        }

        val steamId64 = SteamOpenIdCallback.steamId64From(params.getValue("openid.claimed_id"))
            ?: return SteamLoginResult.Failure("Steam returned an account identifier this app could not recognize.")

        credentialStore.setSteamId64(steamId64)
        return SteamLoginResult.Success(steamId64)
    }

    override fun setWebApiKey(key: String) {
        credentialStore.setSteamWebApiKey(key)
    }

    override suspend fun refreshPersonaName(): Boolean {
        val steamId64 = credentialStore.getSteamId64() ?: return false
        if (credentialStore.getSteamWebApiKey().isNullOrBlank()) return false
        val persona = try {
            libraryFetcher.getPlayerSummary(steamId64)
        } catch (_: Exception) {
            null
        } ?: return false
        credentialStore.setSteamPersonaName(persona.personaName)
        return true
    }

    override suspend fun ownedGamesCountPreview(): Result<Int> {
        val steamId64 = credentialStore.getSteamId64()
            ?: return Result.failure(IllegalStateException("not signed in with Steam"))
        if (credentialStore.getSteamWebApiKey().isNullOrBlank()) {
            return Result.failure(IllegalStateException("no Steam Web API key configured"))
        }
        return try {
            Result.success(libraryFetcher.getOwnedGames(steamId64).size)
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    override fun signOut() {
        credentialStore.clearSteamIdentity()
    }
}
