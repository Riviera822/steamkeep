package dev.steamvault.app.ui.settings.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.OwnedGame

/**
 * What Settings' Steam-identity section shows about the last
 * `SteamIdentityRepository.ownedGames()` fetch (WP 4h.4, ADR-0004's second
 * addendum). Pure mapping, no Android framework dependency, directly unit-
 * testable in [SteamLibraryStatusTest] -- the wording itself lives in
 * `strings.xml`/`SettingsScreen.kt`'s Composable (same split every other
 * Settings field in this file already uses, e.g. `captionFor`); this type
 * only decides WHICH state applies, never how it reads.
 *
 * **[MaybePrivateOrEmpty] records a real regression against the OLD,
 * device-local design, not a new bug (audit requirement).** Under ADR-0004
 * decision 2 (now superseded for library data), each user's OWN Steam Web
 * API key saw their OWN library even behind a private profile. vault-api's
 * relay uses ONE operator-owned key for every signed-in user on this vault,
 * and Valve's `GetOwnedGames` answers a DIFFERENT SteamID with nothing
 * (`configured: true, game_count: 0`) unless that profile's game details
 * are public -- the identical wire shape a genuinely empty library
 * produces (`vault_api/steam_relay.py::parse_owned_games`). This state
 * cannot and does not try to tell the two apart; it names both possible
 * causes rather than rendering an empty shelf that looks like a bug.
 */
sealed class SteamLibraryStatus {
    /** Never checked this session (the initial state, before any
     * [dev.steamvault.app.ui.settings.SettingsController.checkSteamLibrary]
     * call). */
    data object Unknown : SteamLibraryStatus()

    /** At least one game -- the ordinary case. */
    data class Ready(val gameCount: Int) : SteamLibraryStatus()

    /** `configured: true, game_count: 0` -- see this file's class kdoc. */
    data object MaybePrivateOrEmpty : SteamLibraryStatus()

    /** `409` -- no Steam Web API key is configured on vault-api itself
     * (the operator sets one once, server-side; no app user ever needs
     * their own again). */
    data object RelayNotConfigured : SteamLibraryStatus()

    /** `422` -- vault-api rejected this account's SteamID64. Should not
     * happen for a steamid this app's own OpenID sign-in produced, but the
     * relay names it as a possible answer (`api/vault_api/routers/
     * steam.py::_parse_steamid`), so it gets its own message rather than
     * folding into the generic error case below. */
    data object InvalidSteamId : SteamLibraryStatus()

    /** Anything else -- a network failure, an unreachable vault, a `5xx`,
     * no vault-api connection configured at all. [message] is exactly
     * "whatever the app already does for other vault-api calls" (WP
     * brief): [VaultApiError.detail]/`.message` when the failure came from
     * one, or the plain exception message otherwise -- no new wording
     * invented for this bucket. */
    data class Failed(val message: String) : SteamLibraryStatus()
}

/**
 * Maps [result] -- exactly what
 * [dev.steamvault.app.repo.SteamIdentityRepository.ownedGames] returns --
 * to the state Settings' Steam-identity section renders.
 *
 * `409`/`422` are read off [VaultApiError.status]: both fold into
 * [VaultApiError.Validation]'s shared `validation` kind per
 * `net/error/VaultApiError.kt`'s taxonomy (`classifyHttpStatus`'s ">= 400"
 * bucket), so `.status` — not the sealed subclass — is what actually
 * distinguishes "not configured" from "bad steamid" here; anything else
 * (a different [VaultApiError] status, or a non-`VaultApiError` failure
 * such as "no vault-api connection configured"/"not signed in with Steam")
 * degrades to [SteamLibraryStatus.Failed] rather than being silently folded
 * into one of the two named states above.
 */
fun steamLibraryStatusFor(result: Result<List<OwnedGame>>): SteamLibraryStatus {
    val games = result.getOrNull()
    if (games != null) {
        return if (games.isEmpty()) {
            SteamLibraryStatus.MaybePrivateOrEmpty
        } else {
            SteamLibraryStatus.Ready(games.size)
        }
    }

    return when (val error = result.exceptionOrNull()) {
        is VaultApiError -> when (error.status) {
            409 -> SteamLibraryStatus.RelayNotConfigured
            422 -> SteamLibraryStatus.InvalidSteamId
            else -> SteamLibraryStatus.Failed(error.detail ?: error.message ?: UNKNOWN_ERROR_MESSAGE)
        }
        else -> SteamLibraryStatus.Failed(error?.message ?: UNKNOWN_ERROR_MESSAGE)
    }
}

private const val UNKNOWN_ERROR_MESSAGE = "unknown error"
