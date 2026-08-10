package dev.steamvault.app.ui.library.logic

import java.util.Locale

/**
 * Bytes -> a short "N GB" string (WP 4b.4) -- Kotlin port of
 * `web/js/lib/format.js`'s `formatBytesGB` (mockup's `gb()` helper): >=100
 * GB rounds to a whole number, otherwise one decimal place. `null`/
 * non-positive bytes are never faked into a number -- callers decide what
 * to show instead (mockup rule: "a never-downloaded game shows the icon
 * alone rather than a fake dash").
 * @return `null` when there is nothing honest to print.
 */
fun formatBytesGB(bytes: Long?): String? {
    if (bytes == null || bytes <= 0L) return null
    val gb = bytes / 1_073_741_824.0
    // Locale.ROOT, not the default locale: a German (or any comma-decimal)
    // device would otherwise render "1,5 GB" -- same class of bug
    // docs/LEARNINGS.md's PowerShell section documents for locale-sensitive
    // number formatting ("force InvariantCulture"), here on the JVM side.
    val formatted = if (gb >= 100) {
        String.format(Locale.ROOT, "%.0f", gb)
    } else {
        String.format(Locale.ROOT, "%.1f", gb)
    }
    return "$formatted GB"
}
