package dev.steamvault.app.ui.gallery

import android.animation.ValueAnimator
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import dev.steamvault.app.R
import dev.steamvault.app.ui.status.StatusIcon
import dev.steamvault.app.ui.status.StatusIconSize
import dev.steamvault.app.ui.status.StatusKind

/**
 * Debug gallery screen — this WP's visible artifact (brief: "a debug
 * gallery screen showing every status-icon kind"). Not a real app screen:
 * navigation and the actual views (library/downloads/settings) arrive in
 * WPs 4b.4/4b.5/4b.7. This screen exists purely so the theme + status-icon
 * component can be seen rendered and manually verified.
 *
 * **Moved to `src/debug/` (WP 4b.4 review nit).** It has been unreachable
 * from the UI since WP 4b.3 replaced it in `MainActivity`'s `setContent`
 * (see that class's kdoc), but was still being compiled into the RELEASE
 * variant despite being a debug-only artifact -- `src/debug/` is AGP's
 * standard mechanism for exactly this ("compiled for debug builds only"),
 * so it is now excluded from `release` by construction rather than by
 * convention. No behavior change: still compiles, still covered by
 * `StatusIconLogicTest`/`StatusIconCrossFrontendContractTest` (those test
 * the pure logic/theme modules this screen renders, not this file itself,
 * and live in `src/test/`, which already runs against the debug variant).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun GalleryScreen() {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.gallery_title)) },
            )
        },
        containerColor = MaterialTheme.colorScheme.background,
    ) { innerPadding ->
        Column(modifier = Modifier.padding(innerPadding)) {
            Text(
                text = stringResource(R.string.gallery_subtitle),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
            )
            Text(
                text = stringResource(
                    if (ValueAnimator.areAnimatorsEnabled()) {
                        R.string.reduced_motion_off
                    } else {
                        R.string.reduced_motion_on
                    },
                ),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
            )
            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                items(StatusKind.entries) { kind ->
                    StatusIconRow(kind)
                }
            }
        }
    }
}

@Composable
private fun StatusIconRow(kind: StatusKind) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        StatusIcon(kind = kind, size = StatusIconSize.LARGE)
        Text(
            text = stringResource(kind.labelRes),
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.onBackground,
        )
        Text(
            text = kind.wireName,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
