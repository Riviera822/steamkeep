package dev.steamvault.app.storage

/**
 * Where the vault-api connection (base URL, connectivity-profile kind, API
 * key) is persisted (WP 4b.2 brief).
 *
 * Extracted as an interface specifically so tests run on the JVM against
 * an in-memory fake ([dev.steamvault.app.storage] test sources'
 * `InMemoryCredentialStore`) — the real implementation
 * ([EncryptedCredentialStore]) needs the Android Keystore, which is not
 * available off-device (app/README.md's "No instrumented tests — no
 * emulator/device is available in this environment", unchanged by this
 * WP).
 *
 * The vault-api key NEVER lands in a plain (unencrypted)
 * `SharedPreferences` file through this interface's one intended
 * production implementation — see [EncryptedCredentialStore]'s kdoc and
 * `EncryptedCredentialStoreSourceTest` for how that is pinned given the
 * JVM-only test constraint above.
 */
interface CredentialStore {
    fun getApiKey(): String?
    fun setApiKey(key: String?)

    fun getBaseUrl(): String?
    fun setBaseUrl(url: String?)

    /** One of [ProfileKind]'s constants, or `null` if never configured. */
    fun getProfileKind(): String?
    fun setProfileKind(kind: String?)

    // ---- WP 4b.3: Steam identity -----------------------------------------
    // Distinct from the vault-api key above: this is the SteamID64 an
    // OpenID sign-in resolved to, an optional cached persona name, and the
    // user's OWN Steam Web API key for on-device GetOwnedGames calls
    // (ADR-0004 decision 2 -- never sent to vault-api, see
    // `net/steam/SteamWebApiClient.kt`'s kdoc and `SteamKeyIsolationTest`).

    /** The signed-in SteamID64 (17-digit decimal string), or `null` if never signed in. */
    fun getSteamId64(): String?
    fun setSteamId64(steamId64: String?)

    /** Cached persona name from `GetPlayerSummaries` (WP brief: "optional"), or `null`. */
    fun getSteamPersonaName(): String?
    fun setSteamPersonaName(name: String?)

    /** The user's own, device-local Steam Web API key, or `null` if not yet entered. */
    fun getSteamWebApiKey(): String?
    fun setSteamWebApiKey(key: String?)

    /**
     * Clears only the three Steam-identity values above (WP brief:
     * "sign-out clears everything") -- leaves the vault-api connection
     * (`apiKey`/`baseUrl`/`profileKind`) untouched, since signing out of
     * Steam is not the same action as forgetting the configured vault.
     */
    fun clearSteamIdentity()

    /** Clears everything this store holds (e.g. "forget this vault" entirely). */
    fun clear()
}

/** Which [dev.steamvault.app.net.profile.ConnectivityProfile] to build from stored settings. */
object ProfileKind {
    const val SYSTEM_VPN = "system_vpn"
    const val PUBLIC_DOMAIN = "public_domain"
}
