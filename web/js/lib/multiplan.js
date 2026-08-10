/**
 * Set-aware bulk-delete arithmetic (WP 4a.3).
 *
 * Ports the mockup's `multiPlan` (docs/design/vault-app-mockup.html,
 * docs/design/vault-app-mockup-NOTES.md round 6 "Bulk bar... Multi-delete
 * joins it, and the shared-depot arithmetic is genuinely set-aware"): the
 * plan is computed for the WHOLE selection at once, so co-owners INSIDE the
 * batch stop counting as reasons to keep a depot, and a depot mapped by two
 * selected games is only counted once (the bytes exist on disk once).
 *
 * Real-data adaptation: the mockup holds every game's full depot list (with
 * ownership) in one in-memory array and derives sharing by scanning it. The
 * shipped API doesn't expose that in one call — `GET /v1/games` (the list
 * the library already polls) has no per-depot breakdown, and
 * `GET /v1/games/{appid}` (per-game depot list + `shared: true/false`)
 * doesn't name WHICH other apps share a depot. This module is therefore
 * pure arithmetic over data the CALLER assembles from three real sources
 * (library.js's `fetchMultiPlanInputs`):
 *   - `details`: `GET /v1/games/{appid}` responses for every id being
 *     deleted (`{appid, depots: [{depotid, shared, size_bytes}]}`);
 *   - `mapping`: the full `GET /v1/mapping` table (`{depotid, appid}` for
 *     every mapping row on the server) — the only source of "which OTHER
 *     apps map this depot", since `shared` alone is a boolean;
 *   - `gamesByAppid` + `activeJobAppids`: the already-polled games/jobs
 *     snapshots (web/js/store.js) — no new poll loop, per this WP's brief
 *     ("wire the view to the store, do not create parallel polling"); a
 *     one-off `GET /v1/games/{appid}` / `GET /v1/mapping` fetch on demand
 *     (only when the user opens a bulk-delete confirm) is not a poll loop.
 *
 * "Has cache content" for judging whether an OTHER app protects a shared
 * depot mirrors the server's real predicate exactly
 * (`deletion._has_cache_content`, already ported once in
 * `web/js/demo-data.js`'s `hasCacheContent` and again as
 * `hasProtectedCacheContent` in `web/js/lib/game-status.js` — reused here,
 * not reimplemented a third time) — NOT the byte-based "is this visibly
 * cached" check the grid uses. An owner appid this module cannot resolve
 * (missing from `gamesByAppid`) is treated as protecting the depot
 * (fail-closed, mirrors the server's own "unreadable mapping row / no apps
 * row -> protected" rule, api/README.md "Last cached remnants").
 *
 * This is a PREVIEW for the confirm dialog, not the final word: the server
 * re-checks every depot's co-owner state immediately before removing it
 * (api/README.md, "Two-stage decision, same shape as the shared-depot
 * TOCTOU recheck") and is the authority library.js reports back to the user
 * after the real `DELETE /v1/cache/{appid}` calls return — see
 * `views/library.js`'s post-delete toast, which sums the SERVER's
 * `total_bytes_freed`, not this module's prediction.
 */

import { hasProtectedCacheContent } from "./game-status.js";

/**
 * @param {number[]} ids appids being deleted, as ONE batch.
 * @param {{
 *   details: {appid: number, depots: {depotid: number, shared: boolean, size_bytes: number|null}[]}[],
 *   mapping: {depotid: number, appid: number}[],
 *   gamesByAppid: Map<number, object>,
 *   activeJobAppids: Set<number>,
 * }} inputs
 * @returns {{
 *   ids: number[],
 *   rows: {depotid: number, sizeBytes: number|null, shared: boolean, others: number[], holderAppids: number[], free: boolean}[],
 *   sharedRows: object[],
 *   freedBytes: number,
 *   keptBytes: number,
 *   occupiedBytes: number,
 * }}
 */
export function buildMultiPlan(ids, { details, mapping, gamesByAppid, activeJobAppids }) {
  const idSet = new Set(ids);

  // De-dupe depots across the WHOLE selection. Keying by depotid means the
  // Map itself already guarantees at most one entry per depot no matter
  // how many selected games list it (`Map.set` on an existing key
  // overwrites, it never adds a second entry) — the `if (!depotsSeen.has(...))`
  // check below is NOT what prevents double-counting; it only decides
  // WHICH game's depot object wins when two selected games both report the
  // same depotid (first one seen, arbitrarily but deterministically by
  // `details` order). See multiplan.test.js for a regression pin on the
  // one-entry-per-depot outcome itself (real depot arithmetic, not this
  // tie-break rule).
  const depotsSeen = new Map(); // depotid -> {depotid, size_bytes}
  for (const detail of details) {
    for (const d of detail?.depots ?? []) {
      if (!depotsSeen.has(d.depotid)) depotsSeen.set(d.depotid, d);
    }
  }

  const ownersByDepot = new Map(); // depotid -> appid[]
  for (const row of Array.isArray(mapping) ? mapping : []) {
    if (!ownersByDepot.has(row.depotid)) ownersByDepot.set(row.depotid, []);
    ownersByDepot.get(row.depotid).push(row.appid);
  }

  function protects(appid) {
    const game = gamesByAppid?.get(appid);
    if (!game) return true; // fail-closed: unresolvable owner protects the depot
    return hasProtectedCacheContent(game, !!activeJobAppids?.has(appid));
  }

  const rows = [...depotsSeen.values()].map((d) => {
    const owners = ownersByDepot.get(d.depotid) || [];
    // Everyone mapping this depot EXCEPT the batch being deleted — a
    // co-owner INSIDE the batch is never a reason to keep it (the whole
    // point of computing this per-SET rather than per-game).
    const others = owners.filter((appid) => !idSet.has(appid));
    const holderAppids = others.filter(protects);
    return {
      depotid: d.depotid,
      sizeBytes: d.size_bytes,
      shared: owners.length > 1,
      others,
      holderAppids,
      free: holderAppids.length === 0,
    };
  });

  const sum = (pred) => rows.filter(pred).reduce((acc, r) => acc + (r.sizeBytes || 0), 0);

  return {
    ids,
    rows,
    sharedRows: rows.filter((r) => r.shared),
    freedBytes: sum((r) => r.free),
    keptBytes: sum((r) => !r.free),
    occupiedBytes: rows.reduce((acc, r) => acc + (r.sizeBytes || 0), 0),
  };
}
