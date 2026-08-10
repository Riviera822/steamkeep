package dev.steamvault.app.ui.library.logic

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CoverArtTest {

    @Test
    fun `coverArtUrl builds the pinned Steam CDN path`() {
        assertEquals(
            "https://cdn.akamai.steamstatic.com/steam/apps/440/library_600x900.jpg",
            coverArtUrl(440),
        )
    }

    @Test
    fun `coverArtUrl only ever targets the pinned CDN host`() {
        // Mirrors the web port's CSP-scope note: this is the ONLY function
        // allowed to construct a URL against STEAM_CDN_HOST.
        val url = coverArtUrl(123456)
        assertTrue(url.startsWith("https://$STEAM_CDN_HOST/"))
    }

    @Test
    fun `fallbackHues are deterministic for the same appid`() {
        assertEquals(fallbackHues(440), fallbackHues(440))
    }

    @Test
    fun `fallbackHues differ for different appids (not a constant)`() {
        assertTrue(fallbackHues(440) != fallbackHues(570))
    }

    @Test
    fun `fallbackHues stay within 0-359`() {
        for (appid in listOf(1, 440, 570, 123456, Int.MAX_VALUE / 2)) {
            val hues = fallbackHues(appid)
            assertTrue(hues.h1 in 0..359)
            assertTrue(hues.h2 in 0..359)
        }
    }

    @Test
    fun `fallbackPattern is deterministic and within 0-5`() {
        for (appid in listOf(1, 440, 570, 123456)) {
            val pattern = fallbackPattern(appid)
            assertEquals(pattern, fallbackPattern(appid))
            assertTrue(pattern in 0..5)
        }
    }
}
