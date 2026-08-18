package dev.steamvault.app.net.model

import dev.steamvault.app.net.VaultJson
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Wire-shape decoding for [OwnedGamesRelayOut]/[PlayerSummariesRelayOut]
 * (WP 4h.4) -- replaces `SteamWebApiParsingTest`'s hand-parsing-era
 * coverage now that these are plain `@Serializable` DTOs decoded through
 * [VaultJson], same shape as every other `net/model` type.
 *
 * Same STRICT + lenient double-decode technique
 * `SerializationRoundTripTest` uses (its own kdoc has the full reasoning):
 * [strictJson] catches a fixture typo that [VaultJson]'s
 * `ignoreUnknownKeys = true` would otherwise silently absorb.
 */
class SteamRelayParsingTest {

    private val strictJson = Json {
        ignoreUnknownKeys = false
        isLenient = false
    }

    private inline fun <reified T> decodeStrictAndLenient(json: String): T {
        strictJson.decodeFromString<T>(json)
        return VaultJson.decodeFromString(json)
    }

    // ---- OwnedGamesRelayOut ---------------------------------------------

    @Test
    fun `both playtime_forever and rtime_last_played PRESENT decode their real values`() {
        val json = """
            {"configured":true,"game_count":1,
             "games":[{"appid":440,"name":"Team Fortress 2","playtime_forever":120,
                       "img_icon_url":"abc123","rtime_last_played":1723000000}]}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<OwnedGamesRelayOut>(json)

        assertEquals(1, decoded.game_count)
        val game = decoded.games.single()
        assertEquals(440, game.appid)
        assertEquals("Team Fortress 2", game.name)
        assertEquals(120, game.playtime_forever)
        assertEquals("abc123", game.img_icon_url)
        assertEquals(1723000000, game.rtime_last_played)
    }

    @Test
    fun `MUTATION PIN -- both playtime_forever and rtime_last_played ABSENT -- the actual WP 4h0 default-gate wire shape -- still decodes`() {
        // This is the response a DEFAULT-CONFIGURED vault sends (both
        // VAULT_RELAY_EXPOSE_* switches default off, ADR-0010) -- the
        // COMMON case per the audit requirement, not an edge case. Removing
        // either field's `= null` default in OwnedGame kills this test by
        // name (MissingFieldException).
        val json = """
            {"configured":true,"game_count":1,
             "games":[{"appid":440,"name":"Team Fortress 2","img_icon_url":"abc123"}]}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<OwnedGamesRelayOut>(json)

        val game = decoded.games.single()
        assertEquals(440, game.appid)
        assertNull(game.playtime_forever)
        assertNull(game.rtime_last_played)
    }

    @Test
    fun `only playtime_forever exposed -- rtime_last_played still absent -- decodes independently`() {
        // ADR-0010: the two switches are independent, so this asymmetric
        // shape is a real possible server configuration, not a fixture
        // artifact.
        val json = """
            {"configured":true,"game_count":1,
             "games":[{"appid":440,"name":"Team Fortress 2","playtime_forever":0,"img_icon_url":"abc123"}]}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<OwnedGamesRelayOut>(json)

        val game = decoded.games.single()
        assertEquals(0, game.playtime_forever)
        assertNull(game.rtime_last_played)
    }

    @Test
    fun `game_count 0 with an empty games array decodes cleanly -- the maybe-private-or-empty wire shape`() {
        val json = """{"configured":true,"game_count":0,"games":[]}"""

        val decoded = decodeStrictAndLenient<OwnedGamesRelayOut>(json)

        assertEquals(0, decoded.game_count)
        assertTrue(decoded.games.isEmpty())
    }

    @Test
    fun `a future field this client has never heard of is ignored, not a decode failure`() {
        // Deliberately VaultJson-only, not decodeStrictAndLenient -- see
        // SerializationRoundTripTest's identical case for why.
        val json = """
            {"configured":true,"game_count":1,
             "games":[{"appid":440,"name":"Team Fortress 2","img_icon_url":"abc",
                       "a_field_this_client_has_never_heard_of":{"nested":["whatever"]}}]}
        """.trimIndent()

        val decoded = VaultJson.decodeFromString<OwnedGamesRelayOut>(json)

        assertEquals(440, decoded.games.single().appid)
    }

    // ---- PlayerSummariesRelayOut -----------------------------------------

    @Test
    fun `PlayerSummariesRelayOut decodes a persona row`() {
        val json = """
            {"configured":true,
             "players":[{"steamid":"76561198042117903","personaname":"Example",
                         "avatar":"https://example.test/a.jpg","personastate":1}]}
        """.trimIndent()

        val decoded = decodeStrictAndLenient<PlayerSummariesRelayOut>(json)

        val player = decoded.players.single()
        assertEquals("76561198042117903", player.steamid)
        assertEquals("Example", player.personaname)
        assertEquals(1, player.personastate)
    }

    @Test
    fun `PlayerSummariesRelayOut with no players decodes to an empty list, not an error`() {
        val decoded = decodeStrictAndLenient<PlayerSummariesRelayOut>("""{"configured":true,"players":[]}""")
        assertTrue(decoded.players.isEmpty())
    }
}
