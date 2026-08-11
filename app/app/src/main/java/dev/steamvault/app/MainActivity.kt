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
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.profile.buildConnectivityProfile
import dev.steamvault.app.net.steam.SteamOpenIdConfig
import dev.steamvault.app.repo.SteamIdentityRepository
import dev.steamvault.app.repo.SteamIdentityRepositoryImpl
import dev.steamvault.app.repo.VaultCacheRepository
import dev.steamvault.app.repo.VaultGamesRepository
import dev.steamvault.app.repo.VaultJobsRepository
import dev.steamvault.app.repo.VaultMappingRepository
import dev.steamvault.app.storage.EncryptedCredentialStore
import dev.steamvault.app.storage.SharedPreferencesLibraryPreferences
import dev.steamvault.app.ui.downloads.DownloadsScreen
import dev.steamvault.app.ui.downloads.logic.countPending
import dev.steamvault.app.ui.library.LibraryScreen
import dev.steamvault.app.ui.nav.BottomNavBar
import dev.steamvault.app.ui.nav.Destination
import dev.steamvault.app.ui.onboarding.AndroidOnboardingStrings
import dev.steamvault.app.ui.onboarding.OnboardingController
import dev.steamvault.app.ui.onboarding.OnboardingMode
import dev.steamvault.app.ui.onboarding.OnboardingScreen
import dev.steamvault.app.ui.settings.AndroidSettingsStrings
import dev.steamvault.app.ui.settings.SettingsController
import dev.steamvault.app.ui.settings.SettingsScreen
import dev.steamvault.app.ui.theme.SteamVaultTheme
import kotlinx.coroutines.launch

/**
 * Single-activity app shell. As of WP 4b.7, [vaultApiClientState] and
 * [showOnboarding] are the two pieces of state that decide what the whole
 * app shows: onboarding (this WP's [OnboardingScreen]) when there is no
 * working vault-api connection, the normal three-destination shell
 * otherwise -- see `ui/onboarding/logic/OnboardingSteps.kt::shouldShowOnboarding`
 * for the underlying pure rule this mirrors.
 *
 * **The WP 4b.4/4b.7 gap this WP closes.** Before this WP, no screen wrote
 * a vault-api base URL/API key/connectivity-profile kind into
 * [dev.steamvault.app.storage.CredentialStore] at all -- [vaultApiClientState]
 * was permanently `null` on every real install, and
 * `net/profile/ConnectivityProfileFactory.kt`'s `buildConnectivityProfile`
 * kdoc documented this as
 * "WP 4b.7's job, not a prerequisite of this one". [OnboardingScreen] /
 * [OnboardingController] are that missing write path; [refreshVaultApiClient]
 * is what makes the rest of the app shell notice a connection appeared (or
 * disappeared -- Settings' Disconnect).
 *
 * **Full-screen swap, not a modal overlay -- see [OnboardingScreen]'s own
 * kdoc** for why this differs from `web/js/onboarding.js`'s dialog-overlay
 * approach: this codebase already committed to a plain state-based screen
 * switcher (`ui/nav/Destination.kt`'s kdoc), and onboarding is simply one
 * more top-level state alongside the three [Destination]s, gating them
 * entirely rather than floating above them.
 */
class MainActivity : ComponentActivity() {

    private val credentialStore by lazy { EncryptedCredentialStore(applicationContext) }
    private val identityRepository: SteamIdentityRepository by lazy {
        SteamIdentityRepositoryImpl(credentialStore)
    }
    private val libraryPreferences by lazy { SharedPreferencesLibraryPreferences(applicationContext) }

    /** Long-lived for the whole app process (same category as
     * [identityRepository]/[credentialStore]) -- `onNewIntent` needs a
     * stable reference to route a Steam OpenID callback into while
     * onboarding is the active screen. */
    private val onboardingController: OnboardingController by lazy {
        OnboardingController(credentialStore, identityRepository, AndroidOnboardingStrings(resources))
    }

    /** `null` until a vault-api connection has been configured -- see this
     * class's kdoc. Rebuilt by [refreshVaultApiClient] whenever the
     * connection changes (onboarding finishes, Settings disconnects). */
    private var vaultApiClientState by mutableStateOf<VaultApiClient?>(null)

    /** Rebuilt alongside [vaultApiClientState] so it always reflects the
     * SAME client (and can be reached directly from `onNewIntent` -- unlike
     * `LibraryDestinationContent`'s repositories, this one needs a stable
     * identity outside Compose's `remember`, for the same reason
     * [onboardingController] does). */
    private var settingsControllerState by mutableStateOf<SettingsController?>(null)

    private var showOnboarding by mutableStateOf(false)
    private var onboardingMode by mutableStateOf(OnboardingMode.FIRST_RUN)

    /** Latest `GET /v1/jobs` snapshot from WHICHEVER screen is currently
     * polling jobs (Library or Downloads -- both now report through
     * `onJobsSnapshot`, see `LibraryScreen.kt`/`DownloadsScreen.kt`'s own
     * kdoc for that parameter). Feeds the bottom-nav pip
     * ([dev.steamvault.app.ui.downloads.logic.countPending]).
     *
     * **Honest scope limitation (WP 4b.5).** This is NOT a background poll
     * -- it is only ever updated while a jobs-polling screen is on screen,
     * same foreground-only constraint every poll loop in this app has
     * before WP 4b.8's WorkManager wiring lands. */
    private var pendingJobsSnapshot by mutableStateOf<List<JobSummary>>(emptyList())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        refreshVaultApiClient()
        if (vaultApiClientState == null) openOnboarding(OnboardingMode.FIRST_RUN)
        handleIntent(intent)

        setContent {
            SteamVaultTheme {
                if (showOnboarding) {
                    OnboardingScreen(
                        controller = onboardingController,
                        onFinished = {
                            refreshVaultApiClient()
                            showOnboarding = false
                        },
                        onCancelled = { showOnboarding = false },
                        onLaunchSteamLogin = { url -> launchSteamLogin(url) },
                    )
                } else {
                    var destination by remember { mutableStateOf(Destination.LIBRARY) }
                    val pendingJobsCount = countPending(pendingJobsSnapshot)

                    Scaffold(
                        bottomBar = {
                            BottomNavBar(
                                current = destination,
                                pendingJobsCount = pendingJobsCount,
                                onSelect = { destination = it },
                            )
                        },
                    ) { innerPadding ->
                        Box(modifier = Modifier.fillMaxSize().padding(innerPadding)) {
                            when (destination) {
                                Destination.LIBRARY -> LibraryDestinationContent()
                                Destination.DOWNLOADS -> DownloadsDestinationContent()
                                Destination.SETTINGS -> SettingsDestinationContent()
                            }
                        }
                    }
                }
            }
        }
    }

    /** Recomputes [vaultApiClientState]/[settingsControllerState] from
     * whatever is currently in [credentialStore] -- call after anything
     * that writes the connection ([OnboardingController.finish] via
     * `onFinished`, [SettingsController.disconnect] via `onDisconnected`). */
    private fun refreshVaultApiClient() {
        val profile = buildConnectivityProfile(credentialStore)
        val hasApiKey = !credentialStore.getApiKey().isNullOrBlank()
        val client = if (profile != null && hasApiKey) {
            // Review fix (N4): read the store FRESH inside the lambda, not a
            // captured local -- VaultApiClient's own kdoc promises
            // apiKeyProvider is "read fresh on every call... so a key change
            // in CredentialStore takes effect on the very next request
            // without rebuilding this client"; capturing a snapshot string
            // here would have silently broken that promise for this app's
            // only caller.
            VaultApiClient(profile, apiKeyProvider = { credentialStore.getApiKey().orEmpty() })
        } else {
            null
        }
        vaultApiClientState = client
        settingsControllerState = client?.let {
            SettingsController(it, credentialStore, identityRepository, AndroidSettingsStrings(resources))
        }
        // Review fix (N3): a stale pip count from the connection that just
        // disappeared (Settings' Disconnect) must not linger on the bottom
        // nav once Library/Downloads are unreachable -- there is no poll
        // left running to correct it on its own.
        pendingJobsSnapshot = emptyList()
    }

    private fun openOnboarding(mode: OnboardingMode) {
        onboardingController.start(mode)
        onboardingMode = mode
        showOnboarding = true
    }

    @Composable
    private fun LibraryDestinationContent() {
        val client = vaultApiClientState
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
            onJobsSnapshot = { pendingJobsSnapshot = it },
        )
    }

    /** WP 4b.5's screen (Downloads + job control). */
    @Composable
    private fun DownloadsDestinationContent() {
        val client = vaultApiClientState
        if (client == null) {
            NotConnectedPlaceholder()
            return
        }
        DownloadsScreen(
            jobsRepository = remember(client) { VaultJobsRepository(client) },
            gamesRepository = remember(client) { VaultGamesRepository(client) },
            onJobsSnapshot = { pendingJobsSnapshot = it },
        )
    }

    /** WP 4b.7's screen -- replaces the previous bare
     * `ui.identity.IdentityScreen` placeholder (still compiles,
     * unreferenced -- same "kept but unreachable" treatment WP 4b.3/4b.4
     * gave `ui.gallery.GalleryScreen`). */
    @Composable
    private fun SettingsDestinationContent() {
        val controller = settingsControllerState
        if (controller == null) {
            NotConnectedPlaceholder()
            return
        }
        SettingsScreen(
            controller = controller,
            onSignInSteamClick = { launchSteamLogin(controller.buildSteamLoginUrl()) },
            onReconnectClick = { openOnboarding(OnboardingMode.RECONNECT) },
            onDisconnected = {
                refreshVaultApiClient()
                openOnboarding(OnboardingMode.FIRST_RUN)
            },
        )
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    /** Only acts on a redirect matching this app's own OpenID return_to --
     * see [SteamOpenIdConfig]. Routes to whichever controller currently
     * owns the visible Steam sign-in flow -- see this class's kdoc and
     * [settingsControllerState]'s. */
    private fun handleIntent(intent: Intent?) {
        val data = intent?.dataString ?: return
        if (!data.startsWith(SteamOpenIdConfig.RETURN_TO)) return

        lifecycleScope.launch {
            val settings = settingsControllerState
            when {
                showOnboarding -> onboardingController.completeSteamLogin(data)
                settings != null -> settings.completeSteamLogin(data)
                else -> {
                    // Review fix (N2): neither screen is currently active to
                    // route this into (e.g. the connection was disconnected
                    // between launching the Custom Tab and the redirect
                    // arriving) -- still consume the pending login state
                    // directly through the repository, ignoring the result,
                    // so a dropped/unroutable callback cannot leave
                    // PendingLoginState holding a value forever. This is
                    // what makes "single-use" literally true regardless of
                    // which screen happens to be showing when the redirect
                    // lands, not just when a controller is listening.
                    identityRepository.completeLogin(data)
                }
            }
        }
    }

    private fun launchSteamLogin(url: String) {
        CustomTabsIntent.Builder().build().launchUrl(this, Uri.parse(url))
    }
}

/** Shown for Library/Downloads/Settings if, somehow, the connection
 * disappears out from under a still-composed screen (e.g. a stale
 * recomposition mid-disconnect) -- normally unreachable in practice since
 * [dev.steamvault.app.ui.nav.Destination] is a plain state switch and
 * disconnecting always routes through [MainActivity.openOnboarding]. */
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
