package dev.steamvault.app.net.model

import dev.steamvault.app.net.steam.SteamWebApiError
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * Hostile-fixture parsing for [parseOwnedGames]/[parsePlayerSummary] --
 * WP brief: "owned-games parsing with hostile fixtures (strict + lenient
 * pass like 4b.2)". Every fixture here is ALSO decodable through
 * [dev.steamvault.app.net.VaultJson] itself (the lenient, production
 * instance) -- there is no separate "strict" `Json` instance in this file
 * the way `SerializationRoundTripTest` uses one, because [parseOwnedGames]
 * does its own field-by-field validation via [kotlinx.serialization.json.JsonElement]
 * rather than a `@Serializable` data class decode -- the "strict" role
 * WP 4b.2's double-decode played is filled here by asserting each hostile
 * entry is INDIVIDUALLY skipped rather than silently coerced or crashing
 * the whole parse.
 */
class SteamWebApiParsingTest {

    // ---- parseOwnedGames ----------------------------------------------------

    @Test
    fun `a well-formed response decodes every field`() {
        val games = parseOwnedGames(
            """{"response":{"game_count":1,"games":[{"appid":440,"name":"Team Fortress 2","playtime_forever":120,"img_icon_url":"abc123"}]}}""",
        )
        assertEquals(1, games.size)
        assertEquals(OwnedGame(440, "Team Fortress 2", 120, "abc123"), games[0])
    }

    @Test
    fun `a private profile with no games key decodes to an empty list`() {
        assertEquals(emptyList<OwnedGame>(), parseOwnedGames("""{"response":{}}"""))
    }

    @Test
    fun `an empty games array decodes to an empty list`() {
        assertEquals(emptyList<OwnedGame>(), parseOwnedGames("""{"response":{"games":[]}}"""))
    }

    @Test
    fun `an entry missing appid is skipped, valid entries survive`() {
        val games = parseOwnedGames(
            """{"response":{"games":[{"name":"no appid"},{"appid":440,"name":"ok"}]}}""",
        )
        assertEquals(1, games.size)
        assertEquals(440, games[0].appid)
    }

    @Test
    fun `appid zero and negative appid are both skipped`() {
        val games = parseOwnedGames(
            """{"response":{"games":[{"appid":0,"name":"zero"},{"appid":-5,"name":"negative"},{"appid":1,"name":"ok"}]}}""",
        )
        assertEquals(listOf(1), games.map { it.appid })
    }

    @Test
    fun `MUTATION PIN -- a boolean appid is rejected, never coerced to 1 or 0`() {
        val games = parseOwnedGames("""{"response":{"games":[{"appid":true,"name":"bool appid"}]}}""")
        assertTrue(games.isEmpty())
    }

    @Test
    fun `MUTATION PIN -- a boolean playtime_forever is rejected, defaults to zero rather than 1`() {
        val games = parseOwnedGames(
            """{"response":{"games":[{"appid":1,"name":"x","playtime_forever":true}]}}""",
        )
        assertEquals(0, games[0].playtimeForever)
    }

    @Test
    fun `a negative playtime_forever falls back to zero`() {
        val games = parseOwnedGames(
            """{"response":{"games":[{"appid":1,"name":"x","playtime_forever":-30}]}}""",
        )
        assertEquals(0, games[0].playtimeForever)
    }

    @Test
    fun `an appid given as a string is rejected -- Steam always sends numbers`() {
        val games = parseOwnedGames("""{"response":{"games":[{"appid":"440","name":"string appid"}]}}""")
        assertTrue(games.isEmpty())
    }

    @Test
    fun `a non-object entry in games is skipped`() {
        val games = parseOwnedGames("""{"response":{"games":[42,"garbage",null,{"appid":1,"name":"ok"}]}}""")
        assertEquals(1, games.size)
    }

    @Test
    fun `games not being a list at all is treated as an empty library, not an error`() {
        assertEquals(emptyList<OwnedGame>(), parseOwnedGames("""{"response":{"games":"not a list"}}"""))
    }

    @Test
    fun `an oversized name is truncated, not rejected wholesale`() {
        val longName = "x".repeat(1000)
        val games = parseOwnedGames("""{"response":{"games":[{"appid":1,"name":"$longName"}]}}""")
        assertEquals(256, games[0].name.length)
    }

    @Test
    fun `an oversized icon hash is dropped to empty rather than truncated`() {
        val longIcon = "a".repeat(200)
        val games = parseOwnedGames("""{"response":{"games":[{"appid":1,"name":"x","img_icon_url":"$longIcon"}]}}""")
        assertEquals("", games[0].imgIconUrl)
    }

    @Test
    fun `more than MAX_GAMES entries are truncated, not an unbounded allocation`() {
        val many = (1..5100).joinToString(",") { """{"appid":$it,"name":"g$it"}""" }
        val games = parseOwnedGames("""{"response":{"games":[$many]}}""")
        assertEquals(5000, games.size)
    }

    @Test
    fun `a document with no usable response object raises SteamWebApiError`() {
        try {
            parseOwnedGames("""{"not_response":{}}""")
            fail("expected SteamWebApiError")
        } catch (_: SteamWebApiError) {
            // expected
        }
    }

    @Test
    fun `a document that is not a JSON object at all raises SteamWebApiError`() {
        try {
            parseOwnedGames("""[1,2,3]""")
            fail("expected SteamWebApiError")
        } catch (_: SteamWebApiError) {
            // expected
        }
    }

    @Test
    fun `garbage (non-JSON) input raises SteamWebApiError, not a raw SerializationException`() {
        try {
            parseOwnedGames("not json at all")
            fail("expected SteamWebApiError")
        } catch (_: SteamWebApiError) {
            // expected
        }
    }

    // ---- parsePlayerSummary ---------------------------------------------

    @Test
    fun `decodes the persona matching the requested steamid`() {
        val persona = parsePlayerSummary(
            """{"response":{"players":[{"steamid":"76561198042117903","personaname":"Example"}]}}""",
            "76561198042117903",
        )
        assertEquals("Example", persona?.personaName)
    }

    @Test
    fun `MUTATION PIN -- a player entry for a DIFFERENT steamid is not attributed to the requested one`() {
        val persona = parsePlayerSummary(
            """{"response":{"players":[{"steamid":"76561198042117904","personaname":"WrongAccount"}]}}""",
            "76561198042117903",
        )
        assertNull(persona)
    }

    @Test
    fun `an invalid steamid in the response is skipped`() {
        val persona = parsePlayerSummary(
            """{"response":{"players":[{"steamid":"not-a-steamid","personaname":"X"}]}}""",
            "76561198042117903",
        )
        assertNull(persona)
    }

    @Test
    fun `no players array decodes to null, not an error`() {
        assertNull(parsePlayerSummary("""{"response":{}}""", "76561198042117903"))
    }

    @Test
    fun `an oversized personaname is truncated`() {
        val longName = "y".repeat(500)
        val persona = parsePlayerSummary(
            """{"response":{"players":[{"steamid":"76561198042117903","personaname":"$longName"}]}}""",
            "76561198042117903",
        )
        assertEquals(128, persona?.personaName?.length)
    }
}
