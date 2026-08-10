/**
 * Game display-status logic (WP 4a.3).
 *
 * Ports the mockup's `dispKind`/`statusAct`/`hasContent` trio
 * (docs/design/vault-app-mockup.html, docs/design/vault-app-mockup-NOTES.md
 * round 5/6) onto the REAL `GET /v1/games` / `GET /v1/jobs` shapes
 * (api/README.md), which are narrower than the mockup's fake data in two
 * load-bearing ways documented inline below. Pure functions only — no DOM,
 * no fetch — so every branch is unit-testable headlessly
 * (web/tests/game-status.test.js).
 *
 * **Divergence 1 — no "stale" status.** `web/js/notifications.js` already
 * documents this: `apps.status` is exactly `idle|running|done|error` (WP
 * 3.12, "apps.status gains none"); there is no manifest-oracle field folded
 * into `GET /v1/games` (api/README.md, manifest-oracle section: "folding a
 * stale flag into the library list is a Phase-4 decision to make once the
 * UI knows how it wants to render it"). This WP makes that decision: ship
 * without the "Update ready" state/chip/glyph. `diffGamesForNotifications`
 * is already future-proofed for a `stale` status arriving later; this
 * module can gain a fourth `dispKind` the same day without any other
 * change here.
 *
 * **Divergence 2 — no live progress percentage.** The mockup's `job.pct`/
 * `job.speed`/`job.target` are simulator-only; the real `JobSummary` (see
 * api/README.md's jobs endpoints and vault_api/routers/jobs.py) carries no
 * byte-level progress field at all. The capsule pill therefore shows the
 * status icon ALONE while a job is running/paused (no fabricated
 * percentage) and the cached size once `size_bytes` is real — see
 * game-card.js. This also means the round-7 "patch in place" concern is
 * narrower in the real app than in the mockup (there is no live number to
 * patch); what MUST still be avoided is rebuilding a card on every jobs
 * poll tick just because a running job's `log_excerpt` grew (the worker
 * appends to it continuously) — `shouldRebuildForJob` below is the guard:
 * only a genuine `dispKind` transition warrants a rebuild, not a
 * byte-identical-status update.
 *
 * **Cache-content invariant, ported (mockup round 5, finding 6): "cached"
 * requires visible bytes.** A `GET /v1/games` row can legitimately report
 * `status: "done"` with `size_bytes: null` — not a server bug, but the
 * documented "last cached remnant" consequence (api/README.md, "Last
 * cached remnants"): app A's only depot was shared with app B, someone
 * deleted B's copy of it as an orphaned remnant, and A's own `status` is
 * untouched by that unrelated request. `hasVisibleCacheContent` is what
 * downgrades that to the honest "Not cached" card instead of a green badge
 * over nothing.
 *
 * Naming note (read before touching the deletion-side code in
 * multiplan.js): this module's `hasVisibleCacheContent` (BYTES-based,
 * "does the grid show this as cached right now") is a DIFFERENT predicate
 * from `hasProtectedCacheContent` (STATUS-based, mirrors
 * `deletion._has_cache_content` / demo-data.js's `hasCacheContent` — "does
 * this app's mapping protect a shared depot from deletion"). The two can
 * disagree (the remnant case above is exactly status-protected-but-not-
 * visibly-cached) — that is not a bug, it is why they are two functions.
 */

/** Display-status kinds this module ever returns. Intentionally NOT the
 * mockup's full set (no "stale"/"updating"/"verify" — see module header). */
export const KIND = Object.freeze({
  CACHED: "cached",
  NONE: "none",
  RUNNING: "running",
  PAUSED: "paused",
  ERROR: "error",
});

/** Job statuses that occupy this app's card with a live indicator. Queued
 * jobs are deliberately excluded (mockup parity: `jobFor` only matches
 * running/paused/verify — a queued job shows in the Downloads FIFO queue,
 * WP 4a.5, not on the Library card). GC jobs are excluded too: pause/resume
 * and the download pill are prefill-only concepts (api/README.md job
 * control table: pause on a GC job is `409`), so a GC job for this appid
 * must never drive its library card into a "running" download state. */
const LIVE_JOB_STATUSES = new Set(["running", "paused"]);

/**
 * Find the job (if any) that should drive this app's library card.
 * @param {object[] | null | undefined} jobs `GET /v1/jobs` snapshot.
 * @param {number} appid
 * @returns {object | undefined}
 */
export function findLiveJob(jobs, appid) {
  if (!Array.isArray(jobs)) return undefined;
  return jobs.find(
    (j) => j.appid === appid && j.type === "prefill" && LIVE_JOB_STATUSES.has(j.status),
  );
}

/** Build an `appid -> liveJob` lookup once per tick instead of re-scanning
 * the jobs array per card (O(n) instead of O(cards*jobs)). */
export function indexLiveJobsByAppid(jobs) {
  const map = new Map();
  if (!Array.isArray(jobs)) return map;
  for (const j of jobs) {
    if (j.type === "prefill" && LIVE_JOB_STATUSES.has(j.status)) map.set(j.appid, j);
  }
  return map;
}

/** Byte-based: does the grid have real content to show as cached right now?
 * See module header for why this is distinct from hasProtectedCacheContent. */
export function hasVisibleCacheContent(game) {
  return typeof game?.size_bytes === "number" && game.size_bytes > 0;
}

/**
 * Status-based: mirrors the server's own shared-depot protection predicate
 * exactly (`deletion._has_cache_content`, ported already once in
 * `web/js/demo-data.js`'s `hasCacheContent`): an app "has cache content"
 * unless it is `idle`, has never been prefilled, AND has no active job.
 * Used by multiplan.js to decide whether an OTHER app protects a shared
 * depot from a bulk delete — never for what the grid displays.
 *
 * @param {{status: string, last_prefill_at: string|null}} game
 * @param {boolean} hasActiveJob
 */
export function hasProtectedCacheContent(game, hasActiveJob) {
  const idle = game.status === "idle";
  const neverPrefilled = game.last_prefill_at == null;
  return !(idle && neverPrefilled && !hasActiveJob);
}

/**
 * The status a card should SHOW: a live job overrides the cache state.
 * @param {object} game GameSummary
 * @param {object|undefined} liveJob from indexLiveJobsByAppid, or undefined
 */
export function dispKind(game, liveJob) {
  if (liveJob) return liveJob.status === "paused" ? KIND.PAUSED : KIND.RUNNING;
  if (game.status === "error") return KIND.ERROR;
  return hasVisibleCacheContent(game) ? KIND.CACHED : KIND.NONE;
}

/**
 * What tapping the capsule pill / list-row icon does. Returns null when
 * there is no honest action (mirrors the mockup's rule: a non-actionable
 * icon renders as a plain span, never a button).
 *
 * Deliberate extension over the mockup: an `error` game IS actionable here
 * (retry) — the mockup never modeled a persistent per-app error status (its
 * "error" only ever lived on a finished JOBS row, never on a GAMES row), so
 * it never had to decide this. The real `apps.status` genuinely can sit at
 * `error` indefinitely until re-prefilled (api/README.md, "Per-game
 * deletion": "`error` is the honest state... a re-prefill... repairs the
 * cache"), so offering the same "start a prefill" action as `none` is the
 * direct, honest fix rather than forcing the user into a not-yet-built
 * detail sheet (WP 4a.4) just to retry.
 *
 * @param {object} game
 * @param {object|undefined} liveJob
 * @param {boolean} selecting `true` while multi-select is active — a tap
 *   must toggle selection instead of firing the action (mockup parity).
 */
export function statusAction(game, liveJob, selecting) {
  if (selecting) return null;
  if (liveJob) {
    if (liveJob.status === "running") return { type: "pause", title: "Pause download" };
    if (liveJob.status === "paused") return { type: "resume", title: "Resume download" };
    return null;
  }
  const kind = dispKind(game, undefined);
  if (kind === KIND.NONE) return { type: "download", title: "Download to cache" };
  if (kind === KIND.ERROR) return { type: "download", title: "Retry download" };
  return null; // cached — never a silent re-download (mockup round 5 rule)
}

/**
 * Round-7 rule, ported: decide whether a job transition on this appid is a
 * genuine STATE change (rebuild warranted) or a no-op update that must NOT
 * touch the card (e.g. `log_excerpt` growing on an otherwise-unchanged
 * running job — see module header, Divergence 2). Pure so the "a growing
 * log must never cause a rebuild" guarantee is directly mutation-testable.
 *
 * @param {object|undefined} prevJob `diffByKey` `prev` half (undefined for
 *   a brand-new row).
 * @param {object|undefined} currJob `diffByKey` `curr` half.
 */
export function isJobStateTransition(prevJob, currJob) {
  if (!currJob) return true; // job disappeared (finished/cancelled/removed) — always structural
  if (!prevJob) return true; // brand-new job row — always structural
  return prevJob.status !== currJob.status;
}
