package dev.steamvault.app.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/**
 * The app is committed to a single dark theme on purpose (mockup NOTES.md
 * "Committed to dark, on purpose": "the app surface is single-theme ...
 * there is no light variant of the app surface itself"). [isSystemInDarkTheme]
 * is therefore intentionally NOT read here — SteamVaultTheme always applies
 * the same dark scheme regardless of the system setting, matching the web
 * frontend (web/css/theme.css has no `@media (prefers-color-scheme: light)`
 * override for the app surface either).
 */
private val VaultDarkColorScheme = darkColorScheme(
    primary = VaultColors.Accent,
    onPrimary = VaultColors.AccentInk,
    secondary = VaultColors.AccentDeep,
    onSecondary = VaultColors.AccentInk,
    background = VaultColors.Bg,
    onBackground = VaultColors.Text,
    surface = VaultColors.Surface,
    onSurface = VaultColors.Text,
    surfaceVariant = VaultColors.Raised,
    onSurfaceVariant = VaultColors.Dim,
    outline = VaultColors.Line,
    outlineVariant = VaultColors.LineSoft,
    error = VaultColors.StatusDanger,
    onError = Color.Black,
)

@Composable
fun SteamVaultTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = VaultDarkColorScheme,
        typography = VaultTypography,
        content = content,
    )
}
