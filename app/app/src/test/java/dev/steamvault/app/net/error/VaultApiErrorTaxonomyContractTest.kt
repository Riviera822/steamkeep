package dev.steamvault.app.net.error

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Literal cross-frontend wire-name contract (WP 4b.2 brief), same
 * technique `StatusIconCrossFrontendContractTest` (WP 4b.1) uses for
 * `StatusKind`: every kind name below is HAND-TRANSCRIBED from
 * `web/js/errors.js`'s `ERROR_KINDS`, never derived from
 * [VaultApiError] itself — a rename here or there (e.g. "not_found" ->
 * "notfound") would otherwise stay internally consistent with a derived
 * test and still pass. Reading web/js/errors.js at test time is out of
 * scope for this app/-only WP (same reasoning app/README.md documents for
 * the status-icon contract) — the literals below are the intended
 * manual-sync point if the web taxonomy ever changes.
 */
class VaultApiErrorTaxonomyContractTest {

    /** web/js/errors.js ERROR_KINDS values, spelled out. */
    private val expectedKinds = setOf(
        "network",
        "auth",
        "not_found",
        "validation",
        "server",
        "unknown",
    )

    @Test
    fun `taxonomy has exactly the six literal web kind names`() {
        assertEquals(6, expectedKinds.size)
        val actual = setOf(
            VaultApiError.KIND_NETWORK,
            VaultApiError.KIND_AUTH,
            VaultApiError.KIND_NOT_FOUND,
            VaultApiError.KIND_VALIDATION,
            VaultApiError.KIND_SERVER,
            VaultApiError.KIND_UNKNOWN,
        )
        assertEquals(expectedKinds, actual)
    }

    @Test
    fun `every subclass reports the kind constant matching its literal name`() {
        assertEquals("network", VaultApiError.Network("x").kind)
        assertEquals("auth", VaultApiError.Auth("x", 401).kind)
        assertEquals("not_found", VaultApiError.NotFound("x", 404).kind)
        assertEquals("validation", VaultApiError.Validation("x", 422).kind)
        assertEquals("server", VaultApiError.Server("x", 500).kind)
        assertEquals("unknown", VaultApiError.Unknown("x").kind)
    }

    // classifyHttpStatus: web/js/errors.js's documented mapping. Both
    // directions of every boundary are pinned (docs/LEARNINGS.md "Testing
    // discipline": a fail-closed/fail-open boundary needs the DEFAULT
    // direction pinned, not just the happy path).

    @Test
    fun `401 maps to auth`() {
        assertEquals(VaultApiError.KIND_AUTH, VaultApiError.classifyHttpStatus(401))
    }

    @Test
    fun `404 maps to not_found`() {
        assertEquals(VaultApiError.KIND_NOT_FOUND, VaultApiError.classifyHttpStatus(404))
    }

    @Test
    fun `409 folds into validation, matching web -- no dedicated conflict kind`() {
        assertEquals(VaultApiError.KIND_VALIDATION, VaultApiError.classifyHttpStatus(409))
    }

    @Test
    fun `422 maps to validation`() {
        assertEquals(VaultApiError.KIND_VALIDATION, VaultApiError.classifyHttpStatus(422))
    }

    @Test
    fun `every 5xx maps to server`() {
        for (status in listOf(500, 502, 503, 599)) {
            assertEquals(VaultApiError.KIND_SERVER, VaultApiError.classifyHttpStatus(status))
        }
    }

    @Test
    fun `400 is the validation side of the 400-vs-401 and 400-vs-500 boundaries`() {
        assertEquals(VaultApiError.KIND_VALIDATION, VaultApiError.classifyHttpStatus(400))
    }

    @Test
    fun `499 is still validation, not server -- the 500 boundary is exclusive on the low side`() {
        assertEquals(VaultApiError.KIND_VALIDATION, VaultApiError.classifyHttpStatus(499))
    }

    @Test
    fun `a status below 400 falls back to unknown, never a 2xx-shaped kind`() {
        assertEquals(VaultApiError.KIND_UNKNOWN, VaultApiError.classifyHttpStatus(200))
        assertEquals(VaultApiError.KIND_UNKNOWN, VaultApiError.classifyHttpStatus(0))
    }

    @Test
    fun `forHttpStatus builds the subclass matching classifyHttpStatus, with status and detail carried through`() {
        val error = VaultApiError.forHttpStatus(404, "GET", "/v1/games/999999", "Unknown appid 999999")
        assertEquals(true, error is VaultApiError.NotFound)
        assertEquals("not_found", error.kind)
        assertEquals(404, error.status)
        assertEquals("Unknown appid 999999", error.detail)
    }
}
