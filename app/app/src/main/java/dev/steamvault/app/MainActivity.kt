package dev.steamvault.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import dev.steamvault.app.ui.gallery.GalleryScreen
import dev.steamvault.app.ui.theme.SteamVaultTheme

/**
 * Single-activity app shell (WP 4b.1). Renders the theme + the debug
 * status-icon gallery — this WP's only screen. Real navigation (bottom
 * nav / destinations for library, downloads, settings) is out of scope
 * here and arrives with the later WPs that need it (4b.4 onward).
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            SteamVaultTheme {
                GalleryScreen()
            }
        }
    }
}
