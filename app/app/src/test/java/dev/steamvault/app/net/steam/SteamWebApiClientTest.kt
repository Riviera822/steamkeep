package dev.steamvault.app.net.steam

import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * [SteamWebApiClient] behaviour: successful round trips, the host/path
 * literal pin, and -- the WP brief's explicit mutation-verify target --
 * that a canary API key NEVER appears in any exception message this class
 * raises, across every error path (network failure, non-2xx, oversized
 * body). `baseUrl` is injectable purely so this file can point at a local
 * `MockWebServer`; production code never overrides it (see [hostPin]).
 */
class SteamWebApiClientTest {

    private lateinit var server: MockWebServer

    @After
    fun tearDown() {
        if (::server.isInitialized) server.shutdown()
    }

    @Test
    fun `hostPin -- the production endpoint constants are the literal Valve host and paths`() {
        assertEquals("https://api.steampowered.com", SteamWebApiClient.STEAM_API_BASE)
        assertEquals("api.steampowered.com", SteamWebApiClient.STEAM_API_HOST)
        assertEquals("/IPlayerService/GetOwnedGames/v1/", SteamWebApiClient.OWNED_GAMES_PATH)
        assertEquals("/ISteamUser/GetPlayerSummaries/v2/", SteamWebApiClient.PLAYER_SUMMARIES_PATH)
    }

    @Test
    fun `getOwnedGames sends key, steamid, format and include_appinfo, and decodes the games list`() = runTest {
        server = MockWebServer().apply { start() }
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"response":{"game_count":1,"games":[{"appid":440,"name":"Team Fortress 2","playtime_forever":120,"img_icon_url":"abc123"}]}}""",
            ),
        )
        val client = SteamWebApiClient(apiKeyProvider = { "TESTKEY123" }, baseUrl = server.url("/").toString().trimEnd('/'))

        val games = client.getOwnedGames("76561198042117903")

        assertEquals(1, games.size)
        assertEquals(440, games[0].appid)
        assertEquals("Team Fortress 2", games[0].name)
        assertEquals(120, games[0].playtimeForever)

        val recorded = server.takeRequest()
        assertTrue(recorded.path?.contains("key=TESTKEY123") == true)
        assertTrue(recorded.path?.contains("steamid=76561198042117903") == true)
        assertTrue(recorded.path?.contains("include_appinfo=1") == true)
        assertTrue(recorded.path?.startsWith(SteamWebApiClient.OWNED_GAMES_PATH) == true)
    }

    @Test
    fun `getPlayerSummary cross-checks the returned steamid and decodes the persona`() = runTest {
        server = MockWebServer().apply { start() }
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"response":{"players":[{"steamid":"76561198042117903","personaname":"Example"}]}}""",
            ),
        )
        val client = SteamWebApiClient(apiKeyProvider = { "TESTKEY123" }, baseUrl = server.url("/").toString().trimEnd('/'))

        val persona = client.getPlayerSummary("76561198042117903")

        assertEquals("Example", persona?.personaName)
    }

    @Test
    fun `an empty games library (private profile) decodes to an empty list, not an error`() = runTest {
        server = MockWebServer().apply { start() }
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"response":{}}"""))
        val client = SteamWebApiClient(apiKeyProvider = { "TESTKEY123" }, baseUrl = server.url("/").toString().trimEnd('/'))

        assertEquals(emptyList<Any>(), client.getOwnedGames("76561198042117903"))
    }

    // ---- key redaction (mutation-verify target) ----------------------------

    private val canaryKey = "CANARY-KEY-MUST-NEVER-APPEAR-IN-AN-ERROR-1234"

    @Test
    fun `MUTATION PIN -- a network failure's exception message never contains the API key`() = runTest {
        val deadServer = MockWebServer().apply { start() }
        val port = deadServer.port
        deadServer.shutdown()
        val client = SteamWebApiClient(apiKeyProvider = { canaryKey }, baseUrl = "http://127.0.0.1:$port")

        try {
            client.getOwnedGames("76561198042117903")
            fail("expected SteamWebApiError")
        } catch (e: SteamWebApiError) {
            assertFalse(e.message.orEmpty().contains(canaryKey))
            assertFalse(e.cause?.message.orEmpty().contains(canaryKey))
        }
    }

    @Test
    fun `MUTATION PIN -- a non-2xx status's exception message never contains the API key`() = runTest {
        server = MockWebServer().apply { start() }
        server.enqueue(MockResponse().setResponseCode(403).setBody("Forbidden"))
        val client = SteamWebApiClient(apiKeyProvider = { canaryKey }, baseUrl = server.url("/").toString().trimEnd('/'))

        try {
            client.getOwnedGames("76561198042117903")
            fail("expected SteamWebApiError")
        } catch (e: SteamWebApiError) {
            assertFalse(e.message.orEmpty().contains(canaryKey))
        }

        // Defense in depth: the key DID legitimately have to reach the wire
        // (that is the whole point of the call) -- the pin above is about
        // the CLIENT-SIDE exception message, not about whether the server
        // received it.
        val recorded = server.takeRequest()
        assertTrue(recorded.path?.contains(canaryKey) == true)
    }

    @Test
    fun `MUTATION PIN -- an oversized body's exception message never contains the API key`() = runTest {
        server = MockWebServer().apply { start() }
        val padding = "x".repeat((SteamWebApiClient.MAX_RESPONSE_BYTES + 1024).toInt())
        server.enqueue(MockResponse().setResponseCode(200).setBody(padding))
        val client = SteamWebApiClient(apiKeyProvider = { canaryKey }, baseUrl = server.url("/").toString().trimEnd('/'))

        try {
            client.getOwnedGames("76561198042117903")
            fail("expected SteamWebApiError")
        } catch (e: SteamWebApiError) {
            assertFalse(e.message.orEmpty().contains(canaryKey))
        }
    }

    @Test
    fun `a garbage (non-JSON) body raises a key-free SteamWebApiError`() = runTest {
        server = MockWebServer().apply { start() }
        server.enqueue(MockResponse().setResponseCode(200).setBody("not json at all"))
        val client = SteamWebApiClient(apiKeyProvider = { canaryKey }, baseUrl = server.url("/").toString().trimEnd('/'))

        try {
            client.getOwnedGames("76561198042117903")
            fail("expected an exception for a non-JSON body")
        } catch (e: Exception) {
            assertFalse(e.message.orEmpty().contains(canaryKey))
        }
    }

    @Test
    fun `S1b -- SteamWebApiClient's built OkHttpClient has both redirect flags disabled`() {
        val client = SteamWebApiClient(apiKeyProvider = { "unused" })
        val httpClient = client.debugHttpClientForTesting
        assertFalse(httpClient.followRedirects)
        assertFalse(httpClient.followSslRedirects)
    }
}
