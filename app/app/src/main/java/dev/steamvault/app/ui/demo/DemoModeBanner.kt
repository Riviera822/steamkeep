package dev.steamvault.app.ui.demo

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import dev.steamvault.app.R
import dev.steamvault.app.ui.theme.VaultColors

/**
 * The persistent, visible "you are looking at sample data" indicator (WP
 * APP-DEMO brief constraint 1: "It must be impossible to confuse with a
 * real vault... A user who forgets they are in demo mode and concludes
 * their cache is broken is the failure this must not have"). Rendered at
 * the top of every screen that shows data while demo mode is active --
 * `ui/library/LibraryScreen.kt`, `ui/downloads/DownloadsScreen.kt`,
 * `ui/settings/SettingsScreen.kt`, `ui/detail/GameDetailSheet.kt` -- each
 * call site is a plain `if (demoMode) DemoModeBanner()`, never a data-driven
 * decision this composable makes on its own.
 *
 * [VaultColors.StatusRun] (the same "pay attention, not an error" amber this
 * app already reserves for running/paused states, `Color.kt`'s own kdoc) is
 * deliberately NOT [VaultColors.StatusDanger] -- demo mode is not a failure
 * state, and painting it red would misreport an intentional choice as
 * something broken (`docs/LEARNINGS.md`'s "Status-icon kinds follow the
 * SHIPPED status set" entry documents the same reasoning for a different
 * case: never reuse the error glyph/colour for a non-error state).
 */
@Composable
fun DemoModeBanner() {
    Text(
        text = stringResource(R.string.demo_mode_banner),
        modifier = Modifier
            .fillMaxWidth()
            .background(VaultColors.StatusRun)
            .padding(vertical = 6.dp, horizontal = 12.dp),
        color = VaultColors.StatusIconInk,
        style = MaterialTheme.typography.labelLarge,
        fontWeight = FontWeight.Bold,
        textAlign = TextAlign.Center,
    )
}
