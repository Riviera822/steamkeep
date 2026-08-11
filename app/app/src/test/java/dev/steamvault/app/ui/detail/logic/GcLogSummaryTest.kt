package dev.steamvault.app.ui.detail.logic

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Fixtures are the EXACT log text api/README.md quotes for "What you
 * see" (dry run) and the executed-run example under "GC totals" --
 * docs/LEARNINGS.md "Verify empirically over believing docs": this is the
 * one place in the WP that could not run the real server, so the literal
 * doc text is the closest available ground truth.
 */
class GcLogSummaryTest {

    private val dryRunLog = """
        [vault-api] GC for app 440: DRY RUN.
          depot 441 planned: orphans=2 (700 bytes) held_back=2 (700 bytes) kept=2 ...
            ~ 00..b1: stored 0.3 days ago, grace window is 14 days
        [vault-api] GC totals (DRY RUN): orphans=2 (700 bytes) held_back=2 (700 bytes)
          would_delete=0 (0 bytes) reclaimable_dedupe_bytes=0 planned_depots=[441]. ...
    """.trimIndent()

    private val executedLog = """
        [vault-api] GC for app 440: EXECUTE.
          depot 441 planned: orphans=2 (700 bytes) -> removed=2 (700 bytes) already_gone=0 ...
          depot 900 skipped_no_manifest: ADR-0007 readiness gate: no current manifest ...
        [vault-api] GC totals (EXECUTED): chunks_removed=2 bytes_freed=700 already_gone=0
          dedupe_removed=2 dedupe_bytes_freed=1234 total_bytes_freed=1934 problems=0
          declined=0 held_back=0 (0 bytes) depots_touched=[441]
          needs_force_set_for=[440, 730]
    """.trimIndent()

    @Test
    fun `null or blank excerpt parses to null`() {
        assertNull(parseGcLogSummary(null))
        assertNull(parseGcLogSummary(""))
        assertNull(parseGcLogSummary("   "))
    }

    @Test
    fun `an excerpt with no GC totals line parses to null -- never fabricates a headline`() {
        assertNull(parseGcLogSummary("some unrelated prefill log with no GC markers at all"))
    }

    @Test
    fun `dry run -- would_delete and held_back read from the TOTALS line`() {
        val summary = parseGcLogSummary(dryRunLog)!!
        assertEquals(GcMode.DRY_RUN, summary.mode)
        assertEquals(0, summary.wouldDeleteCount)
        assertEquals(0L, summary.wouldDeleteBytes)
        assertEquals(2, summary.heldBackCount)
        assertEquals(700L, summary.heldBackBytes)
        assertNull(summary.chunksRemoved)
        assertNull(summary.totalBytesFreed)
    }

    @Test
    fun `MUTATION TARGET -- distinguishes the totals line's held_back from an earlier per-depot held_back`() {
        // Same key name appears twice (once per-depot, once in the totals);
        // a naive "first match anywhere in the string" parse would report
        // the PER-DEPOT number instead of the run's actual total.
        val log = """
            [vault-api] GC for app 440: DRY RUN.
              depot 441 planned: orphans=9 (9000 bytes) held_back=1 (300 bytes) kept=1 ...
            [vault-api] GC totals (DRY RUN): orphans=9 (9000 bytes) held_back=2 (700 bytes)
              would_delete=0 (0 bytes) reclaimable_dedupe_bytes=0 planned_depots=[441]. ...
        """.trimIndent()
        val summary = parseGcLogSummary(log)!!
        assertEquals(2, summary.heldBackCount)
        assertEquals(700L, summary.heldBackBytes)
    }

    @Test
    fun `executed -- chunks_removed, bytes_freed and total_bytes_freed read correctly despite the dedupe_ and total_ prefixed lookalike keys`() {
        val summary = parseGcLogSummary(executedLog)!!
        assertEquals(GcMode.EXECUTE, summary.mode)
        assertEquals(2, summary.chunksRemoved)
        // MUTATION TARGET: without the \b word-boundary guard, a bare
        // "bytes_freed=" regex would match inside "dedupe_bytes_freed=1234"
        // or "total_bytes_freed=1934" too -- this pins the STANDALONE
        // bytes_freed=700, not either lookalike.
        assertEquals(700L, summary.bytesFreed)
        assertEquals(1934L, summary.totalBytesFreed)
        assertEquals(0, summary.heldBackCount)
        assertEquals(0L, summary.heldBackBytes)
        assertNull(summary.wouldDeleteCount)
    }

    @Test
    fun `NIT fix -- bytes_freed resolves correctly regardless of key ORDER, not just because it happens to come first`() {
        // The real server always emits chunks_removed/bytes_freed before
        // the dedupe_/total_ prefixed keys (see `executedLog` above), so
        // the previous test alone could not distinguish "the \b guard
        // works" from "bytes_freed just happens to be the first match in
        // this string". This fixture reorders the keys (total_bytes_freed
        // and dedupe_bytes_freed both appear BEFORE the standalone
        // bytes_freed) to prove the word-boundary guard, not key order, is
        // what is actually load-bearing.
        val reorderedLog = "[vault-api] GC totals (EXECUTED): total_bytes_freed=1934 " +
            "dedupe_bytes_freed=1234 bytes_freed=700 chunks_removed=2"
        val summary = parseGcLogSummary(reorderedLog)!!
        assertEquals(700L, summary.bytesFreed)
        assertEquals(1934L, summary.totalBytesFreed)
        assertEquals(2, summary.chunksRemoved)
    }

    @Test
    fun `a nonzero would_delete count reports the exact bytes`() {
        val log = """
            [vault-api] GC totals (DRY RUN): orphans=5 (12345 bytes) held_back=0 (0 bytes)
              would_delete=5 (12345 bytes) reclaimable_dedupe_bytes=0 planned_depots=[441].
        """.trimIndent()
        val summary = parseGcLogSummary(log)!!
        assertEquals(5, summary.wouldDeleteCount)
        assertEquals(12345L, summary.wouldDeleteBytes)
    }
}
