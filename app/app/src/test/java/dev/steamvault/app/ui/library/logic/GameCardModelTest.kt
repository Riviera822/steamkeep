package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.ui.status.StatusKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * See `GameCardModel.kt`'s kdoc for the full mechanism this pins: Compose
 * skips recomposing a composable whose stable parameters equal the previous
 * composition's. This test proves the JVM-provable half of that
 * precondition -- that [buildGameCardModel] is a pure function producing an
 * `equals()`-equal (not merely reference-equal) result when fed two
 * DISTINCT-INSTANCE but functionally-identical `GameSummary`/`JobSummary`
 * values, the way two successive `GET /v1/games` / `GET /v1/jobs` polls
 * would for a genuinely unchanged running job.
 */
class GameCardModelTest {

    private fun runningGame(appid: Int = 42) = GameSummary(
        appid = appid,
        name = "Nebula Drift",
        status = "idle",
        last_prefill_at = null,
        last_manifest_check = null,
        depot_count = 0,
        size_bytes = null,
        needs_force = true,
    )

    private fun runningJob(appid: Int = 42) = JobSummary(
        id = 7,
        appid = appid,
        type = "prefill",
        status = "running",
        created_at = "2026-08-01T00:00:00Z",
        started_at = "2026-08-01T00:00:05Z",
    )

    @Test
    fun `two ticks of a genuinely-unchanged running job produce an EQUAL model from DISTINCT instances`() {
        // Two separately-constructed object graphs, simulating two distinct
        // HTTP responses that happen to describe the same state.
        val tick1 = buildGameCardModel(runningGame(), runningJob(), selected = false, selecting = false)
        val tick2 = buildGameCardModel(runningGame(), runningJob(), selected = false, selecting = false)

        assertNotSame(tick1, tick2) // different object instances ...
        assertEquals(tick1, tick2) // ... but structurally equal: Compose would SKIP.
        assertEquals(StatusKind.RUNNING, tick1.kind)
    }

    @Test
    fun `a genuine status transition (running to paused) produces a DIFFERENT model`() {
        val running = buildGameCardModel(runningGame(), runningJob(), selected = false, selecting = false)
        val paused = buildGameCardModel(
            runningGame(),
            runningJob().copy(status = "paused", paused_at = "2026-08-01T00:10:00Z"),
            selected = false,
            selecting = false,
        )

        assertTrue(running != paused)
        assertEquals(StatusKind.RUNNING, running.kind)
        assertEquals(StatusKind.PAUSED, paused.kind)
    }

    @Test
    fun `toggling selection changes the model (selection is part of the render-diff key)`() {
        val unselected = buildGameCardModel(runningGame(), null, selected = false, selecting = true)
        val selected = buildGameCardModel(runningGame(), null, selected = true, selecting = true)
        assertTrue(unselected != selected)
    }

    @Test
    fun `a blank name falls back to a stable placeholder, not blank text`() {
        val blank = runningGame().copy(name = "   ")
        val model = buildGameCardModel(blank, null, selected = false, selecting = false)
        assertEquals("App 42", model.name)
    }

    @Test
    fun `coverUrl and fallback fields are derived purely from appid`() {
        val model = buildGameCardModel(runningGame(appid = 570), null, selected = false, selecting = false)
        assertEquals(coverArtUrl(570), model.coverUrl)
        assertEquals(fallbackHues(570), model.fallbackHues)
        assertEquals(fallbackPattern(570), model.fallbackPattern)
    }

    @Test
    fun `isKnownToVault is false for a synthetic Steam-only row`() {
        val synthetic = GameSummary(
            appid = 570,
            name = "Dota 2",
            status = "idle",
            last_prefill_at = null,
            last_manifest_check = null,
            depot_count = 0,
            size_bytes = null,
            needs_force = true,
        )
        val model = buildGameCardModel(synthetic, null, selected = false, selecting = false)
        assertEquals(false, model.isKnownToVault)
    }

    @Test
    fun `isKnownToVault is true once the vault has ever prefilled the app`() {
        val known = runningGame().copy(status = "done", last_prefill_at = "2026-08-01T00:00:00Z")
        val model = buildGameCardModel(known, null, selected = false, selecting = false)
        assertEquals(true, model.isKnownToVault)
    }
}
