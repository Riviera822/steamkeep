package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.ui.status.StatusKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

private fun vaultGame(appid: Int, status: String = "done", sizeBytes: Long? = 5_000_000_000): GameSummary =
    GameSummary(
        appid = appid,
        name = "Vault $appid",
        status = status,
        last_prefill_at = "2026-08-01T00:00:00Z",
        last_manifest_check = null,
        depot_count = 1,
        size_bytes = sizeBytes,
        needs_force = false,
    )

class LibraryMergeTest {

    @Test
    fun `null owned games list returns the vault list completely unchanged -- vault-only view stays functional`() {
        val vault = listOf(vaultGame(1), vaultGame(2))
        val merged = mergeLibrary(vault, null)
        assertSame(vault, merged)
    }

    @Test
    fun `empty owned games list also leaves the vault list unchanged`() {
        val vault = listOf(vaultGame(1))
        val merged = mergeLibrary(vault, emptyList())
        assertEquals(vault, merged)
    }

    @Test
    fun `an owned game already known to the vault is NOT duplicated -- vault data wins`() {
        val vault = listOf(vaultGame(440, status = "done"))
        val owned = listOf(OwnedGame(440, "Team Fortress", 10, "icon"))
        val merged = mergeLibrary(vault, owned)
        assertEquals(1, merged.size)
        assertEquals("Vault 440", merged.single().name) // vault's own name wins, not Steam's
    }

    @Test
    fun `an owned game unknown to the vault gets a synthetic not-cached row`() {
        val vault = listOf(vaultGame(440))
        val owned = listOf(OwnedGame(440, "TF2", 10, "icon"), OwnedGame(570, "Dota 2", 5, "icon2"))
        val merged = mergeLibrary(vault, owned)

        assertEquals(2, merged.size)
        val synthetic = merged.first { it.appid == 570 }
        assertEquals("Dota 2", synthetic.name)
        assertEquals(StatusKind.NONE, dispKind(synthetic, null))
        // Never invents a size or depot count for a game the vault has no
        // knowledge of (mockup rule: "Depots unknown until the first download").
        assertEquals(null, synthetic.size_bytes)
        assertEquals(0, synthetic.depot_count)
    }

    @Test
    fun `MUTATION PIN -- a synthetic row's needs_force is the honest false, never a fabricated server claim`() {
        // Review fix: needs_force is a SERVER-computed signal about a row
        // the server has actually seen (apps/depot_app_map); a game the
        // vault has never heard of has no server claim to represent, so
        // this must NOT default to true the way a genuinely never-filled
        // VAULT row's needs_force legitimately would.
        val synthetic = mergeLibrary(emptyList(), listOf(OwnedGame(570, "Dota 2", 0, ""))).single()
        assertEquals(false, synthetic.needs_force)
    }

    @Test
    fun `the full synthesized-row shape is pinned field for field`() {
        val synthetic = mergeLibrary(emptyList(), listOf(OwnedGame(570, "Dota 2", 5, "icon"))).single()
        assertEquals(
            GameSummary(
                appid = 570,
                name = "Dota 2",
                status = "idle",
                last_prefill_at = null,
                last_manifest_check = null,
                depot_count = 0,
                size_bytes = null,
                needs_force = false,
            ),
            synthetic,
        )
    }

    @Test
    fun `a vault game the Steam library no longer lists is kept as-is, un-merged`() {
        val vault = listOf(vaultGame(1))
        val owned = listOf(OwnedGame(2, "Other Game", 0, ""))
        val merged = mergeLibrary(vault, owned)
        assertTrue(merged.any { it.appid == 1 })
        assertEquals(2, merged.size) // vault's 1 + synthetic 2
    }

    @Test
    fun `duplicate appids in the owned list are de-duplicated`() {
        val owned = listOf(OwnedGame(440, "TF2", 1, ""), OwnedGame(440, "TF2 dup", 2, ""))
        val merged = mergeLibrary(emptyList(), owned)
        assertEquals(1, merged.size)
    }

    @Test
    fun `a blank Steam name falls back to null, not an empty string`() {
        val merged = mergeLibrary(emptyList(), listOf(OwnedGame(1, "", 0, "")))
        assertEquals(null, merged.single().name)
    }
}
