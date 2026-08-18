package dev.steamvault.app.storage

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * A STRUCTURAL pin on [EncryptedCredentialStore]'s one guarantee — the
 * vault-api key never lands in a plain, unencrypted `SharedPreferences`
 * file — not a behavioural one.
 *
 * [EncryptedCredentialStore] cannot be instantiated or exercised on the
 * JVM: it needs a real Android Keystore, and no emulator/device is
 * available in this environment (app/README.md's WP 4b.1 note, unchanged
 * here). Reading its source file as text and asserting on it is the same
 * lightweight technique `StatusIconCrossFrontendContractTest`
 * (`app/app/src/test/java/dev/steamvault/app/ui/status/`, WP 4b.1) uses
 * for `strings.xml`/`colors.xml` — applied here to a `.kt` file instead of
 * an XML resource. It is an honest, narrower guarantee than a runtime
 * test would give (it cannot catch a bug in HOW the encrypted store is
 * used, only that no plaintext fallback path exists in the file at all),
 * and that scope is deliberate, not hidden.
 *
 * The regression this catches: a future edit that "fixes" an
 * `EncryptedSharedPreferences.create` failure by falling back to
 * `context.getSharedPreferences(...)` directly — a real, documented
 * historical footgun with this API — would silently downgrade every
 * stored value (the API key included) to plaintext. This test fails the
 * moment that call appears anywhere in the file, without needing a device
 * to catch it.
 */
class EncryptedCredentialStoreSourceTest {

    private val source: String by lazy {
        val file = File(
            "src/main/java/dev/steamvault/app/storage/EncryptedCredentialStore.kt",
        )
        check(file.exists()) { "expected source file at ${file.absolutePath}" }
        file.readText(Charsets.UTF_8)
    }

    @Test
    fun `never calls the plain getSharedPreferences directly`() {
        assertFalse(
            "EncryptedCredentialStore.kt must never call Context.getSharedPreferences(...) " +
                "directly -- all reads/writes must go through EncryptedSharedPreferences.create(...)",
            source.contains("getSharedPreferences("),
        )
    }

    @Test
    fun `does route through EncryptedSharedPreferences dot create`() {
        assertTrue(
            "expected EncryptedCredentialStore.kt to call EncryptedSharedPreferences.create(...)",
            source.contains("EncryptedSharedPreferences.create("),
        )
    }

    @Test
    fun `builds its key via MasterKey, not a hand-rolled key scheme`() {
        assertTrue(
            "expected EncryptedCredentialStore.kt to build its key via MasterKey.Builder",
            source.contains("MasterKey.Builder("),
        )
    }

    /**
     * WP 4h.4 round-2 review catch: the shared `legacyPrefKeysToScrub`
     * function is behaviourally pinned for `InMemoryCredentialStore` (the
     * JVM-testable fake) via `InMemoryCredentialStoreTest`'s `MUTATION
     * PIN`, but nothing in the JVM suite ever CONSTRUCTS a real
     * [EncryptedCredentialStore] or calls its `clearSteamIdentity()` — so
     * the production call sites themselves were unpinned; a diff that
     * deleted BOTH the `init` block's scrub loop and
     * `clearSteamIdentity()`'s restored `editor.remove(...)` line still
     * passed the entire suite (577/0, both variants — measured, not
     * assumed). This is a STRUCTURAL pin, not a behavioural one, for the
     * same JVM/Keystore reason every other test in this file is
     * structural: it proves the call sites exist in the source text, not
     * that they run correctly at runtime.
     */
    @Test
    fun `calls the shared legacy-key scrub at construction and on sign-out`() {
        assertTrue(
            "expected EncryptedCredentialStore.kt's constructor to call legacyPrefKeysToScrub(...) " +
                "(WP 4h.4: the one-time migration that actively removes an existing install's " +
                "abandoned device-local Steam Web API key, rather than leaving it to rot unrevoked)",
            source.contains("legacyPrefKeysToScrub("),
        )
        assertTrue(
            "expected EncryptedCredentialStore.kt's clearSteamIdentity() to also remove " +
                "LEGACY_STEAM_WEB_API_KEY_PREF_NAME on Steam sign-out (belt-and-suspenders with the " +
                "construction-time scrub above)",
            source.contains("editor.remove(LEGACY_STEAM_WEB_API_KEY_PREF_NAME)"),
        )
    }
}
