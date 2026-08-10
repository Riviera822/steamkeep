package dev.steamvault.app.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import java.io.File

/**
 * Structural pin (same lightweight source-text technique as
 * `EncryptedCredentialStoreSourceTest`/`StatusIconCrossFrontendContractTest`)
 * for the WP 4b.3 brief's "grep-provable: no steam key in any vault-api
 * request builder" requirement -- ADR-0004 decision 2: the Steam Web API
 * key never touches vault-api, and [dev.steamvault.app.net.VaultApiClient]
 * never even references the Steam identity classes that carry it.
 *
 * **Allowlist form (review round NIT).** Rather than checking only
 * `VaultApiClient.kt` in isolation, this walks every `.kt` file under
 * `src/main` and asserts `getSteamWebApiKey` appears ONLY in the files
 * that are actually supposed to touch it -- catching a future regression
 * anywhere in the tree (a new repository, a helper class, a debug screen
 * wired up carelessly), not just a reintroduction inside
 * `VaultApiClient.kt` specifically.
 */
class SteamKeyIsolationTest {

    private val srcMainRoot = File("src/main/java/dev/steamvault/app")

    /**
     * The complete, exact set of files allowed to reference
     * `getSteamWebApiKey` -- the interface declaration, its one production
     * implementation, and the one repository that reads the key to build a
     * [dev.steamvault.app.net.steam.SteamWebApiClient] call. Any other hit
     * (most importantly `net/VaultApiClient.kt`) fails this test.
     */
    private val allowlistForSteamWebApiKeyAccessor = setOf(
        "storage/CredentialStore.kt",
        "storage/EncryptedCredentialStore.kt",
        "repo/SteamIdentityRepository.kt",
    )

    private fun allKotlinFiles(): List<File> {
        check(srcMainRoot.exists()) { "expected source root at ${srcMainRoot.absolutePath}" }
        return srcMainRoot.walkTopDown().filter { it.isFile && it.extension == "kt" }.toList()
    }

    private fun relativePath(file: File): String = file.relativeTo(srcMainRoot).invariantSeparatorsPath

    @Test
    fun `getSteamWebApiKey appears only in the allowlisted credential-store and repository files`() {
        val hits = allKotlinFiles()
            .filter { it.readText(Charsets.UTF_8).contains("getSteamWebApiKey") }
            .map { relativePath(it) }
            .toSet()

        assertEquals(
            "getSteamWebApiKey must appear ONLY in the allowlisted files -- any other file " +
                "referencing it (most importantly net/VaultApiClient.kt) is a new potential path " +
                "for the Steam Web API key to reach somewhere it must not (ADR-0004 decision 2)",
            allowlistForSteamWebApiKeyAccessor,
            hits,
        )
    }

    @Test
    fun `VaultApiClient never references any Steam identity class`() {
        val vaultApiClientSource = File("src/main/java/dev/steamvault/app/net/VaultApiClient.kt").let {
            check(it.exists()) { "expected source file at ${it.absolutePath}" }
            it.readText(Charsets.UTF_8)
        }
        for (symbol in listOf("SteamWebApiClient", "SteamOpenIdClient", "SteamIdentityRepository")) {
            assertFalse(
                "VaultApiClient.kt must never reference $symbol -- Steam identity/library " +
                    "fetching is fully independent of the vault-api client (ADR-0004 decision 2)",
                vaultApiClientSource.contains(symbol),
            )
        }
    }
}
