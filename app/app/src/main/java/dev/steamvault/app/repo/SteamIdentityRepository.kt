package dev.steamvault.app.repo

import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.net.steam.PendingLoginState
import dev.steamvault.app.net.steam.SteamLoginState
import dev.steamvault.app.net.steam.SteamOpenIdCallback
import dev.steamvault.app.net.steam.SteamOpenIdClient
import dev.steamvault.app.net.steam.SteamOpenIdConfig
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
     * in 4b.4 — expose the repository, render a count preview only". Kept
     * around for [dev.steamvault.app.ui.identity.IdentityScreen]'s existing
     * "check library size" affordance; superseded for the actual library
     * grid by [ownedGames] below (WP 4b.4), which this delegates to so the
     * two never drift.
     */
    suspend fun ownedGamesCountPreview(): Result<Int>

    /**
     * The full owned-library list (WP 4b.4 brief: "Steam library (owned
     * games) merge per the mockup's model"). `Result.failure` — never a
     * thrown exception — when not signed in, no Web API key is configured,
     * or the on-device `GetOwnedGames` call itself fails; the Library
     * screen's merge logic (`ui/library/logic/LibraryMerge.kt`) treats
     * absence of this data as "the vault-only view must be fully
     * functional" (mockup-notes.md open question 5 / WP brief), not an
     * error state.
     */
    suspend fun ownedGames(): Result<List<OwnedGame>>

    /** Clears everything Steam-identity-related (steamid, persona, Web API key) — WP brief: "sign-out clears everything". */
    fun signOut()
}

class SteamIdentityRepositoryImpl(
    private val credentialStore: CredentialStore,
    private val openIdVerifier: SteamOpenIdVerifier = SteamOpenIdClient(),
    private val libraryFetcher: SteamLibraryFetcher = SteamWebApiClient(
        apiKeyProvider = { credentialStore.getSteamWebApiKey().orEmpty() },
    ),
    /** WP 4b.7 replay-residual fix -- see [PendingLoginState]'s kdoc. Held
     * per-repository-instance (repository lifetime == app process lifetime,
     * same as [MainActivity]'s `by lazy` wiring), not persisted: a login
     * attempt that outlives the process (a killed-and-restarted app while
     * the Custom Tab is open) simply fails closed on the way back in,
     * rather than being silently exempt from the state check. */
    private val pendingLoginState: PendingLoginState = PendingLoginState(),
    /** Overridable ONLY for deterministic tests -- the production default
     * is [SteamLoginState.generate]'s real `SecureRandom` path. */
    private val stateGenerator: () -> String = { SteamLoginState.generate() },
) : SteamIdentityRepository {

    override fun state(): SteamIdentityState = SteamIdentityState(
        steamId64 = credentialStore.getSteamId64(),
        personaName = credentialStore.getSteamPersonaName(),
        hasWebApiKey = !credentialStore.getSteamWebApiKey().isNullOrBlank(),
    )

    override fun buildLoginUrl(): String {
        // WP 4b.7 replay-residual fix: a fresh, single-use random state per
        // login attempt, embedded in return_to and checked BEFORE the
        // network round trip in completeLogin below -- see
        // SteamLoginState.kt's module kdoc for the attack this closes.
        val state = stateGenerator()
        pendingLoginState.start(state)
        val returnTo = "${SteamOpenIdConfig.RETURN_TO}?state=$state"
        return openIdVerifier.buildLoginUrl(returnTo = returnTo, realm = SteamOpenIdConfig.REALM)
    }

    override suspend fun completeLogin(rawCallbackUrl: String): SteamLoginResult {
        val params = SteamOpenIdCallback.parse(rawCallbackUrl)
            ?: return SteamLoginResult.Failure("The Steam sign-in response was malformed or incomplete.")

        // WP 4b.7 replay-residual fix: checked BEFORE signedCoversClaimedId
        // and BEFORE the check_authentication network call (brief:
        // "callback with missing/wrong state rejected BEFORE
        // check_authentication"). consume() is single-use -- a second call
        // with the exact same (valid) callback fails here the second time,
        // since the pending state was already cleared by the first.
        val callbackState = SteamOpenIdCallback.stateFromReturnTo(params.getValue("openid.return_to"))
        if (!pendingLoginState.consume(callbackState)) {
            return SteamLoginResult.Failure("This sign-in link has expired or was already used — please sign in again.")
        }

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

    override suspend fun ownedGamesCountPreview(): Result<Int> =
        ownedGames().map { it.size }

    override suspend fun ownedGames(): Result<List<OwnedGame>> {
        val steamId64 = credentialStore.getSteamId64()
            ?: return Result.failure(IllegalStateException("not signed in with Steam"))
        if (credentialStore.getSteamWebApiKey().isNullOrBlank()) {
            return Result.failure(IllegalStateException("no Steam Web API key configured"))
        }
        return try {
            Result.success(libraryFetcher.getOwnedGames(steamId64))
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    override fun signOut() {
        credentialStore.clearSteamIdentity()
    }
}
