package dev.steamvault.app.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pin (same lightweight source-text technique as
 * `EncryptedCredentialStoreSourceTest`/`StatusIconCrossFrontendContractTest`)
 * for what ADR-0004's Steam-credential isolation still guarantees after WP
 * 4h.4 (the second addendum) removed the device-local Steam Web API key
 * entirely and moved library/persona fetching onto vault-api's relay.
 *
 * **This file's ORIGINAL invariant no longer applies, and pretending it
 * still holds unchanged would lie: `getSteamWebApiKey` cannot appear
 * "only in an allowlist of files that touch it" once every file that used
 * to touch it (`CredentialStore.kt`, `EncryptedCredentialStore.kt`,
 * `SteamIdentityRepository.kt`) has had the accessor deleted outright.**
 * The NEW, narrower, still-true invariant this file pins instead (per the
 * WP brief: "update each pin to state the NEW true invariant... rather
 * than deleting it without a successor"):
 *
 * 1. `getSteamWebApiKey`/`setSteamWebApiKey` — the accessor NAMES
 *    themselves — appear NOWHERE in a SHIPPED source set anymore.
 *    Reintroducing either name anywhere (not just in `VaultApiClient.kt`)
 *    would be the device-local key coming back, which this WP
 *    deliberately removes.
 * 2. `api.steampowered.com` — the ONE host every direct-to-Valve Steam Web
 *    API call used to target — appears NOWHERE in a shipped source set
 *    either. The OpenID sign-in flow this app keeps
 *    (`net/steam/SteamOpenIdClient.kt` et al.) never touched that host to
 *    begin with (it talks to `steamcommunity.com`), so this is not
 *    "outside the OpenID flow" as a carved-out exception — the true
 *    invariant is simply that this literal never appears at all, full
 *    stop.
 * 3. `VaultApiClient.kt` still never references the Steam OpenID identity
 *    classes (`SteamOpenIdClient`, `SteamIdentityRepository`) — narrowed
 *    from the original three-symbol list because `SteamWebApiClient` no
 *    longer exists as a class to reference in the first place; what
 *    remains meaningful is that vault-api's client stays ignorant of HOW
 *    Steam identity/sign-in works, even though it now (correctly) knows
 *    about the Steam relay routes themselves
 *    ([VaultApiClient.steamOwnedGames]/[VaultApiClient.steamPlayerSummaries]).
 *
 * **`src/main` alone is not "the app" (review catch, WP 4h.4).** AGP builds
 * the `debug` variant from `src/main` PLUS `src/debug` merged together —
 * `IdentityScreen.kt` (the very file this WP deleted for referencing the
 * now-gone key concept) lived under `src/debug/java/dev/steamvault/app/
 * ui/identity/`, so a scan that only ever walked `src/main` would have a
 * blind spot exactly where a debug-only screen is exactly the kind of file
 * likely to reintroduce a quick, "it's just for the gallery" reference to
 * either invariant above. [shippedSourceRoots] walks both `src/main` and
 * `src/debug` — every source set AGP actually compiles into a runnable
 * variant — never `src/test` (which legitimately quotes both literals in
 * its own assertion strings, this file included).
 */
class SteamKeyIsolationTest {

    private val srcRoot = File("src")
    private val shippedSourceRoots = listOf(
        File("src/main/java/dev/steamvault/app"),
        File("src/debug/java/dev/steamvault/app"),
    )

    private fun allKotlinFiles(): List<File> {
        for (root in shippedSourceRoots) {
            check(root.exists()) { "expected source root at ${root.absolutePath}" }
        }
        return shippedSourceRoots.flatMap { root ->
            root.walkTopDown().filter { it.isFile && it.extension == "kt" }
        }
    }

    private fun relativePath(file: File): String = file.relativeTo(srcRoot).invariantSeparatorsPath

    @Test
    fun `getSteamWebApiKey and setSteamWebApiKey appear nowhere in a shipped source set -- the device-local key is fully removed`() {
        val hits = allKotlinFiles()
            .filter { it.readText(Charsets.UTF_8).let { text -> text.contains("getSteamWebApiKey") || text.contains("setSteamWebApiKey") } }
            .map { relativePath(it) }

        assertEquals(
            "getSteamWebApiKey/setSteamWebApiKey must not appear ANYWHERE in src/main or src/debug (WP 4h.4, " +
                "ADR-0004's second addendum): the device-local Steam Web API key -- its entry UI, its storage, " +
                "and every accessor for it -- was removed, not hidden. Hits: $hits",
            emptyList<String>(),
            hits,
        )
    }

    @Test
    fun `api_steampowered_com appears nowhere in a shipped source set -- library data flows exclusively through the vault relay`() {
        val hits = allKotlinFiles()
            .filter { it.readText(Charsets.UTF_8).contains("api.steampowered.com") }
            .map { relativePath(it) }

        assertEquals(
            "api.steampowered.com must not appear ANYWHERE in src/main or src/debug (WP 4h.4): this app no " +
                "longer talks to Valve's Web API directly at all -- GET /v1/steam/owned-games and GET " +
                "/v1/steam/player-summaries (vault-api's relay) are the ONLY path to library/persona data now, " +
                "with no fallback. The OpenID sign-in flow this app keeps never referenced this host either (it " +
                "talks to steamcommunity.com), so there is no carved-out exception to state -- the invariant is " +
                "absolute. Hits: $hits",
            emptyList<String>(),
            hits,
        )
    }

    @Test
    fun `VaultApiClient never references the Steam OpenID identity classes`() {
        val vaultApiClientSource = File("src/main/java/dev/steamvault/app/net/VaultApiClient.kt").let {
            check(it.exists()) { "expected source file at ${it.absolutePath}" }
            it.readText(Charsets.UTF_8)
        }
        for (symbol in listOf("SteamOpenIdClient", "SteamIdentityRepository")) {
            assertFalse(
                "VaultApiClient.kt must never reference $symbol -- Steam OpenID identity stays fully " +
                    "independent of vault-api even though library fetching (GET /v1/steam/owned-games, " +
                    "GET /v1/steam/player-summaries) now goes through this exact client (ADR-0004's second " +
                    "addendum, WP 4h.4).",
                vaultApiClientSource.contains(symbol),
            )
        }
        // The positive half of the same claim: this client DOES now carry
        // the relay routes -- a regression that silently dropped them
        // (e.g. reverting to the old exclusion) would fail every caller of
        // VaultRelayLibraryFetcher, but pinning their presence here too
        // makes the "what changed vs. what didn't" story explicit in one
        // place rather than only provable by absence elsewhere.
        assertTrue(
            "VaultApiClient.kt is expected to wrap GET /v1/steam/owned-games as of WP 4h.4",
            vaultApiClientSource.contains("/v1/steam/owned-games"),
        )
        assertTrue(
            "VaultApiClient.kt is expected to wrap GET /v1/steam/player-summaries as of WP 4h.4",
            vaultApiClientSource.contains("/v1/steam/player-summaries"),
        )
    }
}
