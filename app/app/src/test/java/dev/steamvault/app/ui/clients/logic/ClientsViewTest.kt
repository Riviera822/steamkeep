package dev.steamvault.app.ui.clients.logic

import dev.steamvault.app.net.model.ClientOut
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Ports `web/tests/clients-view.test.js`'s `partitionClients`/
 * `hitRatePercent` cases onto [ClientOut], plus the WP 4b.10 brief's
 * [ClientRowModel] stability claim -- see that class's kdoc for why this
 * is the Compose-platform equivalent of `clients-render-plan.js`'s full/
 * patch/rebuild verdict, same class of proof
 * `ui/downloads/logic/JobCardModelTest.kt` establishes for [JobCardModel]-
 * shaped stability guarantees.
 */
class ClientsViewTest {

    private fun client(
        clientId: String = "workshop-pc",
        appCount: Int? = 10,
        sourceAddrs: List<String> = listOf("10.10.0.21"),
        cacheHits: Int = 5,
        cacheMisses: Int = 1,
        bytesServed: Long = 1234L,
        bypassSuspected: Boolean = false,
    ) = ClientOut(
        client_id = clientId,
        first_seen = "2026-08-01T00:00:00Z",
        last_reported_at = "2026-08-10T00:00:00Z",
        app_count = appCount,
        source_addrs = sourceAddrs,
        cache_hits = cacheHits,
        cache_misses = cacheMisses,
        bytes_served = bytesServed,
        last_seen_in_cache_log = "2026-08-10T00:00:00Z",
        bypass_suspected = bypassSuspected,
    )

    // -------------------------------------------------------------
    // partitionClients
    // -------------------------------------------------------------

    @Test
    fun `partitionClients splits by bypass_suspected, preserving order within each bucket`() {
        val a = client(clientId = "a", bypassSuspected = true)
        val b = client(clientId = "b", bypassSuspected = false)
        val c = client(clientId = "c", bypassSuspected = true)
        val partition = partitionClients(listOf(a, b, c))
        assertEquals(listOf("a", "c"), partition.bypassing.map { it.client_id })
        assertEquals(listOf("b"), partition.healthy.map { it.client_id })
    }

    @Test
    fun `partitionClients on an empty list produces two empty buckets`() {
        val partition = partitionClients(emptyList())
        assertTrue(partition.bypassing.isEmpty())
        assertTrue(partition.healthy.isEmpty())
    }

    // -------------------------------------------------------------
    // hitRatePercent
    // -------------------------------------------------------------

    @Test
    fun `hitRatePercent rounds hits over hits-plus-misses to a whole-number percentage`() {
        assertEquals(96, hitRatePercent(client(cacheHits = 96, cacheMisses = 4)))
        assertEquals(33, hitRatePercent(client(cacheHits = 1, cacheMisses = 2))) // 33.33.. -> 33
        assertEquals(67, hitRatePercent(client(cacheHits = 2, cacheMisses = 1))) // 66.67 -> 67
    }

    @Test
    fun `hitRatePercent is null, never a fabricated 0 percent, for zero total requests`() {
        assertNull(hitRatePercent(client(cacheHits = 0, cacheMisses = 0)))
    }

    @Test
    fun `hitRatePercent treats a negative counter as 0, not a negative or crashing rate`() {
        // -3 hits / 5 misses -> 0 hits out of a 5 total -> 0%, mirrors
        // clients-view.js's safeCount guard (Kotlin's non-nullable Int has
        // no NaN case to mirror).
        assertEquals(0, hitRatePercent(client(cacheHits = -3, cacheMisses = 5)))
    }

    // -------------------------------------------------------------
    // ClientRowModel stability (WP 4b.10 brief)
    // -------------------------------------------------------------

    @Test
    fun `two ticks of a genuinely-unchanged client produce an EQUAL model from DISTINCT instances`() {
        val tick1 = buildClientRowModel(client())
        val tick2 = buildClientRowModel(client())
        assertNotSame(tick1, tick2)
        assertEquals(tick1, tick2)
    }

    @Test
    fun `a stats-only diff changes ONLY the stats field`() {
        val before = buildClientRowModel(client(cacheHits = 5, cacheMisses = 1, bytesServed = 1234L))
        val after = buildClientRowModel(client(cacheHits = 96, cacheMisses = 4, bytesServed = 999_999L))

        assertTrue(before != after) // MUTATION TARGET: a real diff must be observed at all
        assertEquals(before.copy(stats = after.stats), after) // ... but nowhere else.
        assertEquals(before.clientId, after.clientId)
        assertEquals(before.bypassSuspected, after.bypassSuspected)
        assertEquals(before.addresses, after.addresses)
    }

    @Test
    fun `a bypass_suspected flip changes ONLY that field, never clientId or addresses`() {
        val before = buildClientRowModel(client(bypassSuspected = false))
        val after = buildClientRowModel(client(bypassSuspected = true))

        assertTrue(before != after) // MUTATION TARGET
        assertEquals(before.copy(bypassSuspected = after.bypassSuspected), after)
        assertEquals(before.clientId, after.clientId)
        assertEquals(before.addresses, after.addresses)
    }

    @Test
    fun `buildClientRowModel carries app_count, bytes_served and the computed hit rate into stats`() {
        val model = buildClientRowModel(client(appCount = 61, cacheHits = 96, cacheMisses = 4, bytesServed = 44_000_000_000L))
        assertEquals(61, model.stats.gamesReported)
        assertEquals(44_000_000_000L, model.stats.bytesServed)
        assertEquals(96, model.stats.hitRatePercent)
    }

    @Test
    fun `buildClientRowModel reports app_count null as null, never a fabricated zero`() {
        val model = buildClientRowModel(client(appCount = null))
        assertNull(model.stats.gamesReported)
    }
}
