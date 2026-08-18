package dev.steamvault.app.storage

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

private const val PREFS_FILE_NAME = "steamvault_secure_prefs"
private const val PREF_KEY_API_KEY = "vault_api_key"
private const val PREF_KEY_BASE_URL = "vault_base_url"
private const val PREF_KEY_PROFILE_KIND = "vault_profile_kind"
private const val PREF_KEY_STEAM_ID64 = "steam_id64"
private const val PREF_KEY_STEAM_PERSONA_NAME = "steam_persona_name"

// WP 4h.4 removed the "steam_web_api_key" accessor pair entirely (ADR-0004's
// second addendum: library data flows through the vault relay, never a
// device-local key) -- see CredentialStore.kt's kdoc "Migration note" and
// `legacyPrefKeysToScrub` for how an existing install's stored value under
// that pref NAME is actively scrubbed (construction-time migration below),
// not merely left unread.

/**
 * [CredentialStore] backed by [EncryptedSharedPreferences] (androidx
 * `security-crypto`, pinned in `gradle/libs.versions.toml`) — the
 * production implementation used everywhere except tests.
 *
 * The vault-api key must never land in a plain, unencrypted
 * `SharedPreferences` file (app/README.md's WP 4b.1 note: `allowBackup` is
 * already `false` for exactly this class of secret). This class's ONE
 * guarantee is therefore narrow and absolute: every read/write of
 * [getApiKey]/[setApiKey] (and the two companion values) goes through
 * `prefs`, which is `EncryptedSharedPreferences.create(...)`'s return
 * value — this file never calls the plain `Context.getSharedPreferences`
 * itself, not even as a fallback.
 *
 * This class is compiled but NOT exercised by any JVM unit test — it needs
 * a real Android Keystore, unavailable off-device (see app/README.md's "No
 * instrumented tests" note, unchanged by this WP; [CredentialStore] exists
 * as an interface precisely so everything that DEPENDS on credential
 * storage can still be tested against `InMemoryCredentialStore` instead).
 * The one guarantee above is pinned structurally, not behaviourally:
 * `EncryptedCredentialStoreSourceTest` reads this very file's source text
 * and asserts no bare call to the plain, unencrypted preferences lookup
 * exists in it anywhere — a regression that "fixed" a
 * `EncryptedSharedPreferences.create`
 * failure by silently falling back to a plaintext file (a real,
 * documented historical footgun with this API) would fail that test
 * immediately, without needing a device to catch it.
 */
class EncryptedCredentialStore(context: Context) : CredentialStore {

    private val prefs = run {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            PREFS_FILE_NAME,
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    init {
        // WP 4h.4 migration (review fix): an existing install may still
        // have a value sitting under the retired device-local Steam Web
        // API key's pref name -- scrub it once, the first time this store
        // is constructed after upgrading, per `legacyPrefKeysToScrub`'s
        // kdoc (CredentialStore.kt) and ADR-0010's "a credential nobody is
        // prompted to revoke" reasoning.
        for (key in legacyPrefKeysToScrub(prefs.all.keys)) {
            prefs.edit().remove(key).apply()
        }
    }

    override fun getApiKey(): String? = prefs.getString(PREF_KEY_API_KEY, null)
    override fun setApiKey(key: String?) = putOrRemove(PREF_KEY_API_KEY, key)

    override fun getBaseUrl(): String? = prefs.getString(PREF_KEY_BASE_URL, null)
    override fun setBaseUrl(url: String?) = putOrRemove(PREF_KEY_BASE_URL, url)

    override fun getProfileKind(): String? = prefs.getString(PREF_KEY_PROFILE_KIND, null)
    override fun setProfileKind(kind: String?) = putOrRemove(PREF_KEY_PROFILE_KIND, kind)

    override fun getSteamId64(): String? = prefs.getString(PREF_KEY_STEAM_ID64, null)
    override fun setSteamId64(steamId64: String?) = putOrRemove(PREF_KEY_STEAM_ID64, steamId64)

    override fun getSteamPersonaName(): String? = prefs.getString(PREF_KEY_STEAM_PERSONA_NAME, null)
    override fun setSteamPersonaName(name: String?) = putOrRemove(PREF_KEY_STEAM_PERSONA_NAME, name)

    override fun clearSteamIdentity() {
        val editor = prefs.edit()
        editor.remove(PREF_KEY_STEAM_ID64)
        editor.remove(PREF_KEY_STEAM_PERSONA_NAME)
        // Restored (WP 4h.4 review fix): this line was deleted alongside
        // the accessor pair it used to sit next to, which silently turned
        // "sign out of Steam" into "leave the retired key in place" --
        // belt-and-suspenders with the construction-time scrub in `init`
        // above (harmless once that has already run; not redundant for
        // the narrow window before the NEXT construction if a legacy
        // value somehow reappears).
        editor.remove(LEGACY_STEAM_WEB_API_KEY_PREF_NAME)
        editor.apply()
    }

    override fun clear() {
        prefs.edit().clear().apply()
    }

    private fun putOrRemove(key: String, value: String?) {
        val editor = prefs.edit()
        if (value == null) editor.remove(key) else editor.putString(key, value)
        editor.apply()
    }
}
