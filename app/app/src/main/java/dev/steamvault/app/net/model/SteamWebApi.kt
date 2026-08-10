package dev.steamvault.app.net.model

import dev.steamvault.app.net.VaultJson
import dev.steamvault.app.net.steam.SteamId64
import dev.steamvault.app.net.steam.SteamWebApiError
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/**
 * One library-grid row from `IPlayerService/GetOwnedGames/v1` -- exactly
 * the fields the WP brief names ("the whitelisted fields the library grid
 * needs"), mirroring `api/vault_api/steam_relay.py::OwnedGame`'s own field
 * whitelist for the analogous web-relay endpoint.
 */
data class OwnedGame(
    val appid: Int,
    val name: String,
    val playtimeForever: Int,
    val imgIconUrl: String,
)

/** `ISteamUser/GetPlayerSummaries/v2`'s persona name, for the identity screen. */
data class SteamPersona(
    val steamId64: String,
    val personaName: String,
)

private const val MAX_GAMES = 5000
private const val MAX_NAME_LEN = 256
private const val MAX_PERSONA_LEN = 128
private const val MAX_ICON_LEN = 64

/**
 * Validate a raw `GetOwnedGames` JSON body into a list of [OwnedGame].
 * Mirrors `api/vault_api/steam_relay.py::parse_owned_games`'s tolerant
 * shape: the response is hostile input by construction (a network call to
 * a host this project does not run) and this is one of the "Everything
 * returned is hostile input" cases `docs/LEARNINGS.md` describes for the
 * server-side relay -- so a single malformed game entry is skipped, not
 * fatal, while a document with no usable `response`/`games` shape at all
 * raises [dev.steamvault.app.net.steam.SteamWebApiError].
 *
 * A private profile or an empty library legitimately has no `games` key
 * (or an empty array) -- both decode to an empty list, not an error.
 */
fun parseOwnedGames(body: String): List<OwnedGame> {
    val root = decodeJsonOrThrow(body, "GetOwnedGames")
    val obj = root as? JsonObject
        ?: throw SteamWebApiError("GetOwnedGames did not return a JSON object")
    val response = obj["response"] as? JsonObject
        ?: throw SteamWebApiError("GetOwnedGames response has no usable 'response' object")

    val rawGames = response["games"] as? JsonArray ?: return emptyList()
    val result = mutableListOf<OwnedGame>()
    for ((index, element) in rawGames.withIndex()) {
        if (index >= MAX_GAMES) break
        val entry = element as? JsonObject ?: continue
        val appid = (entry["appid"] as? JsonPrimitive)?.intOrNullStrict()?.takeIf { it > 0 } ?: continue
        val rawName = (entry["name"] as? JsonPrimitive)?.contentOrNull
        val name = rawName?.take(MAX_NAME_LEN) ?: ""
        val playtime = (entry["playtime_forever"] as? JsonPrimitive)
            ?.intOrNullStrict()
            ?.takeIf { it >= 0 } ?: 0
        val rawIcon = (entry["img_icon_url"] as? JsonPrimitive)?.contentOrNull
        val icon = if (rawIcon != null && rawIcon.length <= MAX_ICON_LEN) rawIcon else ""
        result.add(OwnedGame(appid = appid, name = name, playtimeForever = playtime, imgIconUrl = icon))
    }
    return result
}

/**
 * Validate a raw `GetPlayerSummaries` JSON body into the persona matching
 * [expectedSteamId64], or `null` if the document has no usable entry for
 * that exact account. Cross-checks the returned `steamid` against
 * [expectedSteamId64] before trusting `personaname` -- the same corruption
 * cross-check `api/vault_api/steam_relay.py::parse_player_summaries`
 * applies: an answer that is not about the account asked for must never be
 * attributed to it.
 */
fun parsePlayerSummary(body: String, expectedSteamId64: String): SteamPersona? {
    val root = decodeJsonOrThrow(body, "GetPlayerSummaries")
    val obj = root as? JsonObject
        ?: throw SteamWebApiError("GetPlayerSummaries did not return a JSON object")
    val response = obj["response"] as? JsonObject
        ?: throw SteamWebApiError("GetPlayerSummaries response has no usable 'response' object")

    val players = response["players"] as? JsonArray ?: return null
    for (element in players) {
        val entry = element as? JsonObject ?: continue
        val rawSteamId = (entry["steamid"] as? JsonPrimitive)?.contentOrNull ?: continue
        val validated = SteamId64.validate(rawSteamId) ?: continue
        if (validated != expectedSteamId64) continue
        val persona = (entry["personaname"] as? JsonPrimitive)?.contentOrNull?.take(MAX_PERSONA_LEN) ?: ""
        return SteamPersona(steamId64 = validated, personaName = persona)
    }
    return null
}

/**
 * Bytes -> [JsonElement], with every decode failure converted to a
 * key-free [SteamWebApiError] instead of letting kotlinx.serialization's
 * own [SerializationException] (or, for a maliciously deep document, a
 * [StackOverflowError] -- the same recursive-descent-parser risk
 * `docs/LEARNINGS.md`'s Parsers section documents for CPython's `json`
 * module) escape this module's documented "only SteamWebApiError, and
 * never with the key in it" contract.
 */
private fun decodeJsonOrThrow(body: String, endpointLabel: String): JsonElement = try {
    VaultJson.parseToJsonElement(body)
} catch (e: SerializationException) {
    throw SteamWebApiError("$endpointLabel did not return valid JSON", e)
} catch (e: StackOverflowError) {
    throw SteamWebApiError("$endpointLabel response nests too deeply to parse")
}

/**
 * A non-negative, non-boolean int straight out of parsed JSON. `bool` is
 * rejected explicitly (`docs/LEARNINGS.md`: `true == 1` must never sneak
 * through as a count/id via Pydantic-lax-mode-style coercion; the same
 * house rule `api/vault_api/steam_relay.py::_coerce_nonneg_int` applies).
 */
private fun JsonPrimitive.intOrNullStrict(): Int? {
    if (this.isString) return null
    if (content == "true" || content == "false") return null
    return content.toIntOrNull()
}
