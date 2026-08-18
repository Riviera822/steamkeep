package dev.steamvault.app.net.model

import kotlinx.serialization.Serializable

/**
 * One library-grid row from vault-api's Steam relay
 * (`GET /v1/steam/owned-games`, mirroring
 * `vault_api/routers/steam.py::OwnedGameOut` field for field, verbatim
 * snake_case per this package's own no-renaming-layer convention -- see
 * `Games.kt`'s kdoc for the same rule applied to `GameSummary`).
 *
 * **WP 4h.4 (ADR-0004's second addendum) replaced the OLD hand-parsed,
 * direct-to-Valve `GetOwnedGames` shape with this one.** The app no longer
 * talks to Valve's Web API host at all -- see `app/README.md`'s "Steam
 * library via the vault relay" section for the full before/after and
 * `net/steam/VaultRelayLibraryFetcher.kt` for the client that decodes this.
 *
 * **`playtime_forever`/`rtime_last_played` are genuinely OPTIONAL on the
 * wire, not merely nullable -- and the default-off case is the COMMON
 * shape, not an edge case (audit requirement, not a defensive nicety).**
 * WP 4h.0's privacy gate (`ADR-0010`,
 * `vault_api/routers/steam.py::_build_owned_game_out`,
 * `response_model_exclude_unset=True`) OMITS either JSON key entirely from
 * the response object when its corresponding `VAULT_RELAY_EXPOSE_*`
 * environment switch is off server-side -- both default OFF, so a
 * default-configured vault answers with NEITHER key present at all. kotlinx
 * treats a missing key exactly like every other field this client's own
 * default-argument convention already relies on (`VaultJson`'s kdoc): the
 * property's Kotlin default (`null` for both here) is what makes decoding
 * succeed, no custom decoder needed. `SteamRelayParsingTest`'s "both keys
 * absent -- the actual default-gate shape" fixture is built to match that
 * REAL response shape (both keys textually absent), not a hand-typed
 * fixture with the keys present as JSON `null` -- kotlinx would tolerate
 * that shape too, but it is not what a default-configured vault sends.
 */
@Serializable
data class OwnedGame(
    val appid: Int,
    val name: String,
    val playtime_forever: Int? = null,
    val img_icon_url: String = "",
    val rtime_last_played: Int? = null,
)

/**
 * `GET /v1/steam/owned-games`'s top-level envelope
 * (`vault_api/routers/steam.py::OwnedGamesOut`). A `409` (no key configured
 * server-side) never reaches this decode path -- it surfaces as a
 * [dev.steamvault.app.net.error.VaultApiError] with `status == 409` before
 * any body is parsed as this type (`net/VaultApiClient.kt::steamOwnedGames`
 * throws before returning, same as every other non-2xx response this
 * client handles).
 *
 * **`game_count == 0` is a first-class, deliberately AMBIGUOUS state (WP
 * 4h.4 audit requirement), not a bug signal.** Under the OLD device-local
 * design (ADR-0004 decision 2, now superseded for library data), each
 * user's OWN key saw their OWN library even behind a private profile. The
 * relay uses ONE operator-owned key for every signed-in user on this vault,
 * and Valve's `GetOwnedGames` for a DIFFERENT SteamID answers with nothing
 * (verified against `vault_api/steam_relay.py::parse_owned_games`: a
 * private profile / restricted-permission view degrades to the exact same
 * empty `response` object as a genuinely empty library) unless that
 * profile's game details happen to be public. This type cannot -- and does
 * not try to -- distinguish "genuinely zero owned games" from "private
 * profile"; both collapse to the identical wire shape upstream. See
 * `ui/settings/logic/SteamLibraryStatus.kt`'s `MaybePrivateOrEmpty` state,
 * which names both possible causes rather than rendering an empty shelf
 * that looks like a bug.
 */
@Serializable
data class OwnedGamesRelayOut(
    val configured: Boolean = true,
    val game_count: Int = 0,
    val games: List<OwnedGame> = emptyList(),
)

/**
 * One entry of `GET /v1/steam/player-summaries`
 * (`vault_api/routers/steam.py::PlayerSummaryOut`) -- the wire shape,
 * decoded then cross-checked and mapped to [SteamPersona] by
 * `VaultRelayLibraryFetcher.getPlayerSummary` (the same "answer must be
 * about the account asked for" cross-check
 * `vault_api/steam_relay.py::parse_player_summaries` already applies
 * server-side; kept here too per docs/LEARNINGS.md's "everything returned
 * is hostile input" -- a semi-trusted relay is still not "trust the
 * account identifier it echoes back without checking").
 */
@Serializable
data class PlayerSummaryEntry(
    val steamid: String,
    val personaname: String = "",
    val avatar: String? = null,
    val avatarmedium: String? = null,
    val avatarfull: String? = null,
    val personastate: Int? = null,
)

/** `GET /v1/steam/player-summaries`'s top-level envelope. */
@Serializable
data class PlayerSummariesRelayOut(
    val configured: Boolean = true,
    val players: List<PlayerSummaryEntry> = emptyList(),
)

/**
 * The identity screen's persona name (WP 4b.3) -- unchanged shape by WP
 * 4h.4, only WHERE its data comes from changed (the vault relay's
 * [PlayerSummaryEntry], not a direct-to-Valve call).
 */
data class SteamPersona(
    val steamId64: String,
    val personaName: String,
)
