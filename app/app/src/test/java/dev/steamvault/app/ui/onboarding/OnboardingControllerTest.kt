package dev.steamvault.app.ui.onboarding

import dev.steamvault.app.net.connection.ConnectionFailureReason
import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.repo.SteamIdentityRepository
import dev.steamvault.app.repo.SteamIdentityState
import dev.steamvault.app.repo.SteamLoginResult
import dev.steamvault.app.storage.InMemoryCredentialStore
import dev.steamvault.app.storage.ProfileKind
import dev.steamvault.app.ui.onboarding.logic.OnboardingStep
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private const val VALID_KEY = "0123456789ABCDEF0123456789abcdef"

private class FakeOnboardingStrings : OnboardingStrings {
    override fun invalidUrl() = "invalid-url"
    override fun enterApiKeyFirst() = "enter-key-first"
    override fun connectionOk() = "ok"
    override fun connectionFailure(reason: ConnectionFailureReason) = "failure:$reason"
    override fun invalidWebApiKeyFormat() = "invalid-format"
    override fun webApiKeySaveFailed(cause: Throwable) = "save-failed:${cause.message}"
}

private class FakeSteamIdentityRepository(
    private var current: SteamIdentityState = SteamIdentityState(null, null, false),
    var loginResult: SteamLoginResult = SteamLoginResult.Success("76561198042117903"),
) : SteamIdentityRepository {
    var setWebApiKeyCalls = mutableListOf<String>()
    var buildLoginUrlCalls = 0
    var completeLoginCalls = 0
    var signOutCalls = 0
    var throwOnSetWebApiKey: Exception? = null

    override fun state(): SteamIdentityState = current
    override fun buildLoginUrl(): String {
        buildLoginUrlCalls++
        return "https://steamcommunity.com/openid/login?fake=1"
    }

    override suspend fun completeLogin(rawCallbackUrl: String): SteamLoginResult {
        completeLoginCalls++
        if (loginResult is SteamLoginResult.Success) {
            current = current.copy(steamId64 = (loginResult as SteamLoginResult.Success).steamId64)
        }
        return loginResult
    }

    override fun setWebApiKey(key: String) {
        throwOnSetWebApiKey?.let { throw it }
        setWebApiKeyCalls.add(key)
        current = current.copy(hasWebApiKey = true)
    }

    override suspend fun refreshPersonaName(): Boolean = false
    override suspend fun ownedGamesCountPreview(): Result<Int> = Result.success(0)
    override suspend fun ownedGames(): Result<List<OwnedGame>> = Result.success(emptyList())
    override fun signOut() {
        signOutCalls++
        current = SteamIdentityState(null, null, false)
    }
}

class OnboardingControllerTest {

    private fun controller(
        store: InMemoryCredentialStore = InMemoryCredentialStore(),
        identity: FakeSteamIdentityRepository = FakeSteamIdentityRepository(),
        strings: OnboardingStrings = FakeOnboardingStrings(),
    ) = Triple(OnboardingController(store, identity, strings), store, identity)

    // ---- start() / navigation delegation -------------------------------------

    @Test
    fun `start resets to step 1 and seeds fields from the credential store`() {
        val store = InMemoryCredentialStore().apply {
            setBaseUrl("http://192.168.1.50:8080")
            setProfileKind(ProfileKind.PUBLIC_DOMAIN)
        }
        val (controller, _, _) = controller(store = store)

        controller.start(OnboardingMode.RECONNECT)

        assertEquals(OnboardingStep.CONNECT, controller.step)
        assertEquals(OnboardingMode.RECONNECT, controller.mode)
        assertEquals("http://192.168.1.50:8080", controller.baseUrlText)
        assertEquals(ConnectivityProfileChoice.PUBLIC_DOMAIN, controller.profileChoice)
        assertEquals("", controller.apiKeyText) // never pre-filled -- a secret is not re-shown
        assertFalse(controller.tested)
    }

    @Test
    fun `first-run mode cannot be cancelled, reconnect mode can`() {
        val (controller, _, _) = controller()
        controller.start(OnboardingMode.FIRST_RUN)
        assertFalse(controller.canCancelWithoutFinishing)
        controller.start(OnboardingMode.RECONNECT)
        assertTrue(controller.canCancelWithoutFinishing)
    }

    @Test
    fun `MUTATION PIN -- next() delegates to the step machine's gate -- refuses to leave step 1 untested`() {
        val (controller, _, _) = controller()
        controller.start(OnboardingMode.FIRST_RUN)
        controller.next()
        assertEquals(OnboardingStep.CONNECT, controller.step) // unchanged: not tested
    }

    // ---- Steam login delegation ------------------------------------------------

    @Test
    fun `buildSteamLoginUrl clears any previous login error and delegates to the repository`() {
        val (controller, _, identity) = controller()
        controller.start(OnboardingMode.FIRST_RUN)

        val url = controller.buildSteamLoginUrl()

        assertEquals(1, identity.buildLoginUrlCalls)
        assertTrue(url.isNotEmpty())
        assertNull(controller.loginError)
    }

    @Test
    fun `completeSteamLogin on success refreshes identityState with no error`() = runTest {
        val (controller, _, identity) = controller(
            identity = FakeSteamIdentityRepository(loginResult = SteamLoginResult.Success("76561198042117903")),
        )
        controller.start(OnboardingMode.FIRST_RUN)

        controller.completeSteamLogin("steamvault://auth/openid-return?whatever=1")

        assertEquals(1, identity.completeLoginCalls)
        assertEquals("76561198042117903", controller.identityState.steamId64)
        assertNull(controller.loginError)
    }

    @Test
    fun `completeSteamLogin on failure surfaces the fixed reason string`() = runTest {
        val (controller, _, _) = controller(
            identity = FakeSteamIdentityRepository(loginResult = SteamLoginResult.Failure("rejected")),
        )
        controller.start(OnboardingMode.FIRST_RUN)

        controller.completeSteamLogin("steamvault://auth/openid-return?whatever=1")

        assertEquals("rejected", controller.loginError)
    }

    // ---- submitWebApiKeyEntry: the compose-state clearing pin (WP 4b.7 brief) --

    @Test
    fun `MUTATION PIN -- webApiKeyInput is cleared after a SUCCESSFUL submit`() {
        val (controller, _, identity) = controller()
        controller.start(OnboardingMode.FIRST_RUN)
        controller.webApiKeyInput = VALID_KEY

        controller.submitWebApiKeyEntry()

        assertEquals("", controller.webApiKeyInput)
        assertNull(controller.webApiKeyError)
        assertEquals(listOf(VALID_KEY), identity.setWebApiKeyCalls)
        assertTrue(controller.identityState.hasWebApiKey)
    }

    @Test
    fun `MUTATION PIN -- webApiKeyInput is cleared even after a REJECTED (invalid format) submit`() {
        val (controller, _, identity) = controller()
        controller.start(OnboardingMode.FIRST_RUN)
        controller.webApiKeyInput = "not-a-valid-key"

        controller.submitWebApiKeyEntry()

        assertEquals("", controller.webApiKeyInput)
        assertEquals("invalid-format", controller.webApiKeyError)
        assertTrue(identity.setWebApiKeyCalls.isEmpty())
    }

    @Test
    fun `MUTATION PIN -- webApiKeyInput is cleared even when the repository THROWS on persist`() {
        val (controller, _, identity) = controller()
        identity.throwOnSetWebApiKey = IllegalStateException("boom")
        controller.start(OnboardingMode.FIRST_RUN)
        controller.webApiKeyInput = VALID_KEY

        controller.submitWebApiKeyEntry()

        assertEquals("", controller.webApiKeyInput)
        assertEquals("save-failed:boom", controller.webApiKeyError)
    }

    // ---- finish() persists exactly the connection fields -----------------------

    @Test
    fun `finish persists base URL, profile kind, and API key -- and nothing Steam-related`() {
        val (controller, store, _) = controller()
        controller.start(OnboardingMode.FIRST_RUN)
        controller.baseUrlText = " http://192.168.1.50:8080 "
        controller.apiKeyText = " secret-key "
        controller.profileChoice = ConnectivityProfileChoice.SYSTEM_VPN

        controller.finish()

        assertEquals("http://192.168.1.50:8080", store.getBaseUrl())
        assertEquals("secret-key", store.getApiKey())
        assertEquals(ProfileKind.SYSTEM_VPN, store.getProfileKind())
    }
}
