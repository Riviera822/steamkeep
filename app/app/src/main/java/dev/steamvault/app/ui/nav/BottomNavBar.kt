package dev.steamvault.app.ui.nav

import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.res.stringResource

/** The three-item bottom nav (WP 4b.4 brief) -- see [Destination]'s kdoc for
 * why this is a plain state switch, not Navigation Compose. */
@Composable
fun BottomNavBar(current: Destination, onSelect: (Destination) -> Unit) {
    NavigationBar {
        for (destination in Destination.entries) {
            NavigationBarItem(
                selected = destination == current,
                onClick = { onSelect(destination) },
                icon = {
                    when (destination) {
                        Destination.LIBRARY -> LibraryNavIcon()
                        Destination.DOWNLOADS -> DownloadsNavIcon()
                        Destination.SETTINGS -> SettingsNavIcon()
                    }
                },
                label = { Text(stringResource(destination.labelRes)) },
            )
        }
    }
}
