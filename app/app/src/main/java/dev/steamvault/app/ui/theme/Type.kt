package dev.steamvault.app.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/**
 * Typography basics (WP 4b.1).
 *
 * The mockup's type stack (mockup-notes.md "Type") is a CSS font-family
 * fallback list with no bundled webfont: Roboto for UI text, Roboto
 * Condensed for capsule art titles, Roboto Mono for numbers/ids/paths.
 * Roboto ships as the Android system font on every device this app targets
 * (minSdk 26), so [FontFamily.Default] already renders as Roboto with no
 * bundled font files needed. Condensed and Mono variants are NOT bundled in
 * this WP (no capsule/library UI exists yet) — [FontFamily.Monospace] is
 * used as the interim numeric/id family; a real Roboto Condensed/Mono
 * asset pair is a follow-up WP (4b.4 library grid) if the system fallback
 * proves visually insufficient.
 */
val VaultTypography = Typography(
    titleLarge = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Medium,
        fontSize = 22.sp,
    ),
    titleMedium = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Medium,
        fontSize = 16.sp,
    ),
    bodyLarge = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 16.sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 14.sp,
    ),
    labelSmall = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Medium,
        fontSize = 11.sp,
    ),
)

/** Monospace style for numbers/ids/paths — mockup's `--mono` role. */
val VaultMonoStyle = TextStyle(
    fontFamily = FontFamily.Monospace,
    fontSize = 13.sp,
)
