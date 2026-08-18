package dev.steamvault.app.net.steam

import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.profile.SystemVpnProfile
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

private const val VALID_STEAM_ID = "76561198042117903"
private const val OTHER_STEAM_ID = "76561198042117904"

/**
 * [VaultRelayLibraryFetcher] (WP 4h.4) -- the production
 * [SteamLibraryFetcher] that replaced the device-local `SteamWebApiClient`.
 * MockWebServer stands in for vault-api, same pattern
 * `VaultApiClientTest`/the deleted `SteamWebApiClientTest` both used.
 */
class VaultRelayLibraryFetcherTest {

    private lateinit var server: MockWebServer

    @After
    fun tearDown() {
        if (::server.isInitialized) server.shutdown()
    }

    private fun startServer(): MockWebServer = MockWebServer().apply { start() }

    private fun clientAgainst(target: MockWebServer): VaultApiClient = VaultApiClient(
        profile = SystemVpnProfile(target.url("/").toString().trimEnd('/')),
        apiKeyProvider = { "test-key" },
    )

    @Test
    fun `getOwnedGames delegates to VaultApiClient steamOwnedGames -- never api_steampowered_com`() = runTest {
        server = startServer()
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"configured":true,"game_count":1,"games":[{"appid":440,"name":"TF2","img_icon_url":"x"}]}""",
            ),
        )
        val fetcher = VaultRelayLibraryFetcher { clientAgainst(server) }

        val games = fetcher.getOwnedGames(VALID_STEAM_ID)

        assertEquals(1, games.size)
        assertEquals(440, games[0].appid)
        // The request actually landed on THIS mock server (standing in for
        // the vault) -- if a regression hardcoded api.steampowered.com
        // instead, this would never arrive and the bounded takeRequest
        // below would return null, failing this assertion instead of
        // hanging forever.
        val recorded = server.takeRequest(2, java.util.concurrent.TimeUnit.SECONDS)
        assertEquals("/v1/steam/owned-games?steamid=$VALID_STEAM_ID", recorded?.path)
    }

    @Test
    fun `getPlayerSummary returns the matching persona`() = runTest {
        server = startServer()
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"configured":true,"players":[{"steamid":"$VALID_STEAM_ID","personaname":"Example"}]}""",
            ),
        )
        val fetcher = VaultRelayLibraryFetcher { clientAgainst(server) }

        val persona = fetcher.getPlayerSummary(VALID_STEAM_ID)

        assertEquals("Example", persona?.personaName)
        assertEquals(VALID_STEAM_ID, persona?.steamId64)
    }

    @Test
    fun `MUTATION PIN -- getPlayerSummary cross-checks the returned steamid, never attributing a mismatch`() = runTest {
        server = startServer()
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"configured":true,"players":[{"steamid":"$OTHER_STEAM_ID","personaname":"WrongAccount"}]}""",
            ),
        )
        val fetcher = VaultRelayLibraryFetcher { clientAgainst(server) }

        val persona = fetcher.getPlayerSummary(VALID_STEAM_ID)

        assertNull(persona)
    }

    @Test
    fun `getPlayerSummary with no players decodes to null, not an error`() = runTest {
        server = startServer()
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"configured":true,"players":[]}"""))
        val fetcher = VaultRelayLibraryFetcher { clientAgainst(server) }

        assertNull(fetcher.getPlayerSummary(VALID_STEAM_ID))
    }

    @Test
    fun `409 from the relay surfaces as a VaultApiError, unwrapped`() = runTest {
        server = startServer()
        server.enqueue(MockResponse().setResponseCode(409).setBody("""{"detail":"Steam relay is not configured."}"""))
        val fetcher = VaultRelayLibraryFetcher { clientAgainst(server) }

        try {
            fetcher.getOwnedGames(VALID_STEAM_ID)
            fail("expected VaultApiError.Validation")
        } catch (e: VaultApiError.Validation) {
            assertEquals(409, e.status)
        }
    }

    @Test
    fun `MUTATION PIN -- no vault-api connection configured fails closed with a clear message, not an NPE`() = runTest {
        val fetcher = VaultRelayLibraryFetcher { null }

        try {
            fetcher.getOwnedGames(VALID_STEAM_ID)
            fail("expected an exception")
        } catch (e: IllegalStateException) {
            assertTrue(e.message.orEmpty().contains("vault-api"))
        }
    }
}
