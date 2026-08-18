package dev.steamvault.app.ui.settings.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.OwnedGame
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * [steamLibraryStatusFor] (WP 4h.4): each of the app's first-class Steam-
 * library UI states must land on its OWN dedicated status, never folded
 * into a generic error -- the WP brief's explicit mutation-verify target
 * ("treat as generic error -> dies by name").
 */
class SteamLibraryStatusTest {

    private fun sampleGames(count: Int): List<OwnedGame> =
        (1..count).map { OwnedGame(appid = it, name = "Game $it") }

    @Test
    fun `a non-empty games list maps to Ready with the count`() {
        val status = steamLibraryStatusFor(Result.success(sampleGames(3)))
        assertEquals(SteamLibraryStatus.Ready(3), status)
    }

    @Test
    fun `MUTATION PIN -- an empty games list maps to MaybePrivateOrEmpty, never a generic Failed`() {
        val status = steamLibraryStatusFor(Result.success(emptyList()))
        assertEquals(SteamLibraryStatus.MaybePrivateOrEmpty, status)
    }

    @Test
    fun `MUTATION PIN -- a 409 VaultApiError maps to RelayNotConfigured, never a generic Failed`() {
        val error = VaultApiError.Validation("GET /v1/steam/owned-games failed (409)", 409, "not configured")
        val status = steamLibraryStatusFor(Result.failure(error))
        assertEquals(SteamLibraryStatus.RelayNotConfigured, status)
    }

    @Test
    fun `MUTATION PIN -- a 422 VaultApiError maps to InvalidSteamId, never a generic Failed`() {
        val error = VaultApiError.Validation("GET /v1/steam/owned-games failed (422)", 422, "bad steamid")
        val status = steamLibraryStatusFor(Result.failure(error))
        assertEquals(SteamLibraryStatus.InvalidSteamId, status)
    }

    @Test
    fun `a 502 VaultApiError -- any status other than 409 or 422 -- falls into the generic Failed bucket`() {
        val error = VaultApiError.Server("GET /v1/steam/owned-games failed (502)", 502, "bad gateway")
        val status = steamLibraryStatusFor(Result.failure(error))
        assertEquals(SteamLibraryStatus.Failed("bad gateway"), status)
    }

    @Test
    fun `a VaultApiError with no detail falls back to its message`() {
        val error = VaultApiError.Network("GET /v1/steam/owned-games failed: network error")
        val status = steamLibraryStatusFor(Result.failure(error))
        assertEquals(SteamLibraryStatus.Failed("GET /v1/steam/owned-games failed: network error"), status)
    }

    @Test
    fun `a non-VaultApiError failure -- e g no vault-api connection configured -- falls into Failed with its own message`() {
        val status = steamLibraryStatusFor(Result.failure(IllegalStateException("no vault-api connection configured")))
        assertEquals(SteamLibraryStatus.Failed("no vault-api connection configured"), status)
    }

    @Test
    fun `a failure with no message at all falls back to the unknown-error placeholder, never a crash`() {
        val status = steamLibraryStatusFor(Result.failure(IllegalStateException()))
        assertEquals(SteamLibraryStatus.Failed("unknown error"), status)
    }
}
