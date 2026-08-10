/**
 * Games-tick render decision (WP 4a.3 review fix — blocker B1).
 *
 * The round-7 mockup rule ("rebuild DOM only when a card's STATE changes...
 * while a job is merely progressing, patch the volatile values in place...
 * touch no animated node", docs/design/vault-app-mockup-NOTES.md) is
 * "explicitly not a mockup-only concern" (same NOTES section) — it applies
 * to the `GET /v1/games` poll too, not just the `GET /v1/jobs` one
 * `game-status.js`'s `isJobStateTransition` already guards. The 15 s games
 * poll drifts `size_bytes` upward for a running download (the server's own
 * size cache updates independently of the job poll) — a naive "any updated
 * row -> rebuild its card" would recreate the animated status-icon `<svg>`
 * subtree on every such tick, restarting the CSS animation exactly like
 * the mockup's original bug.
 *
 * This module is the pure DECISION only: given a `diffByKey` result for
 * `GET /v1/games` plus the STRUCTURAL key currently painted on each visible
 * card (`data-dk`, written by `components/game-card.js`'s `buildCard`/
 * `cardStructuralKey`) and a function to compute a game's CURRENT
 * structural key, decide which appids can be patched in place (structural
 * key unchanged — only volatile text, e.g. a GB size, may have moved) and
 * which must be rebuilt (structural key changed — a real status
 * transition, so the animated node legitimately needs to change shape).
 * No DOM access here at all — `views/library.js` owns reading `data-dk`
 * from the live grid and calling `game-card.js`'s `patchCardVolatile`/
 * `buildCard` per this plan's verdict.
 */

/**
 * @param {{isFirst: boolean, added: object[], updated: {prev: object, curr: object}[], removed: object[]}} diff
 *   `diffByKey`'s result for the `GET /v1/games` snapshot (keyed by appid).
 * @param {Map<number, string>} currentCardKeys appid -> the `data-dk` value
 *   currently painted on that appid's card, for every card ON SCREEN right
 *   now (an appid missing from this map is not currently rendered — e.g.
 *   filtered out by search/chip — and is therefore never patched/rebuilt
 *   by this plan; nothing to touch).
 * @param {(game: object) => string} computeKey the CURRENT structural key
 *   for a game row (`components/game-card.js`'s `cardStructuralKey`,
 *   supplied by the caller so this module stays independent of
 *   `game-status.js`'s live-job bookkeeping).
 * @returns {{full: boolean, patch: number[], rebuild: number[]}}
 *   `full: true` means "fall back to a full re-render", and `patch`/
 *   `rebuild` are meaningless in that case. Otherwise `patch` and
 *   `rebuild` are disjoint appid lists.
 */
export function planGamesUpdate(diff, currentCardKeys, computeKey) {
  if (!diff || diff.isFirst) {
    // No prior snapshot to diff against, or the very first paint — there
    // is nothing on screen yet to patch/rebuild selectively.
    return { full: true, patch: [], rebuild: [] };
  }
  if ((diff.added && diff.added.length) || (diff.removed && diff.removed.length)) {
    // A row entering or leaving the tracked set can change which cards the
    // active filter/search shows — grid MEMBERSHIP, not just a card's own
    // content. Falling back to a full render here is correct and, per the
    // WP brief, an accepted simplification (added/removed rows are rare —
    // apps are not commonly removed from `GET /v1/games`, and a brand-new
    // app has no animated card yet to disturb).
    return { full: true, patch: [], rebuild: [] };
  }

  const patch = [];
  const rebuild = [];
  for (const { curr } of diff.updated || []) {
    if (!currentCardKeys.has(curr.appid)) continue; // not on screen right now
    const newKey = computeKey(curr);
    const paintedKey = currentCardKeys.get(curr.appid);
    if (newKey === paintedKey) {
      patch.push(curr.appid);
    } else {
      rebuild.push(curr.appid);
    }
  }
  return { full: false, patch, rebuild };
}
