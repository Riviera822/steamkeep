package dev.steamvault.app.ui.downloads.logic

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

/** Kotlin port of `web/tests/format.test.js`'s `formatTimestamp` cases
 * against [formatTimestamp] -- "nothing honest to print" never fabricates a
 * time for null/blank/unparseable input. */
class FormatTest {

    @Test
    fun `null input renders the dash placeholder`() {
        assertEquals("—", formatTimestamp(null))
    }

    @Test
    fun `blank input renders the dash placeholder`() {
        assertEquals("—", formatTimestamp("  "))
    }

    @Test
    fun `unparseable input renders the dash placeholder, never throws`() {
        assertEquals("—", formatTimestamp("not-a-timestamp"))
    }

    @Test
    fun `a real vault-api timestamp renders a non-placeholder string`() {
        // api/vault_api/jobs.py::TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
        val result = formatTimestamp("2026-08-01T12:34:56Z")
        assertNotEquals("—", result)
    }
}
