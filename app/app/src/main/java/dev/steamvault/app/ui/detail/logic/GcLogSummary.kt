package dev.steamvault.app.ui.detail.logic

/**
 * Best-effort headline extraction from a GC job's `log_excerpt` (WP 4b.6
 * brief: "show the plan from the job result... JobDetail carries the
 * outcome"). There is NO structured GC plan anywhere in the API contract --
 * `JobDetail` carries only `status` plus a free-text `log_excerpt`
 * (api/README.md "Garbage collection": the totals line is the only
 * per-run summary vault-api produces, e.g.
 * `GC totals (DRY RUN): ... would_delete=0 (0 bytes) ...` /
 * `GC totals (EXECUTED): chunks_removed=2 bytes_freed=700 ...
 * total_bytes_freed=1934 ...`). [parseGcLogSummary] regex-scrapes that one
 * totals line for a friendly headline; callers MUST still show the raw
 * `log_excerpt` text alongside it (never replace it) so a log format this
 * parser doesn't recognize is never a lie by omission -- a `null` result
 * here means "no headline number today", not "no plan".
 *
 * **Scoped to the totals line, not the whole excerpt.** A multi-depot run's
 * PER-DEPOT lines can carry their own `held_back=N (M bytes)` (one per
 * depot, api/README.md's dry-run example log), which is a different number
 * from the RUN'S total -- searching the whole string for the first match
 * would silently report a random depot's number instead of the total's.
 * [parseGcLogSummary] therefore locates the `GC totals (...)` header first
 * and only scans the text AFTER it (`GcLogSummaryTest`'s
 * "distinguishes the totals line's held_back from an earlier per-depot
 * held_back" pin is the mutation-worthy case this guards).
 *
 * Fixtures in `GcLogSummaryTest` are the EXACT strings api/README.md quotes
 * for a dry run and an executed run (docs/LEARNINGS.md "Verify empirically
 * over believing docs" -- the real server was not reachable from this
 * environment, so the literal doc text is the closest available ground
 * truth).
 */
enum class GcMode { DRY_RUN, EXECUTE }

data class GcLogSummary(
    val mode: GcMode,
    /** Dry-run only (`would_delete=N (M bytes)` in the totals line). */
    val wouldDeleteCount: Int? = null,
    val wouldDeleteBytes: Long? = null,
    /** Execute only. */
    val chunksRemoved: Int? = null,
    val bytesFreed: Long? = null,
    val totalBytesFreed: Long? = null,
    /** Both modes -- the grace window (WP 3.8b) applies to a dry run too. */
    val heldBackCount: Int? = null,
    val heldBackBytes: Long? = null,
)

private val EXECUTED_HEADER = Regex("""GC totals \(EXECUTED\)""")
private val DRY_RUN_HEADER = Regex("""GC totals \(DRY RUN\)""")

// `\b` (word boundary) rather than a bare substring match: "_" is a `\w`
// character in regex, so there is NO boundary inside "total_bytes_freed" or
// "dedupe_bytes_freed" between the preceding letter/underscore and "bytes" --
// `\bbytes_freed=` matches only a STANDALONE "bytes_freed=", never as a
// suffix of one of those two longer keys that also happen to be present on
// the exact same totals line.
private val BYTES_FREED = Regex("""\bbytes_freed=(\d+)""")
private val CHUNKS_REMOVED = Regex("""\bchunks_removed=(\d+)""")
private val TOTAL_BYTES_FREED = Regex("""\btotal_bytes_freed=(\d+)""")
private val WOULD_DELETE = Regex("""\bwould_delete=(\d+)\s*\((\d+)\s*bytes\)""")
private val HELD_BACK = Regex("""\bheld_back=(\d+)\s*\((\d+)\s*bytes\)""")

fun parseGcLogSummary(logExcerpt: String?): GcLogSummary? {
    if (logExcerpt.isNullOrBlank()) return null

    val executedMatch = EXECUTED_HEADER.find(logExcerpt)
    val dryRunMatch = DRY_RUN_HEADER.find(logExcerpt)
    val (mode, totalsOnward) = when {
        executedMatch != null -> GcMode.EXECUTE to logExcerpt.substring(executedMatch.range.last + 1)
        dryRunMatch != null -> GcMode.DRY_RUN to logExcerpt.substring(dryRunMatch.range.last + 1)
        else -> return null
    }

    val wouldDelete = WOULD_DELETE.find(totalsOnward)
    val heldBack = HELD_BACK.find(totalsOnward)
    return GcLogSummary(
        mode = mode,
        wouldDeleteCount = wouldDelete?.groupValues?.get(1)?.toIntOrNull(),
        wouldDeleteBytes = wouldDelete?.groupValues?.get(2)?.toLongOrNull(),
        chunksRemoved = CHUNKS_REMOVED.find(totalsOnward)?.groupValues?.get(1)?.toIntOrNull(),
        bytesFreed = BYTES_FREED.find(totalsOnward)?.groupValues?.get(1)?.toLongOrNull(),
        totalBytesFreed = TOTAL_BYTES_FREED.find(totalsOnward)?.groupValues?.get(1)?.toLongOrNull(),
        heldBackCount = heldBack?.groupValues?.get(1)?.toIntOrNull(),
        heldBackBytes = heldBack?.groupValues?.get(2)?.toLongOrNull(),
    )
}
