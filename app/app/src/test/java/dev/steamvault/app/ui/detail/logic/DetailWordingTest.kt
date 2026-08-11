package dev.steamvault.app.ui.detail.logic

import org.junit.Assert.assertEquals
import org.junit.Test

/** WP 4b.6 review fix S4: pins the branch that qualifies "Confirmed current
 * at X" when the cache behind that confirmation has since been cleared. */
class DetailWordingTest {

    @Test
    fun `never confirmed -- last_manifest_check null regardless of last_prefill_at`() {
        assertEquals(ConfirmedCurrentWording.NEVER_CONFIRMED, confirmedCurrentWording(null, null))
        assertEquals(
            ConfirmedCurrentWording.NEVER_CONFIRMED,
            confirmedCurrentWording("2026-08-01T00:00:00Z", null),
        )
    }

    @Test
    fun `normal case -- both timestamps present`() {
        assertEquals(
            ConfirmedCurrentWording.CONFIRMED,
            confirmedCurrentWording("2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z"),
        )
    }

    @Test
    fun `THE FIX -- confirmed but last_prefill_at null (post-deletion shape) is qualified, not bare`() {
        // api/README.md: DELETE /v1/cache/{appid} clears last_prefill_at
        // unconditionally but deliberately leaves last_manifest_check --
        // this is the exact resulting row shape.
        assertEquals(
            ConfirmedCurrentWording.CONFIRMED_BEFORE_CACHE_CLEARED,
            confirmedCurrentWording(null, "2026-08-02T00:00:00Z"),
        )
    }
}
