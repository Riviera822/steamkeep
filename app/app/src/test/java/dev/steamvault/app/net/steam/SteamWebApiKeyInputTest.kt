package dev.steamvault.app.net.steam

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private const val VALID_KEY = "0123456789ABCDEF0123456789abcdef"
private const val INVALID_FORMAT_ERROR = "Enter exactly 32 hexadecimal characters."

class SteamWebApiKeyInputTest {

    // ---- validSteamWebApiKey ------------------------------------------------

    @Test
    fun `accepts exactly 32 hex characters, mixed case`() {
        assertTrue(validSteamWebApiKey("0123456789ABCDEF0123456789abcdef"))
    }

    @Test
    fun `MUTATION PIN -- rejects 31 or 33 characters`() {
        assertFalse(validSteamWebApiKey("0123456789ABCDEF0123456789abcde")) // 31
        assertFalse(validSteamWebApiKey("0123456789ABCDEF0123456789abcdef0")) // 33
    }

    @Test
    fun `rejects a non-hex character at any position`() {
        assertFalse(validSteamWebApiKey("g123456789ABCDEF0123456789abcde0"))
    }

    @Test
    fun `rejects an empty string`() {
        assertFalse(validSteamWebApiKey(""))
    }

    // ---- submitWebApiKey: the clearing pin (WP 4b.7 brief) ------------------

    @Test
    fun `MUTATION PIN -- nextFieldValue is empty on SUCCESS`() {
        var persisted: String? = null
        val result = submitWebApiKey(
            rawInput = VALID_KEY,
            invalidFormatError = INVALID_FORMAT_ERROR,
            genericError = { it.message ?: "error" },
            persist = { persisted = it },
        )
        assertTrue(result.ok)
        assertEquals("", result.nextFieldValue)
        assertEquals(VALID_KEY, persisted)
    }

    @Test
    fun `MUTATION PIN -- nextFieldValue is empty on an INVALID FORMAT, and persist is never called`() {
        var persistCalls = 0
        val result = submitWebApiKey(
            rawInput = "not-a-key",
            invalidFormatError = INVALID_FORMAT_ERROR,
            genericError = { "error" },
            persist = { persistCalls++ },
        )
        assertFalse(result.ok)
        assertEquals(INVALID_FORMAT_ERROR, result.error)
        assertEquals("", result.nextFieldValue)
        assertEquals(0, persistCalls)
    }

    @Test
    fun `MUTATION PIN -- nextFieldValue is empty even when persist THROWS`() {
        val result = submitWebApiKey(
            rawInput = VALID_KEY,
            invalidFormatError = INVALID_FORMAT_ERROR,
            genericError = { "boom: ${it.message}" },
            persist = { throw IllegalStateException("disk full") },
        )
        assertFalse(result.ok)
        assertEquals("boom: disk full", result.error)
        assertEquals("", result.nextFieldValue)
    }

    @Test
    fun `whitespace around the raw input is trimmed before validation and persistence`() {
        var persisted: String? = null
        val result = submitWebApiKey(
            rawInput = "  $VALID_KEY  ",
            invalidFormatError = INVALID_FORMAT_ERROR,
            genericError = { "error" },
            persist = { persisted = it },
        )
        assertTrue(result.ok)
        assertEquals(VALID_KEY, persisted)
    }

    @Test
    fun `a rejected format never calls persist with the raw value, whatever it looked like`() {
        val canary = "CAFEBABECAFEBABECAFEBABECAFEBAB" // 31 chars -- invalid length
        var persisted: String? = null
        submitWebApiKey(
            rawInput = canary,
            invalidFormatError = INVALID_FORMAT_ERROR,
            genericError = { "error" },
            persist = { persisted = it },
        )
        assertNull(persisted)
    }
}
