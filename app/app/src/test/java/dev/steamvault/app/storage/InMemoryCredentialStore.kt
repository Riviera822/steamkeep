package dev.steamvault.app.storage

/**
 * In-memory [CredentialStore] fake for JVM tests (WP 4b.2 brief) — a plain
 * `Map`, no Android Keystore involved, so anything that depends on
 * [CredentialStore] (repositories, API-client key wiring) can be tested on
 * the JVM without a device. Not a test CLASS itself (no `@Test` methods) —
 * see `InMemoryCredentialStoreTest` for the contract it is pinned against.
 *
 * [seed] (WP 4h.4 review fix) lets a test simulate "an existing install
 * already has raw pref data" BEFORE construction runs — its one real use
 * is `InMemoryCredentialStoreTest`'s migration pin: seeding
 * [LEGACY_STEAM_WEB_API_KEY_PREF_NAME] and observing it gone afterward,
 * the same shape [EncryptedCredentialStore]'s real constructor-time scrub
 * has but cannot be exercised on the JVM directly. Every other call site
 * keeps the no-arg default and is unaffected.
 */
class InMemoryCredentialStore(seed: Map<String, String> = emptyMap()) : CredentialStore {
    private val values = mutableMapOf<String, String>().apply { putAll(seed) }

    init {
        // Mirrors EncryptedCredentialStore's real constructor-time
        // migration (same shared, pure `legacyPrefKeysToScrub` function)
        // -- see CredentialStore.kt's kdoc.
        for (key in legacyPrefKeysToScrub(values.keys.toSet())) {
            values.remove(key)
        }
    }

    /** Test-only introspection (WP 4h.4 migration pin): whether a RAW key
     * name is present in the backing map, bypassing the [CredentialStore]
     * interface entirely -- which, by design, has no accessor for the
     * retired legacy key. */
    internal fun containsRawKeyForTest(key: String): Boolean = values.containsKey(key)

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

    override fun clearSteamIdentity() {
        values.remove(KEY_STEAM_ID64)
        values.remove(KEY_STEAM_PERSONA_NAME)
        // Restored (WP 4h.4 review fix), mirroring EncryptedCredentialStore's
        // own restored line -- see that class's kdoc.
        values.remove(LEGACY_STEAM_WEB_API_KEY_PREF_NAME)
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
    }
}
