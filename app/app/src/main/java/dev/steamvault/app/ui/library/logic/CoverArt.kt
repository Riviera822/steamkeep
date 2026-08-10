package dev.steamvault.app.ui.library.logic

/**
 * Cover-art URL + deterministic fallback tile (WP 4b.4) — Kotlin port of
 * `web/js/lib/cover-art.js` (docs/design/vault-app-mockup-NOTES.md,
 * "Covers"): 2:3 portrait artwork, and when it can't be shown (offline, CDN
 * miss for an unlisted appid), a styled tile rather than a blank rectangle.
 * `fallbackHues`/`fallbackPattern` derive their two ingredients
 * deterministically FROM the appid, same as the web port — same game
 * always gets the same fallback look, no extra request, no randomness to
 * make the JVM tests (`CoverArtTest`) flaky. Values are pinned to match the
 * web port's FNV-1a hash byte-for-byte (same inputs, same outputs on both
 * frontends) even though nothing currently cross-checks the two beyond
 * this shared design intent.
 *
 * `library_600x900.jpg` is Valve's own portrait-capsule asset path -- same
 * 2:3 ratio the mockup's fake covers already used. Loading is done with
 * Coil (`ui/library/CoverArtImage.kt`) rather than a hand-rolled bitmap
 * fetch -- see that file's kdoc for the "Coil pinned vs hand-rolled"
 * justification recorded for the reviewer.
 */
const val STEAM_CDN_HOST: String = "cdn.akamai.steamstatic.com"

fun coverArtUrl(appid: Int): String = "https://$STEAM_CDN_HOST/steam/apps/$appid/library_600x900.jpg"

/** Tiny deterministic string hash (FNV-1a), good enough for "pick a stable
 * decorative value from an integer id" -- not a security primitive. Mirrors
 * `web/js/lib/cover-art.js`'s `fnv1a` (32-bit unsigned arithmetic, same as
 * JS's `>>> 0`). */
private fun fnv1a(n: Long): Long {
    var h = 0x811c9dc5L
    for (ch in n.toString()) {
        h = h xor ch.code.toLong()
        h = (h * 0x01000193L) and 0xFFFFFFFFL
    }
    return h and 0xFFFFFFFFL
}

data class FallbackHues(val h1: Int, val h2: Int)

/** Two hues (0-359) for the fallback tile's gradient. Always the same pair
 * for the same appid. */
fun fallbackHues(appid: Int): FallbackHues {
    val h = fnv1a(appid.toLong())
    val h1 = (h % 360).toInt()
    val h2 = ((h ushr 9) % 360).toInt()
    return FallbackHues(h1, h2)
}

/** Which of the mockup's six decorative overlay patterns (0..5) this appid's
 * fallback tile uses. */
fun fallbackPattern(appid: Int): Int {
    // JS: fnv1a(appid * 2654435761) % 6 -- the multiplication is float-lossy
    // in JS for large appids but harmless (it's a decorative hash input,
    // not a security or correctness value); here it is done in Long with an
    // explicit 32-bit mask so the result stays deterministic and in-range
    // without relying on overflow semantics differing between platforms.
    val product = (appid.toLong() * 2654435761L) and 0xFFFFFFFFL
    return (fnv1a(product) % 6L).toInt()
}
