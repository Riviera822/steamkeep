package dev.steamvault.app

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.lifecycleScope
import dev.steamvault.app.net.steam.SteamOpenIdConfig
import dev.steamvault.app.repo.SteamIdentityRepository
import dev.steamvault.app.repo.SteamIdentityRepositoryImpl
import dev.steamvault.app.repo.SteamLoginResult
import dev.steamvault.app.storage.EncryptedCredentialStore
import dev.steamvault.app.ui.identity.IdentityScreen
import dev.steamvault.app.ui.theme.SteamVaultTheme
import kotlinx.coroutines.launch

/**
 * Single-activity app shell. As of WP 4b.3 this renders the Steam identity
 * screen (sign-in / signed-in state) in place of WP 4b.1's debug
 * status-icon gallery ([dev.steamvault.app.ui.gallery.GalleryScreen] still
 * compiles and is still covered by its own tests, but is no longer the
 * screen shown here -- real navigation between multiple destinations
 * arrives with the later WPs that need it, 4b.4/4b.5/4b.7. Since 4b.3/4b.4/
 * 4b.5 are branch-parallel work packages that each want to wire their own
 * screen into this single-activity shell, this wiring is expected to be
 * reconciled by whichever later work package introduces real navigation --
 * flagged here for the reviewer/orchestrator rather than hidden.
 */
class MainActivity : ComponentActivity() {

    private val credentialStore by lazy { EncryptedCredentialStore(applicationContext) }
    private val identityRepository: SteamIdentityRepository by lazy {
        SteamIdentityRepositoryImpl(credentialStore)
    }

    private var identityState by mutableStateOf(SteamIdentityScreenState())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        identityState = identityState.copy(identity = identityRepository.state())
        handleIntent(intent)

        setContent {
            SteamVaultTheme {
                IdentityScreen(
                    state = identityState.identity,
                    ownedGamesCountPreview = identityState.ownedGamesCountPreview,
                    loginError = identityState.loginError,
                    onSignInClick = { launchSteamLogin() },
                    onSignOutClick = {
                        identityRepository.signOut()
                        identityState = SteamIdentityScreenState(identity = identityRepository.state())
                    },
                    onRefreshLibraryCountClick = { refreshLibraryCountPreview() },
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    /** Only acts on a redirect matching this app's own OpenID return_to -- see [SteamOpenIdConfig]. */
    private fun handleIntent(intent: Intent?) {
        val data = intent?.dataString ?: return
        if (!data.startsWith(SteamOpenIdConfig.RETURN_TO)) return

        lifecycleScope.launch {
            when (val result = identityRepository.completeLogin(data)) {
                is SteamLoginResult.Success -> {
                    identityState = SteamIdentityScreenState(identity = identityRepository.state())
                }
                is SteamLoginResult.Failure -> {
                    // Review round S3: this branch is exactly where the
                    // device test's un-testable-here steps 1-3 (Valve's
                    // return_to acceptance, the Custom Tab -> onNewIntent
                    // handoff, the real check_authentication round trip)
                    // will land if any of them go wrong -- a silent branch
                    // here left the user with no signal at all. `reason` is
                    // always a fixed, secret-free string by
                    // SteamLoginResult.Failure's own contract, so showing
                    // it verbatim is safe; this is intentionally ONE line
                    // of state, not a new app-wide error-display
                    // convention (there isn't one yet -- that is a later
                    // WP's call).
                    identityState = identityState.copy(
                        identity = identityRepository.state(),
                        loginError = result.reason,
                    )
                }
            }
        }
    }

    private fun launchSteamLogin() {
        // Clear any stale error from a previous attempt before starting a new one.
        identityState = identityState.copy(loginError = null)
        val url = identityRepository.buildLoginUrl()
        CustomTabsIntent.Builder().build().launchUrl(this, Uri.parse(url))
    }

    private fun refreshLibraryCountPreview() {
        lifecycleScope.launch {
            val preview = identityRepository.ownedGamesCountPreview()
            identityState = identityState.copy(ownedGamesCountPreview = preview)
        }
    }
}

private data class SteamIdentityScreenState(
    val identity: dev.steamvault.app.repo.SteamIdentityState =
        dev.steamvault.app.repo.SteamIdentityState(steamId64 = null, personaName = null, hasWebApiKey = false),
    val ownedGamesCountPreview: Result<Int>? = null,
    val loginError: String? = null,
)
