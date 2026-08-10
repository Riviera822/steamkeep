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
}
