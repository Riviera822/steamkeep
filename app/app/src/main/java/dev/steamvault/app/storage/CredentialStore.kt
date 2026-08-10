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

    /** Clears everything this store holds (e.g. "log out" / "forget this vault"). */
    fun clear()
}

/** Which [dev.steamvault.app.net.profile.ConnectivityProfile] to build from stored settings. */
object ProfileKind {
    const val SYSTEM_VPN = "system_vpn"
    const val PUBLIC_DOMAIN = "public_domain"
}
