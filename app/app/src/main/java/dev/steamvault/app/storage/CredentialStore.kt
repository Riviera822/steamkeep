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
    // OpenID sign-in resolved to, plus an optional cached persona name.
    //
    // WP 4h.4 (ADR-0004's second addendum) removed the third field this
    // section used to carry: the user's OWN, device-local Steam Web API
    // key. Library/persona data now flows exclusively through vault-api's
    // relay (`net/steam/VaultRelayLibraryFetcher.kt`), authenticated with
    // the SAME `apiKey` this interface already stores above -- there is no
    // second, device-local key left to store at all.
    //
    // **Migration note (corrected — review catch).** An earlier draft of
    // this WP argued the now-removed device-local Steam Web API key could
    // be left sitting, unread, in `EncryptedSharedPreferences` forever --
    // "never read again and never leaves the device needs no active
    // scrubbing." That argument does not survive contact with ADR-0010's
    // OWN logic applied to a credential instead of a privacy flag: a
    // credential that outlives the code that used it is one nobody is
    // EVER prompted to revoke. A key an operator forgot exists, sitting
    // encrypted-but-present on a phone, is exactly the thing worth
    // actively deleting, not merely orphaning. So this codebase does both:
    //
    // 1. [legacyPrefKeysToScrub] runs once at CONSTRUCTION time in every
    //    [CredentialStore] implementation ([EncryptedCredentialStore]'s
    //    real `init` block; the JVM-testable
    //    [dev.steamvault.app.storage] test fixture `InMemoryCredentialStore`
    //    mirrors it identically, which is what makes the migration
    //    ACTUALLY unit-testable despite the JVM/Keystore constraint above).
    // 2. [EncryptedCredentialStore.clearSteamIdentity] ALSO removes the
    //    legacy pref name directly (belt-and-suspenders: harmless once
    //    (1) has already run, but restores the exact line a prior draft
    //    of this diff had deleted).
    //
    // Either an install already sees the key gone the next time its
    // `CredentialStore` is constructed (typically: next app launch), or —
    // for the narrow window before that — sign-out clears it too. An
    // operator who is uneasy in the meantime, or who wants the STRONGER
    // guarantee of the key being revoked on Valve's side (not just deleted
    // from this app), can do so directly at
    // https://steamcommunity.com/dev/apikey (`app/README.md`'s "Steam
    // library via the vault relay" section repeats this sentence for
    // upgraders).

    /** The signed-in SteamID64 (17-digit decimal string), or `null` if never signed in. */
    fun getSteamId64(): String?
    fun setSteamId64(steamId64: String?)

    /** Cached persona name from `GetPlayerSummaries` (WP brief: "optional"), or `null`. */
    fun getSteamPersonaName(): String?
    fun setSteamPersonaName(name: String?)

    /**
     * Clears only the two Steam-identity values above (WP brief:
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

/**
 * The retired device-local Steam Web API key's raw pref NAME (WP 4h.4,
 * ADR-0004's second addendum). Every accessor that used to read/write it
 * is gone — this constant exists ONLY so the one-time migration below
 * (and its test) have a single named source of truth for "which legacy
 * key", rather than a literal string repeated in two places that could
 * silently drift apart.
 */
internal const val LEGACY_STEAM_WEB_API_KEY_PREF_NAME = "steam_web_api_key"

/**
 * Which of [existingKeys] a fresh [CredentialStore] construction must
 * scrub as a one-time migration (WP 4h.4) — today just
 * [LEGACY_STEAM_WEB_API_KEY_PREF_NAME], if present. Pure, no Android/
 * Keystore dependency, so it is unit-testable directly even though
 * [EncryptedCredentialStore] itself cannot run on the JVM: BOTH
 * [EncryptedCredentialStore]'s real `init` block and the JVM-testable
 * `InMemoryCredentialStore` test fixture call this with their OWN raw key
 * set and remove whatever it returns — one shared decision, not
 * reimplemented per store (docs/LEARNINGS.md's "pinned-the-fake" rule:
 * a fake that reimplements production logic proves nothing about the real
 * path; a fake that calls the SAME function the real path calls does).
 */
internal fun legacyPrefKeysToScrub(existingKeys: Set<String>): Set<String> =
    if (LEGACY_STEAM_WEB_API_KEY_PREF_NAME in existingKeys) {
        setOf(LEGACY_STEAM_WEB_API_KEY_PREF_NAME)
    } else {
        emptySet()
    }
