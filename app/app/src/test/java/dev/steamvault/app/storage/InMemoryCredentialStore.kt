package dev.steamvault.app.storage

/**
 * In-memory [CredentialStore] fake for JVM tests (WP 4b.2 brief) — a plain
 * `Map`, no Android Keystore involved, so anything that depends on
 * [CredentialStore] (repositories, API-client key wiring) can be tested on
 * the JVM without a device. Not a test CLASS itself (no `@Test` methods) —
 * see `InMemoryCredentialStoreTest` for the contract it is pinned against.
 */
class InMemoryCredentialStore : CredentialStore {
    private val values = mutableMapOf<String, String>()

    override fun getApiKey(): String? = values[KEY_API_KEY]
    override fun setApiKey(key: String?) = set(KEY_API_KEY, key)

    override fun getBaseUrl(): String? = values[KEY_BASE_URL]
    override fun setBaseUrl(url: String?) = set(KEY_BASE_URL, url)

    override fun getProfileKind(): String? = values[KEY_PROFILE_KIND]
    override fun setProfileKind(kind: String?) = set(KEY_PROFILE_KIND, kind)

    override fun getSteamId64(): String? = values[KEY_STEAM_ID64]
    override fun setSteamId64(steamId64: String?) = set(KEY_STEAM_ID64, steamId64)

    override fun getSteamPersonaName(): String? = values[KEY_STEAM_PERSONA_NAME]
    override fun setSteamPersonaName(name: String?) = set(KEY_STEAM_PERSONA_NAME, name)

    override fun getSteamWebApiKey(): String? = values[KEY_STEAM_WEB_API_KEY]
    override fun setSteamWebApiKey(key: String?) = set(KEY_STEAM_WEB_API_KEY, key)

    override fun clearSteamIdentity() {
        values.remove(KEY_STEAM_ID64)
        values.remove(KEY_STEAM_PERSONA_NAME)
        values.remove(KEY_STEAM_WEB_API_KEY)
    }

    override fun clear() {
        values.clear()
    }

    private fun set(key: String, value: String?) {
        if (value == null) values.remove(key) else values[key] = value
    }

    private companion object {
        const val KEY_API_KEY = "apiKey"
        const val KEY_BASE_URL = "baseUrl"
        const val KEY_PROFILE_KIND = "profileKind"
        const val KEY_STEAM_ID64 = "steamId64"
        const val KEY_STEAM_PERSONA_NAME = "steamPersonaName"
        const val KEY_STEAM_WEB_API_KEY = "steamWebApiKey"
    }
}
