package dev.steamvault.app.net.steam

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Literal fixture values only -- shared with `web/tests/steamid.test.js`
 * and `api/tests/test_steam_relay.py`'s own literal cases (WP brief:
 * "share the web's boundary values"), never derived from [SteamId64]'s own
 * `BASE`/`MAX` constants (`docs/LEARNINGS.md`: a derived expectation moves
 * with the bug it is supposed to catch).
 */
class SteamId64Test {

    private val base = "76561197960265728" // universe 1, type 1, instance 1, account 0
    private val max = "76561202255233023" // base + 0xFFFFFFFF (account number ceiling)
    private val realShaped = "76561198042117903" // the mockup's example SteamID64

    @Test
    fun `accepts the exact base and max boundary values`() {
        assertEquals(base, SteamId64.validate(base))
        assertEquals(max, SteamId64.validate(max))
    }

    @Test
    fun `accepts a real-shaped SteamID64 well inside the range`() {
        assertEquals(realShaped, SteamId64.validate(realShaped))
    }

    @Test
    fun `MUTATION PIN (base) -- one below the base is rejected`() {
        assertNull(SteamId64.validate("76561197960265727"))
    }

    @Test
    fun `MUTATION PIN (max) -- one above the max is rejected`() {
        assertNull(SteamId64.validate("76561202255233024"))
    }

    @Test
    fun `MUTATION PIN (length) -- 16 and 18 digits are both rejected`() {
        assertNull(SteamId64.validate("7656119796026572")) // 16 digits
        // 18 digits: base with a leading zero. Numerically this equals
        // base if the length check were ever dropped (leading zeros are
        // numerically insignificant) -- this is the case that actually
        // kills a length-check deletion, not the 16-digit one above.
        assertNull(SteamId64.validate("076561197960265728"))
    }

    @Test
    fun `rejects a non-digit character mixed into an otherwise correct-length string`() {
        assertNull(SteamId64.validate("7656119804211790a"))
        assertNull(SteamId64.validate("76561198-4211790"))
    }

    @Test
    fun `a sign character is rejected (regression case, NOT a digit-walk mutation kill)`() {
        // Kotlin's String.toLongOrNull() tolerates a leading '+'/'-' sign,
        // and these two 17-character strings ARE genuinely rejected today
        // -- but NOT because the digit walk is the thing catching them.
        // Honesty correction (review round): a signed 17-char string has at
        // most 16 actual digits, so its parsed MAGNITUDE is always a
        // 16-digit-or-shorter number, which is always < BASE (the smallest
        // 17-digit number, 10**16, is already below BASE) regardless of
        // whether the digit walk runs at all. This case is therefore
        // UNKILLABLE via the digit-walk deletion -- the range check alone
        // already rejects it. Kept as a literal regression pin (a future
        // refactor must not start accepting signed input), but the REAL
        // digit-walk mutation kill is the non-ASCII, in-range case below.
        assertNull(SteamId64.validate("+656119804211790")) // 17 chars, leading '+'
        assertNull(SteamId64.validate("-656119804211790")) // 17 chars, leading '-'
    }

    @Test
    fun `whitespace padding is rejected`() {
        assertNull(SteamId64.validate(" 6561198042117903"))
        assertNull(SteamId64.validate("6561198042117903 "))
    }

    @Test
    fun `rejects a non-ASCII look-alike digit string that is merely out of range`() {
        // U+0663 ARABIC-INDIC DIGIT THREE repeated 17 times -- numerically
        // 33333333333333333, well BELOW BASE. Kept as a literal regression
        // case, but honestly this one does NOT kill the digit-walk deletion
        // either (the range check alone already rejects it) -- see the
        // MUTATION PIN test below for the case that actually does.
        assertNull(SteamId64.validate("٣٣٣٣٣٣٣٣٣٣٣٣٣٣٣٣٣"))
    }

    @Test
    fun `MUTATION PIN (digit walk) -- an IN-RANGE non-ASCII numeral is rejected`() {
        // The actual gap the ASCII-digit walk closes, and the one review
        // round S1 asked to pin directly: Kotlin's String.toLongOrNull()
        // is implemented on top of Character.digit(), which recognizes
        // Unicode Nd (decimal digit) characters -- NOT just ASCII '0'-'9'.
        // This is the Arabic-Indic spelling of BASE itself (the same
        // literal `api/tests/test_steam_relay.py` uses for this exact
        // case): 17 Arabic-Indic digit characters, each mapping to the
        // same digit BASE's own ASCII spelling has
        // (٧=7,٦=6,٥=5,٦=6,١=1,١=1,٩=9,٧=7,٩=9,٦=6,٠=0,٢=2,٦=6,٥=5,٧=7,٢=2,٨=8).
        // Verified empirically (not assumed, docs/LEARNINGS.md "Testing
        // discipline"): `"٧٦٥٦١١٩٧٩٦٠٢٦٥٧٢٨".toLongOrNull()` returns
        // 76561197960265728L -- i.e. BASE exactly -- on this project's
        // pinned Kotlin/JVM toolchain. With the digit walk deleted, this
        // 17-character, IN-RANGE string would pass both the length check
        // and the range check and be WRONGLY ACCEPTED; the unmutated
        // function must reject it (it is not ASCII digits) and does.
        assertNull(SteamId64.validate("٧٦٥٦١١٩٧٩٦٠٢٦٥٧٢٨"))
    }

    @Test
    fun `rejects null`() {
        assertNull(SteamId64.validate(null))
    }

    @Test
    fun `rejects a numerically-valid-looking value that is merely the wrong shape (17 zeros)`() {
        assertNull(SteamId64.validate("00000000000000000"))
    }

    @Test
    fun `rejects empty string`() {
        assertNull(SteamId64.validate(""))
    }
}
