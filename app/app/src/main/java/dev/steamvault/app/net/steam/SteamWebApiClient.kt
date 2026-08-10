package dev.steamvault.app.net.steam

import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.net.model.SteamPersona
import dev.steamvault.app.net.model.parseOwnedGames
import dev.steamvault.app.net.model.parsePlayerSummary
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException

/**
 * Anything that made a Steam Web API call unusable -- network failure,
 * non-200, oversized/garbage body, or a document with no usable shape.
 * Its message is built ONLY from a fixed, key-free description (the
 * endpoint's own [SteamWebApiClient] method name plus, where relevant,
 * the HTTP status) -- see [SteamWebApiClient]'s kdoc "Key redaction" for
 * exactly what this guarantees and how it is pinned.
 */
class SteamWebApiError(message: String, cause: Throwable? = null) : Exception(message, cause)

/** The on-device counterpart to [dev.steamvault.app.net.model.OwnedGame]/[SteamPersona] fetches, so [dev.steamvault.app.repo.SteamIdentityRepositoryImpl] tests can fake it. */
interface SteamLibraryFetcher {
    suspend fun getOwnedGames(steamId64: String): List<OwnedGame>
    suspend fun getPlayerSummary(steamId64: String): SteamPersona?
}

/**
 * On-device `IPlayerService/GetOwnedGames/v1` + `ISteamUser
 * /GetPlayerSummaries/v2` calls, using the user's OWN Steam Web API key
 * (ADR-0004 decision 2: "Library data is fetched directly from the Steam
 * Web API (device-local user-owned API key, stored only on the phone),
 * never proxied through vault-api"). This class NEVER talks to vault-api
 * and vault-api's [dev.steamvault.app.net.VaultApiClient] never references
 * this class or the key it carries -- see `SteamKeyIsolationTest` for the
 * grep-provable structural pin of that separation.
 *
 * **Security posture** -- the same one WP 4b.2's `VaultApiClient`
 * established and `docs/LEARNINGS.md` pins as load-bearing:
 * `followSslRedirects(false)` + `followRedirects(false)` (no redirect is
 * ever legitimate here -- the key lives in the query string, and a
 * redirect target could exfiltrate it exactly like the WP 4b.2 blocker),
 * HTTPS only ([STEAM_API_BASE] is always `https://`), a host pin
 * ([STEAM_API_HOST], hardcoded, never derived from any caller/config
 * input), and a bounded response read (a very large Steam library still
 * renders to well under a megabyte of JSON; [MAX_RESPONSE_BYTES] is
 * generous headroom while refusing a body designed to exhaust memory --
 * same bound `api/vault_api/steam_relay.py::MAX_RESPONSE_BYTES` uses for
 * the analogous server-side relay call).
 *
 * **Key redaction (WP brief: "key never logged; URL with key never in
 * exception messages", mirroring `api/vault_api/steam_relay.py`'s
 * `_redacted_url` discipline).** The API key lives ONLY in the request's
 * query string, which OkHttp needs to actually send the request -- but
 * every error path below builds its [SteamWebApiError] message from a
 * fixed literal plus, at most, the HTTP status code. `e.message` from a
 * caught [IOException] is deliberately NEVER interpolated into an error
 * message (only `e::class.simpleName`, a class name that can never embed
 * request data) -- `SteamWebApiClientTest`'s redaction tests plant a
 * canary key and assert it appears in NEITHER the network-failure path NOR
 * the non-2xx path NOR the garbage-body path's resulting exception
 * message.
 */
class SteamWebApiClient(
    private val apiKeyProvider: () -> String,
    private val baseUrl: String = STEAM_API_BASE,
    okHttpClient: OkHttpClient = defaultSteamOkHttpClient(),
) : SteamLibraryFetcher {

    private val client: OkHttpClient = okHttpClient.newBuilder()
        .followSslRedirects(false)
        .followRedirects(false)
        .build()

    /** Test-only escape hatch, same pattern as `VaultApiClient.debugHttpClientForTesting`. */
    internal val debugHttpClientForTesting: OkHttpClient get() = client

    override suspend fun getOwnedGames(steamId64: String): List<OwnedGame> {
        val body = fetch(OWNED_GAMES_PATH) { builder ->
            builder.addQueryParameter("steamid", steamId64)
            builder.addQueryParameter("include_appinfo", "1")
        }
        return parseOwnedGames(body)
    }

    override suspend fun getPlayerSummary(steamId64: String): SteamPersona? {
        val body = fetch(PLAYER_SUMMARIES_PATH) { builder ->
            builder.addQueryParameter("steamids", steamId64)
        }
        return parsePlayerSummary(body, steamId64)
    }

    /**
     * Shared GET plumbing: builds `$baseUrl$path?key=...&format=json&...`
     * (query params added by [extra] AFTER `key`/`format`, matching
     * `api/vault_api/steam_relay.py`'s own param ordering for the same two
     * calls), executes it, and returns the raw response text -- bounded,
     * never logging or echoing the URL it built.
     */
    private suspend fun fetch(path: String, extra: (HttpUrl.Builder) -> Unit): String {
        val urlBuilder = baseUrl.toHttpUrl().newBuilder().encodedPath(path)
        urlBuilder.addQueryParameter("key", apiKeyProvider())
        urlBuilder.addQueryParameter("format", "json")
        extra(urlBuilder)
        val request = Request.Builder().url(urlBuilder.build()).build()

        val response = try {
            withContext(Dispatchers.IO) { client.newCall(request).execute() }
        } catch (e: IOException) {
            // Redaction: only the exception's CLASS NAME crosses into the
            // message, never e.message (which, for some IOException
            // subtypes, MAY embed connection details) and never the
            // request/URL object itself.
            throw SteamWebApiError("$path failed: network error (${e::class.simpleName})", e)
        }

        return response.use { resp ->
            if (!resp.isSuccessful) {
                throw SteamWebApiError("$path answered HTTP ${resp.code}")
            }
            readBounded(resp.body?.source(), MAX_RESPONSE_BYTES)
                ?: throw SteamWebApiError("$path response exceeded the ${MAX_RESPONSE_BYTES}-byte bound")
        }
    }

    companion object {
        const val STEAM_API_HOST = "api.steampowered.com"
        const val STEAM_API_BASE = "https://$STEAM_API_HOST"
        const val OWNED_GAMES_PATH = "/IPlayerService/GetOwnedGames/v1/"
        const val PLAYER_SUMMARIES_PATH = "/ISteamUser/GetPlayerSummaries/v2/"

        /** Same reasoning as `api/vault_api/steam_relay.py::MAX_RESPONSE_BYTES`. */
        const val MAX_RESPONSE_BYTES = 2L * 1024 * 1024
    }
}
