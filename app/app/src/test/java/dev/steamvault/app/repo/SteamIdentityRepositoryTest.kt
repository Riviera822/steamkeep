package dev.steamvault.app.repo

import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.net.model.SteamPersona
import dev.steamvault.app.net.steam.SteamLibraryFetcher
import dev.steamvault.app.net.steam.SteamOpenIdConfig
import dev.steamvault.app.net.steam.SteamOpenIdVerifier
import dev.steamvault.app.storage.InMemoryCredentialStore
import dev.steamvault.app.storage.ProfileKind
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private const val VALID_STEAM_ID = "76561198042117903"

private fun callbackUrl(
    steamId: String = VALID_STEAM_ID,
    signed: String = "signed,claimed_id,identity,return_to",
    mode: String = "id_res",
): String {
    fun enc(s: String) = java.net.URLEncoder.encode(s, "UTF-8")
    val claimedId = "https://steamcommunity.com/openid/id/$steamId"
    return "${SteamOpenIdConfig.RETURN_TO}?" +
        "openid.mode=$mode" +
        "&openid.claimed_id=" + enc(claimedId) +
        "&openid.identity=" + enc(claimedId) +
        "&openid.return_to=" + enc(SteamOpenIdConfig.RETURN_TO) +
        "&openid.signed=" + enc(signed) +
        "&openid.sig=" + enc("Zm9vYmFy")
}

private class FakeOpenIdVerifier(
    var checkAuthResult: Boolean = true,
    private val loginUrl: String = "https://steamcommunity.com/openid/login?fake=1",
) : SteamOpenIdVerifier {
    var lastParams: Map<String, String>? = null
    var callCount = 0

    override fun buildLoginUrl(returnTo: String, realm: String): String = loginUrl

    override suspend fun checkAuthentication(params: Map<String, String>): Boolean {
        callCount++
        lastParams = params
        return checkAuthResult
    }
}

private class FakeLibraryFetcher(
    var games: List<OwnedGame> = emptyList(),
    var persona: SteamPersona? = null,
    var throwOnGames: Exception? = null,
) : SteamLibraryFetcher {
    override suspend fun getOwnedGames(steamId64: String): List<OwnedGame> {
        throwOnGames?.let { throw it }
        return games
    }

    override suspend fun getPlayerSummary(steamId64: String): SteamPersona? = persona
}

class SteamIdentityRepositoryTest {

    private fun repo(
        verifier: FakeOpenIdVerifier = FakeOpenIdVerifier(),
        fetcher: FakeLibraryFetcher = FakeLibraryFetcher(),
        store: InMemoryCredentialStore = InMemoryCredentialStore(),
    ): Triple<SteamIdentityRepository, FakeOpenIdVerifier, FakeLibraryFetcher> =
        Triple(SteamIdentityRepositoryImpl(store, verifier, fetcher), verifier, fetcher)

    @Test
    fun `initial state is signed out with no persona and no key`() {
        val (repo, _, _) = repo()
        val state = repo.state()
        assertFalse(state.isSignedIn)
        assertNull(state.steamId64)
        assertNull(state.personaName)
        assertFalse(state.hasWebApiKey)
    }

    @Test
    fun `buildLoginUrl delegates to the verifier`() {
        val (repo, _, _) = repo(verifier = FakeOpenIdVerifier(loginUrl = "https://steamcommunity.com/openid/login?x=1"))
        assertEquals("https://steamcommunity.com/openid/login?x=1", repo.buildLoginUrl())
    }

    @Test
    fun `completeLogin succeeds end to end and persists the SteamID64`() = runTest {
        val (repo, verifier, _) = repo()

        val result = repo.completeLogin(callbackUrl())

        assertTrue(result is SteamLoginResult.Success)
        assertEquals(VALID_STEAM_ID, (result as SteamLoginResult.Success).steamId64)
        assertEquals(VALID_STEAM_ID, repo.state().steamId64)
        assertTrue(repo.state().isSignedIn)
        assertEquals(1, verifier.callCount)
    }

    @Test
    fun `completeLogin fails on a malformed callback without calling the verifier at all`() = runTest {
        val (repo, verifier, _) = repo()

        val result = repo.completeLogin("not-even-a-url")

        assertTrue(result is SteamLoginResult.Failure)
        assertEquals(0, verifier.callCount)
        assertNull(repo.state().steamId64)
    }

    @Test
    fun `MUTATION PIN -- completeLogin fails when signed does not cover claimed_id, without calling the verifier`() = runTest {
        val (repo, verifier, _) = repo()

        val result = repo.completeLogin(callbackUrl(signed = "signed,identity,return_to"))

        assertTrue(result is SteamLoginResult.Failure)
        assertEquals(0, verifier.callCount)
        assertNull(repo.state().steamId64)
    }

    @Test
    fun `completeLogin fails when check_authentication rejects the assertion, and nothing is persisted`() = runTest {
        val (repo, verifier, _) = repo(verifier = FakeOpenIdVerifier(checkAuthResult = false))

        val result = repo.completeLogin(callbackUrl())

        assertTrue(result is SteamLoginResult.Failure)
        assertEquals(1, verifier.callCount)
        assertNull(repo.state().steamId64)
    }

    @Test
    fun `completeLogin fails for a claimed_id that is not a valid SteamID64, even after a passing check_authentication`() = runTest {
        val (repo, _, _) = repo()

        val result = repo.completeLogin(callbackUrl(steamId = "00000000000000000"))

        assertTrue(result is SteamLoginResult.Failure)
        assertNull(repo.state().steamId64)
    }

    @Test
    fun `setWebApiKey persists the key and flips hasWebApiKey`() {
        val (repo, _, _) = repo()
        repo.setWebApiKey("0123456789ABCDEF0123456789ABCDEF")
        assertTrue(repo.state().hasWebApiKey)
    }

    @Test
    fun `ownedGamesCountPreview fails when not signed in`() = runTest {
        val (repo, _, _) = repo()
        val result = repo.ownedGamesCountPreview()
        assertTrue(result.isFailure)
    }

    @Test
    fun `ownedGamesCountPreview fails when signed in but no Web API key is configured`() = runTest {
        val store = InMemoryCredentialStore().apply { setSteamId64(VALID_STEAM_ID) }
        val (repo, _, _) = repo(store = store)
        val result = repo.ownedGamesCountPreview()
        assertTrue(result.isFailure)
    }

    @Test
    fun `ownedGamesCountPreview returns the game count once signed in with a key configured`() = runTest {
        val store = InMemoryCredentialStore().apply {
            setSteamId64(VALID_STEAM_ID)
            setSteamWebApiKey("0123456789ABCDEF0123456789ABCDEF")
        }
        val fetcher = FakeLibraryFetcher(games = listOf(OwnedGame(440, "TF2", 1, "")))
        val (repo, _, _) = repo(fetcher = fetcher, store = store)

        val result = repo.ownedGamesCountPreview()

        assertEquals(1, result.getOrNull())
    }

    @Test
    fun `ownedGamesCountPreview surfaces a fetcher failure as a Result failure, not an exception`() = runTest {
        val store = InMemoryCredentialStore().apply {
            setSteamId64(VALID_STEAM_ID)
            setSteamWebApiKey("0123456789ABCDEF0123456789ABCDEF")
        }
        val fetcher = FakeLibraryFetcher(throwOnGames = RuntimeException("boom"))
        val (repo, _, _) = repo(fetcher = fetcher, store = store)

        val result = repo.ownedGamesCountPreview()

        assertTrue(result.isFailure)
    }

    @Test
    fun `ownedGames fails when not signed in`() = runTest {
        val (repo, _, _) = repo()
        assertTrue(repo.ownedGames().isFailure)
    }

    @Test
    fun `ownedGames fails when signed in but no Web API key is configured`() = runTest {
        val store = InMemoryCredentialStore().apply { setSteamId64(VALID_STEAM_ID) }
        val (repo, _, _) = repo(store = store)
        assertTrue(repo.ownedGames().isFailure)
    }

    @Test
    fun `ownedGames returns the full list once signed in with a key configured`() = runTest {
        val store = InMemoryCredentialStore().apply {
            setSteamId64(VALID_STEAM_ID)
            setSteamWebApiKey("0123456789ABCDEF0123456789ABCDEF")
        }
        val games = listOf(OwnedGame(440, "TF2", 1, ""), OwnedGame(570, "Dota 2", 2, ""))
        val (repo, _, _) = repo(fetcher = FakeLibraryFetcher(games = games), store = store)

        val result = repo.ownedGames()

        assertEquals(games, result.getOrNull())
    }

    @Test
    fun `ownedGames surfaces a fetcher failure as a Result failure, not an exception`() = runTest {
        val store = InMemoryCredentialStore().apply {
            setSteamId64(VALID_STEAM_ID)
            setSteamWebApiKey("0123456789ABCDEF0123456789ABCDEF")
        }
        val fetcher = FakeLibraryFetcher(throwOnGames = RuntimeException("boom"))
        val (repo, _, _) = repo(fetcher = fetcher, store = store)

        assertTrue(repo.ownedGames().isFailure)
    }

    @Test
    fun `ownedGamesCountPreview delegates to ownedGames -- the two can never drift`() = runTest {
        val store = InMemoryCredentialStore().apply {
            setSteamId64(VALID_STEAM_ID)
            setSteamWebApiKey("0123456789ABCDEF0123456789ABCDEF")
        }
        val games = listOf(OwnedGame(440, "TF2", 1, ""), OwnedGame(570, "Dota 2", 2, ""))
        val (repo, _, _) = repo(fetcher = FakeLibraryFetcher(games = games), store = store)

        assertEquals(2, repo.ownedGamesCountPreview().getOrNull())
    }

    @Test
    fun `refreshPersonaName is false when not signed in`() = runTest {
        val (repo, _, _) = repo()
        assertFalse(repo.refreshPersonaName())
    }

    @Test
    fun `refreshPersonaName is false when signed in but no Web API key is configured`() = runTest {
        val store = InMemoryCredentialStore().apply { setSteamId64(VALID_STEAM_ID) }
        val (repo, _, _) = repo(store = store)
        assertFalse(repo.refreshPersonaName())
    }

    @Test
    fun `refreshPersonaName persists the persona name once available`() = runTest {
        val store = InMemoryCredentialStore().apply {
            setSteamId64(VALID_STEAM_ID)
            setSteamWebApiKey("0123456789ABCDEF0123456789ABCDEF")
        }
        val fetcher = FakeLibraryFetcher(persona = SteamPersona(VALID_STEAM_ID, "Example"))
        val (repo, _, _) = repo(fetcher = fetcher, store = store)

        assertTrue(repo.refreshPersonaName())
        assertEquals("Example", repo.state().personaName)
    }

    @Test
    fun `signOut clears only Steam identity fields, leaving the vault connection intact`() = runTest {
        val store = InMemoryCredentialStore().apply {
            setApiKey("vault-key")
            setBaseUrl("http://192.168.1.50:8080")
            setProfileKind(ProfileKind.SYSTEM_VPN)
        }
        val (repo, _, _) = repo(store = store)
        repo.completeLogin(callbackUrl())
        repo.setWebApiKey("0123456789ABCDEF0123456789ABCDEF")
        assertTrue(repo.state().isSignedIn)

        repo.signOut()

        val state = repo.state()
        assertFalse(state.isSignedIn)
        assertNull(state.personaName)
        assertFalse(state.hasWebApiKey)
        assertEquals("vault-key", store.getApiKey())
        assertEquals("http://192.168.1.50:8080", store.getBaseUrl())
        assertEquals(ProfileKind.SYSTEM_VPN, store.getProfileKind())
    }
}
