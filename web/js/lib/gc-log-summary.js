/**
 * Best-effort headline extraction from a GC job's `log_excerpt` (WP 4a.4).
 *
 * Ported from the Android sibling's `ui/detail/logic/GcLogSummary.kt`
 * (WP 4b.6) — same regexes, same scoping rule, same fixtures — so the two
 * frontends read the exact same server log text the exact same way.
 *
 * There is NO structured GC plan anywhere in the API contract — `JobDetail`
 * carries only `status` plus a free-text `log_excerpt` (api/README.md
 * "Garbage collection": the totals line is the only per-run summary
 * vault-api produces, e.g. `GC totals (DRY RUN): ... would_delete=0
 * (0 bytes) ...` / `GC totals (EXECUTED): chunks_removed=2 bytes_freed=700
 * ... total_bytes_freed=1934 ...`). `parseGcLogSummary` regex-scrapes that
 * one totals line for a friendly headline; callers MUST still show the raw
 * `log_excerpt` text alongside it (never replace it) so a log format this
 * parser doesn't recognize is never a lie by omission — a `null` result
 * here means "no headline number today", not "no plan".
 *
 * **Scoped to the totals line, not the whole excerpt.** A multi-depot run's
 * PER-DEPOT lines can carry their own `held_back=N (M bytes)` (one per
 * depot, api/README.md's dry-run example log), which is a different number
 * from the RUN'S total — searching the whole string for the first match
 * would silently report a random depot's number instead of the total's.
 * `parseGcLogSummary` therefore locates the `GC totals (...)` header first
 * and only scans the text AFTER it (see the "distinguishes the totals
 * line's held_back from an earlier per-depot held_back" test below — the
 * mutation-worthy case this guards).
 *
 * Fixtures are the EXACT log text api/README.md quotes for a dry run and an
 * executed run (docs/LEARNINGS.md "Verify empirically over believing docs"
 * — the same reasoning the Android port's kdoc records), identical to the
 * ones `GcLogSummaryTest.kt` pins.
 *
 * Pure — no DOM, no fetch. Covered in web/tests/gc-log-summary.test.js.
 */

export const GC_MODE = Object.freeze({ DRY_RUN: "dry-run", EXECUTE: "execute" });

const EXECUTED_HEADER = /GC totals \(EXECUTED\)/;
const DRY_RUN_HEADER = /GC totals \(DRY RUN\)/;

// `\b` (word boundary) rather than a bare substring match: "_" is a `\w`
// character in regex, so there is NO boundary inside "total_bytes_freed" or
// "dedupe_bytes_freed" between the preceding letter/underscore and "bytes"
// — `\bbytes_freed=` matches only a STANDALONE "bytes_freed=", never as a
// suffix of one of those two longer keys that also happen to be present on
// the exact same totals line (ported from the Kotlin sibling's comment,
// same reasoning, same regex).
const BYTES_FREED = /\bbytes_freed=(\d+)/;
const CHUNKS_REMOVED = /\bchunks_removed=(\d+)/;
const TOTAL_BYTES_FREED = /\btotal_bytes_freed=(\d+)/;
const WOULD_DELETE = /\bwould_delete=(\d+)\s*\((\d+)\s*bytes\)/;
const HELD_BACK = /\bheld_back=(\d+)\s*\((\d+)\s*bytes\)/;

/**
 * @param {string | null | undefined} logExcerpt
 * @returns {{
 *   mode: string,
 *   wouldDeleteCount: number | null,
 *   wouldDeleteBytes: number | null,
 *   chunksRemoved: number | null,
 *   bytesFreed: number | null,
 *   totalBytesFreed: number | null,
 *   heldBackCount: number | null,
 *   heldBackBytes: number | null,
 * } | null}
 */
export function parseGcLogSummary(logExcerpt) {
  if (typeof logExcerpt !== "string" || !logExcerpt.trim()) return null;

  const executedMatch = EXECUTED_HEADER.exec(logExcerpt);
  const dryRunMatch = DRY_RUN_HEADER.exec(logExcerpt);

  let mode;
  let totalsOnward;
  if (executedMatch) {
    mode = GC_MODE.EXECUTE;
    totalsOnward = logExcerpt.slice(executedMatch.index + executedMatch[0].length);
  } else if (dryRunMatch) {
    mode = GC_MODE.DRY_RUN;
    totalsOnward = logExcerpt.slice(dryRunMatch.index + dryRunMatch[0].length);
  } else {
    return null;
  }

  const wouldDelete = WOULD_DELETE.exec(totalsOnward);
  const heldBack = HELD_BACK.exec(totalsOnward);
  const chunksRemoved = CHUNKS_REMOVED.exec(totalsOnward);
  const bytesFreed = BYTES_FREED.exec(totalsOnward);
  const totalBytesFreed = TOTAL_BYTES_FREED.exec(totalsOnward);

  return {
    mode,
    wouldDeleteCount: wouldDelete ? Number(wouldDelete[1]) : null,
    wouldDeleteBytes: wouldDelete ? Number(wouldDelete[2]) : null,
    chunksRemoved: chunksRemoved ? Number(chunksRemoved[1]) : null,
    bytesFreed: bytesFreed ? Number(bytesFreed[1]) : null,
    totalBytesFreed: totalBytesFreed ? Number(totalBytesFreed[1]) : null,
    heldBackCount: heldBack ? Number(heldBack[1]) : null,
    heldBackBytes: heldBack ? Number(heldBack[2]) : null,
  };
}
