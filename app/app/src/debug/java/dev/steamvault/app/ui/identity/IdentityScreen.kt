package dev.steamvault.app.ui.identity

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import dev.steamvault.app.R
import dev.steamvault.app.repo.SteamIdentityState

/**
 * Minimal identity screen (WP 4b.3 brief): a sign-in button when signed
 * out, or the signed-in SteamID64/persona plus a sign-out button and a
 * library COUNT PREVIEW only (the brief's explicit boundary -- the real
 * library grid is WP 4b.4's job, this screen only proves the repository
 * wiring works end to end).
 *
 * **Unreachable from the UI since WP 4b.7** (superseded by
 * `ui/settings/SettingsScreen.kt`'s Steam identity section -- see that
 * file's kdoc and `MainActivity.kt`'s), same "kept but unreachable"
 * treatment WP 4b.3/4b.4 already gave `ui.gallery.GalleryScreen`.
 *
 * **Moved to `src/debug/` (WP 4b.9 carry-over, `docs/WORKPACKAGES.md`
 * Phase 4b header).** Same reasoning `ui/gallery/GalleryScreen.kt`'s own
 * kdoc gives for its WP 4b.4 move: `src/debug/` is AGP's standard
 * mechanism for "compiled for debug builds only", so this now-unreferenced
 * screen is excluded from the `release` variant by construction rather
 * than shipping dead code into a signed artefact. No test referenced this
 * file directly before or after the move (`IdentityScreen` was never
 * unit-tested on its own -- the state/logic it renders,
 * `dev.steamvault.app.repo.SteamIdentityState`/`SteamIdentityRepository`,
 * is covered by `SteamIdentityRepositoryTest`, which is untouched by this
 * move since it lives under `net/`/`repo/`, not `ui/`).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun IdentityScreen(
    state: SteamIdentityState,
    ownedGamesCountPreview: Result<Int>?,
    loginError: String?,
    onSignInClick: () -> Unit,
    onSignOutClick: () -> Unit,
    onRefreshLibraryCountClick: () -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(title = { Text(stringResource(R.string.identity_title)) })
        },
        containerColor = MaterialTheme.colorScheme.background,
    ) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            if (!state.isSignedIn) {
                Text(
                    text = stringResource(R.string.identity_signed_out_hint),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                // Review round S3: this is exactly where the device-only
                // verification steps (Valve's return_to acceptance, the
                // Custom Tab callback handoff, the real check_authentication
                // round trip) surface if any of them fail on a real device
                // -- `loginError` is always a fixed, secret-free string by
                // SteamLoginResult.Failure's contract, safe to render as-is.
                if (loginError != null) {
                    Text(
                        text = stringResource(R.string.identity_login_failed, loginError),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
                Button(onClick = onSignInClick) {
                    Text(stringResource(R.string.identity_sign_in))
                }
            } else {
                Text(
                    text = stringResource(R.string.identity_steamid_label, state.steamId64.orEmpty()),
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onBackground,
                )
                Text(
                    text = state.personaName?.let {
                        stringResource(R.string.identity_persona_label, it)
                    } ?: stringResource(R.string.identity_persona_unknown),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (!state.hasWebApiKey) {
                    Text(
                        text = stringResource(R.string.identity_no_web_api_key),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                } else {
                    Button(onClick = onRefreshLibraryCountClick) {
                        Text(stringResource(R.string.identity_refresh_library_count))
                    }
                    val previewText = when {
                        ownedGamesCountPreview == null -> stringResource(R.string.identity_library_count_unknown)
                        ownedGamesCountPreview.isSuccess -> {
                            val count = ownedGamesCountPreview.getOrThrow()
                            pluralStringResource(R.plurals.identity_library_count, count, count)
                        }
                        else -> stringResource(R.string.identity_library_count_error)
                    }
                    Text(
                        text = previewText,
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                OutlinedButton(onClick = onSignOutClick) {
                    Text(stringResource(R.string.identity_sign_out))
                }
            }
        }
    }
}
