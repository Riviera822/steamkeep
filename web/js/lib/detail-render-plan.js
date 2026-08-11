/**
 * Round-7 patch-in-place for the game detail sheet (WP 4a.4 brief: "the
 * sheet must not rebuild animated nodes on poll ticks — patch volatile
 * values, reuse the render-plan pattern"). Same rule as `lib/render-plan.js`
 * (Library grid) and `lib/downloads-render-plan.js` (Downloads job cards):
 * a background `GET /v1/games` / `GET /v1/jobs` tick that changes nothing
 * SHOWN as a shape (the header status icon, the job-control button set, a
 * depot's sharing tag) must never recreate that shape's DOM — only a tick
 * whose STRUCTURAL signature actually changed may rebuild it.
 *
 * The sheet's one animated node is the header `createStatusIcon` glyph
 * (`components/game-detail-sheet.js`'s `DetailHeader`-equivalent) — driven
 * by `dispKind` exactly like a Library card. Everything else volatile
 * (sizes, the last-download/confirmed-current lines, a co-owner's cached/
 * not-cached text) is plain text with nothing to animate, so patching it
 * unconditionally alongside the icon-preserving check is both correct and
 * simpler than tracking a separate signature per field.
 *
 * `buildDetailStructuralKey` folds the three things that change the SHAPE
 * of the sheet body into one comparable string:
 *   - `dispKind` — the header icon + word, and which of
 *     download/retry/nothing shows;
 *   - the tracked job's status (or `null`) — which of the
 *     pause/resume/cancel buttons show (`lib/detail-job.js`);
 *   - each depot's sharing tag (`lib/depot-presentation.js`) — a co-owner's
 *     cache state changing on a LIVE poll tick (a different game finishing
 *     its own download) can flip PROTECTED -> ORPHANED for a depot this
 *     sheet is showing, which changes the tag/note text shown, not just a
 *     number, so it counts as structural too.
 *
 * Two named mutation targets, same shape as the sibling render-plan
 * modules' test files: dropping `dispKind` from the key would let a
 * download-finishing tick leave the OLD status icon painted (rebuild
 * skipped) with a status word restarted only next full render; dropping
 * `depotTags` would let a co-owner's protection state change silently show
 * a stale sharing tag/note until something else forces a rebuild.
 *
 * Pure — no DOM. Covered in web/tests/detail-render-plan.test.js.
 */

/**
 * @param {{dispKind: string, trackedJobStatus: string|null, depotTags: string[]}} input
 * @returns {string} an opaque, comparable signature — equal in and equal
 *   out is the only contract; never parse this string.
 */
export function buildDetailStructuralKey({ dispKind, trackedJobStatus, depotTags }) {
  return JSON.stringify({
    k: dispKind,
    j: trackedJobStatus ?? null,
    d: Array.isArray(depotTags) ? depotTags : [],
  });
}
