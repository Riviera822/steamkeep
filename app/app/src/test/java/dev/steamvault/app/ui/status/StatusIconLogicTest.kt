package dev.steamvault.app.ui.status

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * JVM unit tests for the status-icon pure logic (WP 4b.1 brief:
 * "extract the animate-or-not decision into a pure function" + "JVM unit
 * tests for the pure logic (icon-kind mapping, animate-or-not decision)").
 * No Android framework/Robolectric dependency — everything under test is
 * plain Kotlin, runnable on the bare JVM (no emulator/device available in
 * this environment).
 */
class StatusIconLogicTest {

    // ---------- icon-kind -> glyph mapping ----------
    // 1:1 with KIND_GLYPH in web/js/components/status-icon.js — pin every
    // entry by name so a future edit that silently changes one kind's
    // glyph shape fails a named test instead of just looking different.

    @Test
    fun `cached maps to check`() {
        assertEquals(GlyphShape.CHECK, glyphFor(StatusKind.CACHED))
    }

    @Test
    fun `none maps to download`() {
        assertEquals(GlyphShape.DOWNLOAD, glyphFor(StatusKind.NONE))
    }

    @Test
    fun `running maps to download`() {
        assertEquals(GlyphShape.DOWNLOAD, glyphFor(StatusKind.RUNNING))
    }

    @Test
    fun `stale maps to refresh`() {
        assertEquals(GlyphShape.REFRESH, glyphFor(StatusKind.STALE))
    }

    @Test
    fun `updating maps to refresh`() {
        assertEquals(GlyphShape.REFRESH, glyphFor(StatusKind.UPDATING))
    }

    @Test
    fun `verify maps to refresh`() {
        assertEquals(GlyphShape.REFRESH, glyphFor(StatusKind.VERIFY))
    }

    @Test
    fun `paused maps to pause`() {
        assertEquals(GlyphShape.PAUSE, glyphFor(StatusKind.PAUSED))
    }

    @Test
    fun `error maps to bang`() {
        assertEquals(GlyphShape.BANG, glyphFor(StatusKind.ERROR))
    }

    @Test
    fun `warn maps to bang`() {
        assertEquals(GlyphShape.BANG, glyphFor(StatusKind.WARN))
    }

    @Test
    fun `cancelled maps to stop`() {
        assertEquals(GlyphShape.STOP, glyphFor(StatusKind.CANCELLED))
    }

    @Test
    fun `every StatusKind has a glyph mapping`() {
        // Exhaustiveness guard: if a new StatusKind is ever added without
        // updating glyphFor's `when`, this loop (not just the compiler)
        // catches it — glyphFor's `when` has no else branch, so a missing
        // case is already a compile error, but this also documents the
        // invariant as a runtime-checkable fact.
        for (kind in StatusKind.entries) {
            glyphFor(kind) // must not throw
        }
    }

    // ---------- wire-name round trip / unknown-kind fallback ----------

    @Test
    fun `wire name round trips for every kind`() {
        for (kind in StatusKind.entries) {
            assertEquals(kind, StatusKind.fromWireName(kind.wireName))
        }
    }

    @Test
    fun `unknown wire name falls back to none`() {
        assertEquals(StatusKind.NONE, StatusKind.fromWireName("totally-unrecognized-kind"))
    }

    // ---------- animate-or-not decision (the reduced-motion disable path) ----------
    // This is the fail-closed-direction pin the WP brief calls for: flip
    // the "reduced motion -> never animate" branch and one of these tests
    // must die (LEARNINGS.md "Testing discipline" — pin the default
    // direction, not just the happy path).

    @Test
    fun `running animates when animators are enabled`() {
        assertTrue(shouldAnimate(StatusKind.RUNNING, animatorsEnabled = true))
    }

    @Test
    fun `updating animates when animators are enabled`() {
        assertTrue(shouldAnimate(StatusKind.UPDATING, animatorsEnabled = true))
    }

    @Test
    fun `verify animates when animators are enabled`() {
        assertTrue(shouldAnimate(StatusKind.VERIFY, animatorsEnabled = true))
    }

    @Test
    fun `running never animates when animators are disabled (reduced motion)`() {
        assertFalse(shouldAnimate(StatusKind.RUNNING, animatorsEnabled = false))
    }

    @Test
    fun `updating never animates when animators are disabled (reduced motion)`() {
        assertFalse(shouldAnimate(StatusKind.UPDATING, animatorsEnabled = false))
    }

    @Test
    fun `verify never animates when animators are disabled (reduced motion)`() {
        assertFalse(shouldAnimate(StatusKind.VERIFY, animatorsEnabled = false))
    }

    @Test
    fun `static kinds never animate even when animators are enabled`() {
        val staticKinds = listOf(
            StatusKind.CACHED,
            StatusKind.STALE,
            StatusKind.NONE,
            StatusKind.PAUSED,
            StatusKind.ERROR,
            StatusKind.WARN,
            StatusKind.CANCELLED,
        )
        for (kind in staticKinds) {
            assertFalse(
                "expected $kind to stay still even with animators enabled",
                shouldAnimate(kind, animatorsEnabled = true),
            )
        }
    }

    @Test
    fun `no kind animates when animators are disabled`() {
        for (kind in StatusKind.entries) {
            assertFalse(
                "expected $kind to stay still under reduced motion",
                shouldAnimate(kind, animatorsEnabled = false),
            )
        }
    }

    // ---------- download drift/opacity keyframe math ----------
    // Ported from the `vault-dlslide` CSS keyframes (theme.css); pinned at
    // the keyframe boundaries so a transcription slip is caught exactly.

    @Test
    fun `drift starts at -1_6 and ends at 1_8`() {
        assertEquals(-1.6f, downloadDriftFraction(0f), 0.0001f)
        assertEquals(1.8f, downloadDriftFraction(1f), 0.0001f)
    }

    @Test
    fun `drift is clamped outside the 0 to 1 range`() {
        assertEquals(-1.6f, downloadDriftFraction(-5f), 0.0001f)
        assertEquals(1.8f, downloadDriftFraction(5f), 0.0001f)
    }

    @Test
    fun `opacity keyframes match the CSS 0-30-70-100 percent stops`() {
        assertEquals(0.35f, downloadOpacityFraction(0f), 0.0001f)
        assertEquals(1f, downloadOpacityFraction(0.3f), 0.0001f)
        assertEquals(1f, downloadOpacityFraction(0.7f), 0.0001f)
        assertEquals(0.35f, downloadOpacityFraction(1f), 0.0001f)
    }

    @Test
    fun `opacity never reaches zero (status icon must never be blank)`() {
        // mockup NOTES.md round 7: the glyph doubles as the tap target and
        // must never fade to a fully blank disc.
        var t = 0f
        while (t <= 1f) {
            assertTrue(
                "opacity dropped to $t at progress=$t",
                downloadOpacityFraction(t) >= 0.35f,
            )
            t += 0.01f
        }
    }
}
