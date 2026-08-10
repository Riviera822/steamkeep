package dev.steamvault.app

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.lifecycleScope
import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.profile.buildConnectivityProfile
import dev.steamvault.app.net.steam.SteamOpenIdConfig
import dev.steamvault.app.repo.SteamIdentityRepository
import dev.steamvault.app.repo.SteamIdentityRepositoryImpl
import dev.steamvault.app.repo.SteamLoginResult
import dev.steamvault.app.repo.VaultCacheRepository
import dev.steamvault.app.repo.VaultGamesRepository
import dev.steamvault.app.repo.VaultJobsRepository
import dev.steamvault.app.repo.VaultMappingRepository
import dev.steamvault.app.storage.EncryptedCredentialStore
import dev.steamvault.app.storage.SharedPreferencesLibraryPreferences
import dev.steamvault.app.ui.identity.IdentityScreen
import dev.steamvault.app.ui.library.LibraryScreen
import dev.steamvault.app.ui.nav.BottomNavBar
import dev.steamvault.app.ui.nav.Destination
import dev.steamvault.app.ui.theme.SteamVaultTheme
import kotlinx.coroutines.launch

/**
 * Single-activity app shell. As of WP 4b.4, navigation between the app's
 * three top-level destinations is REAL (see `ui/nav/Destination.kt` for the
 * plain-state-switcher justification) -- this is the reconciliation the WP
 * 4b.3 kdoc (previously here) flagged as needed.
 *
 * **Reconciliation of the WP 4b.3 interim state.** [IdentityScreen] moves
 * under the Settings destination: it is not yet the full "Onboarding +
 * settings" screen WP 4b.7 will build (vault connection profile, layout
 * preference, etc. all still need a home there), so Settings today shows
 * exactly what it showed before this WP -- the Steam identity block --
 * inside the new bottom-nav shell rather than as the activity's only
 * content. `dev.steamvault.app.ui.gallery.GalleryScreen` (WP 4b.1's debug
 * status-icon gallery, now under `src/debug/` -- WP 4b.4 review nit, see
 * its own kdoc) still compiles for debug builds and remains unreachable
 * from the UI, exactly as it already was after WP 4b.3 replaced it in
 * `setContent` -- this WP does not regress or restore its reachability
 * either way.
 *
 * **Gap this WP inherits, not introduces (see
 * `net/profile/ConnectivityProfileFactory.kt`'s kdoc for the full
 * explanation).** No screen writes a vault-api base URL / API key /
 * connectivity-profile kind into [dev.steamvault.app.storage.CredentialStore]
 * yet -- that is WP 4b.7's job, a branch-parallel sibling of this one, not
 * a prerequisite. [vaultApiClient] is therefore `null` on every real
 * install today, and Library/Downloads render an explicit
 * [NotConnectedPlaceholder] instead of attempting a doomed network call.
 * Settings (today: just [IdentityScreen]) has no dependency on the vault-api
 * connection and is fully usable regardless.
 */
class MainActivity : ComponentActivity() {

    private val credentialStore by lazy { EncryptedCredentialStore(applicationContext) }
    private val identityRepository: SteamIdentityRepository by lazy {
        SteamIdentityRepositoryImpl(credentialStore)
    }
    private val libraryPreferences by lazy { SharedPreferencesLibraryPreferences(applicationContext) }

    /** `null` until a vault-api connection has been configured (WP 4b.7) --
     * see this class's kdoc. */
    private val vaultApiClient: VaultApiClient? by lazy {
        val profile = buildConnectivityProfile(credentialStore) ?: return@lazy null
        val apiKey = credentialStore.getApiKey()?.takeIf { it.isNotBlank() } ?: return@lazy null
        VaultApiClient(profile, apiKeyProvider = { apiKey })
    }

    private var identityState by mutableStateOf(SteamIdentityScreenState())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        identityState = identityState.copy(identity = identityRepository.state())
        handleIntent(intent)

        setContent {
            SteamVaultTheme {
                var destination by remember { mutableStateOf(Destination.LIBRARY) }

                Scaffold(
                    bottomBar = { BottomNavBar(current = destination, onSelect = { destination = it }) },
                ) { innerPadding ->
                    Box(modifier = Modifier.fillMaxSize().padding(innerPadding)) {
                        when (destination) {
                            Destination.LIBRARY -> LibraryDestinationContent()
                            Destination.DOWNLOADS -> DownloadsPlaceholder()
                            Destination.SETTINGS -> IdentityScreen(
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
            }
        }
    }

    @Composable
    private fun LibraryDestinationContent() {
        val client = vaultApiClient
        if (client == null) {
            NotConnectedPlaceholder()
            return
        }
        LibraryScreen(
            gamesRepository = remember(client) { VaultGamesRepository(client) },
            jobsRepository = remember(client) { VaultJobsRepository(client) },
            mappingRepository = remember(client) { VaultMappingRepository(client) },
            cacheRepository = remember(client) { VaultCacheRepository(client) },
            identityRepository = identityRepository,
            libraryPreferences = libraryPreferences,
        )
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

/** WP 4b.5's screen (Downloads + job control) has not landed yet -- this is
 * a deliberate placeholder, not a fabricated feature, same posture
 * `web/js/views/library.js`'s `onOpen` no-op documents for its own
 * not-yet-shipped WP. */
@Composable
private fun DownloadsPlaceholder() {
    Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Text(
            text = stringResource(R.string.downloads_placeholder),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/** Shown for Library/Downloads until a vault-api connection is configured
 * (WP 4b.7 gap -- see [MainActivity]'s kdoc). */
@Composable
private fun NotConnectedPlaceholder() {
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = stringResource(R.string.not_connected_title),
            style = MaterialTheme.typography.bodyLarge,
        )
        Text(
            text = stringResource(R.string.not_connected_body),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

private data class SteamIdentityScreenState(
    val identity: dev.steamvault.app.repo.SteamIdentityState =
        dev.steamvault.app.repo.SteamIdentityState(steamId64 = null, personaName = null, hasWebApiKey = false),
    val ownedGamesCountPreview: Result<Int>? = null,
    val loginError: String? = null,
)
