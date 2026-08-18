/**
 * "Check & update all cached games" — the Phase 4c web trigger's pure
 * decision logic (WP 4c-web), consuming `POST /v1/prefill/cached` (Phase
 * 4c, WP 4c-api — see api/README.md "Check & update all cached games" for
 * the full, reviewer-verified server contract this module is built
 * against, quoted rather than re-derived).
 *
 * **The response is a flat `PrefillJobRef[]`** — one entry per selected
 * app, `{appid, job_id, status, deduplicated}` — that silently conflates
 * FIVE different real outcomes if a caller reports only its length:
 *   - a brand-new job just queued (`deduplicated: false`, `status:
 *     "queued"` always, per the contract).
 *   - an already in-flight job that is ALSO still `queued` right now
 *     (`deduplicated: true`, `status: "queued"`) — `enqueue_prefill`
 *     returns the existing job with ITS OWN status, and a job the single
 *     worker has not yet claimed is a completely ordinary thing to dedupe
 *     onto (a double-press before the worker gets to it is the common
 *     case, not an edge case) — reporting this as "already in progress"
 *     would be false, it is still waiting in the FIFO queue.
 *   - an already in-flight job that is RUNNING right now (`deduplicated:
 *     true`, `status: "running"`) — this press changed nothing for that
 *     app, work is genuinely happening.
 *   - an already in-flight job that is PAUSED (`deduplicated: true`,
 *     `status: "paused"`) — an earlier pause is still in the way and
 *     **nothing starts** for this app until the user resumes or cancels it
 *     (WP 3.12: a paused prefill's on-disk chunks ARE its progress store —
 *     deliberate, not a bug). Reporting this as "queued" or "started" would
 *     be a lie.
 *   - an empty selection (`[]`) — nothing is currently cached; `202`, not
 *     an error.
 *
 * `partitionCachedPrefillOutcome`/`summarizeCachedPrefillOutcome` sort a raw
 * response into these buckets as pure functions (no DOM, no fetch) so the
 * claims that would otherwise silently mislead a user are each
 * independently testable (web/tests/cached-prefill-outcome.test.js): a
 * `paused` dedupe entry must never be worded as "queued"/"started", a
 * `queued` dedupe entry must never be worded as "already in progress", and
 * an empty response must never be worded as a failure.
 *
 * **The forced-run note is scoped to what THIS press actually queued fresh
 * (review round 1 blocker).** An earlier version of `summarizeCachedPrefillOutcome`
 * took only `refs` and left the caller to compute + unconditionally append a
 * "(N forced...)" note from its OWN `GET /v1/games` snapshot — which could
 * (and, live-reproduced in headless Chrome, did) claim forced work was
 * starting when the selection was EMPTY, or credit a forced app to a press
 * that only deduplicated (an already-`running`/`queued`/`paused` job's force
 * decision was made whenever IT was first queued, not by this later press).
 * That is exactly the "claims work started when it did not" failure class
 * this module exists to prevent — the composition now lives here, gated on
 * `partition.queued.length > 0`, and scoped to only the appids actually in
 * that bucket (`countForcedCachedGames`'s new `queuedRefs` parameter) —
 * never in untested view glue again.
 *
 * `describeCachedPrefillError` covers the mirror-image failure case: per
 * api/README.md, each app is enqueued in its own committed transaction, so
 * a mid-loop `5xx` can leave the first K apps durably `queued` before the
 * response ever arrives. The honest recovery is re-reading `GET /v1/jobs`,
 * never "nothing happened" — this function is what tells the caller
 * (`views/library.js`) to do that, and only for that one error kind.
 *
 * `createCheckAndUpdateAction` is the in-flight guard: the button-lock
 * requirement reduced to something DOM-free and testable, the same shape as
 * `store.js`'s `ResourceLoop` in-flight guard (module header there) but
 * scoped to a single fire-and-settle action instead of a repeating poll
 * loop — a second `run()` while the first is still pending is a no-op
 * rather than firing a second concurrent request.
 */

import { ERROR_KINDS } from "../errors.js";

/**
 * @param {object[] | null | undefined} refs `POST /v1/prefill/cached`'s raw
 *   response body.
 * @returns {{queued: object[], alreadyQueued: object[], alreadyRunning: object[], alreadyPaused: object[], total: number}}
 *   `queued` = brand-new jobs (`deduplicated: false`); `alreadyQueued` =
 *   deduplicated against a job that is STILL `queued` (not yet claimed by
 *   the single worker); `alreadyRunning` = deduplicated against a job that
 *   is `running` (or any other non-`paused`/non-`queued` in-flight status,
 *   defensively); `alreadyPaused` = deduplicated against a `paused` job
 *   specifically — kept as its OWN bucket rather than folded into
 *   `alreadyRunning`, because unlike a running OR queued dedupe (something
 *   is genuinely going to happen) a paused dedupe means nothing will happen
 *   until the user acts.
 */
export function partitionCachedPrefillOutcome(refs) {
  const list = Array.isArray(refs) ? refs : [];
  const queued = [];
  const alreadyQueued = [];
  const alreadyRunning = [];
  const alreadyPaused = [];
  for (const ref of list) {
    if (!ref || ref.deduplicated !== true) {
      queued.push(ref);
    } else if (ref.status === "paused") {
      alreadyPaused.push(ref);
    } else if (ref.status === "queued") {
      alreadyQueued.push(ref);
    } else {
      alreadyRunning.push(ref);
    }
  }
  return { queued, alreadyQueued, alreadyRunning, alreadyPaused, total: list.length };
}

/** Cached games among ONLY the given `queuedRefs` (a `partitionCachedPrefillOutcome`
 * bucket — the appids THIS press actually enqueued fresh, review round 1
 * blocker fix) that carry `needs_force: true` right now. Deliberately NOT a
 * function of the whole `GET /v1/games` snapshot: a deduplicated entry's
 * force decision was already made whenever that job was first queued, not
 * by this press, so crediting it here would misreport work this press did
 * not start. Client-side estimate only — `GET /v1/games` is polled
 * independently of this action and the server re-decides `needs_force` per
 * app at job-claim time, not at selection time (api/README.md
 * "needs_force" — GC execute and deletion can flip it between polls) — a
 * heads-up, not a guarantee, which is why `summarizeCachedPrefillOutcome`
 * phrases it as "may take longer" rather than a promise.
 *
 * @param {object[]} queuedRefs the `queued` bucket from
 *   `partitionCachedPrefillOutcome` — `PrefillJobRef`s with `deduplicated: false`.
 * @param {object[] | null | undefined} games `GET /v1/games` snapshot.
 */
export function countForcedCachedGames(queuedRefs, games) {
  const gamesByAppid = new Map((Array.isArray(games) ? games : []).map((g) => [g.appid, g]));
  let count = 0;
  for (const ref of Array.isArray(queuedRefs) ? queuedRefs : []) {
    const game = ref && gamesByAppid.get(ref.appid);
    if (game && game.needs_force) count += 1;
  }
  return count;
}

/**
 * The toast text for a successful `POST /v1/prefill/cached` call, built from
 * the partition above so every one of the outcomes reads honestly on its
 * own — including a MIXED result (e.g. some new, one paused) reporting each
 * part distinctly instead of collapsing to one misleading number.
 *
 * @param {object[] | null | undefined} refs
 * @param {object[] | null | undefined} [games] `GET /v1/games` snapshot,
 *   for the forced-run heads-up note — optional; omitting it simply omits
 *   the note (never throws, never fabricates a count).
 * @returns {{message: string, warn: boolean}} `warn` is true only when a
 *   paused dedupe is present — that is the one case that needs the user to
 *   go DO something (resume or cancel) rather than just wait.
 */
export function summarizeCachedPrefillOutcome(refs, games) {
  const p = partitionCachedPrefillOutcome(refs);
  if (p.total === 0) {
    // The empty-selection case (api/README.md: "No cached apps ⇒ [] with a
    // normal 202, never an error") — must read as a normal, unremarkable
    // outcome, never as a failure, and (review round 1 blocker) NEVER
    // followed by a forced-run note: nothing was queued, so there is no
    // "those run full, disk-speed re-checks" to warn about, no matter what
    // `games` says about some unrelated app's `needs_force`.
    return { message: "Nothing cached to check.", warn: false };
  }

  const parts = [];
  if (p.queued.length) {
    // "check & update", never bare "checking" (docs/PROJECT_PLAN.md §7
    // Phase 4c's honesty rule applies to every string this action produces,
    // not just the button label).
    parts.push(`${p.queued.length} queued for check & update`);
  }
  if (p.alreadyQueued.length) {
    // Deliberately distinct from `alreadyRunning`'s wording below: a job
    // still sitting in the FIFO queue is not "in progress" yet.
    parts.push(`${p.alreadyQueued.length} already queued`);
  }
  if (p.alreadyRunning.length) {
    parts.push(`${p.alreadyRunning.length} already in progress`);
  }
  if (p.alreadyPaused.length) {
    // Deliberately NOT "queued"/"started" — see this module's header and
    // `partitionCachedPrefillOutcome`'s docstring: nothing happens for
    // these until the user acts.
    parts.push(
      `${p.alreadyPaused.length} paused — resume or cancel ${
        p.alreadyPaused.length > 1 ? "them" : "it"
      } first`,
    );
  }
  let message = parts.join(" · ");

  // Forced-run heads-up — gated on p.queued.length > 0 (review round 1
  // blocker: an all-deduplicated outcome queues nothing fresh, so there is
  // nothing here for a forced run to apply to) and scoped to ONLY the
  // appids p.queued actually names (never the whole games snapshot).
  //
  // These are two independent layers and each one alone would suffice: with an
  // empty p.queued the scoping already yields 0. The gate is deliberate
  // belt-and-braces for the blocker this shipped with once, so a later
  // "simplification" should remove THIS gate, never the scoping — the scoping
  // is what carries a standalone mutation pin.
  if (p.queued.length > 0) {
    const forcedCount = countForcedCachedGames(p.queued, games);
    if (forcedCount > 0) {
      message += ` (${forcedCount} forced — those run full, disk-speed re-checks and may take longer)`;
    }
  }

  return { message, warn: p.alreadyPaused.length > 0 };
}

/**
 * How to react to a FAILED `POST /v1/prefill/cached` call.
 *
 * The one case that must never read as "nothing happened": a `5xx`. Per
 * api/README.md ("A mid-loop 5xx leaves a partial, unreported result — same
 * as POST /v1/prefill"), the route loops over app ids inside one open
 * connection and enqueues one at a time, so a `5xx` partway through can
 * still have left the first K apps durably `queued` before the response
 * body was ever sent. The correct recovery is re-reading `GET /v1/jobs`,
 * never a blind "retry" or a message implying the button press did
 * nothing — `refresh: true` is the caller's signal to do that (`views/
 * library.js` maps it to `store.refreshNow()`, the same re-poll every other
 * action in this view already triggers on success).
 *
 * Every other error kind (401 wrong/missing key, a validation-shaped 4xx,
 * a network failure that never reached the server) genuinely means no job
 * was queued by THIS call — `refresh: false` for those.
 *
 * @param {{kind?: string, detail?: unknown, message?: string}} err an
 *   ApiError (see errors.js) — but never assumed to be one (defensive: an
 *   unexpected thrown value still gets a safe fallback message).
 * @returns {{message: string, warn: boolean, refresh: boolean}}
 */
export function describeCachedPrefillError(err) {
  if (err && err.kind === ERROR_KINDS.SERVER) {
    return {
      message:
        "The server had trouble partway through — some games may already be queued. Re-checking Downloads for the real state…",
      warn: true,
      refresh: true,
    };
  }
  const detail = err && typeof err.detail === "string" && err.detail ? err.detail : null;
  return {
    message: detail || (err && err.message) || "Could not start the check.",
    warn: true,
    refresh: false,
  };
}

/**
 * A run-at-most-one-at-a-time guard around `fetcher`. `run()` called while a
 * previous call is still pending returns `{skipped: true}` immediately
 * WITHOUT invoking `fetcher` again — this is "disable the button while in
 * flight" with the DOM removed from the picture, the same in-flight
 * guarantee `store.js`'s `ResourceLoop` gives its poll loop (see that
 * module's header), scoped here to one fire-and-settle press instead of a
 * repeating timer chain. `views/library.js` ALSO disables the real button
 * for the same window (belt and suspenders, same posture as store.js's
 * `inFlight` flag plus its generation token) — this guard is what makes
 * that guarantee provable without a browser.
 *
 * @param {{fetcher: () => Promise<unknown>}} deps
 */
export function createCheckAndUpdateAction({ fetcher }) {
  let inFlight = false;
  return {
    isInFlight: () => inFlight,
    /** @returns {Promise<{skipped: true} | {skipped: false, ok: true, refs: unknown} | {skipped: false, ok: false, err: unknown}>} */
    async run() {
      if (inFlight) return { skipped: true };
      inFlight = true;
      try {
        const refs = await fetcher();
        return { skipped: false, ok: true, refs };
      } catch (err) {
        return { skipped: false, ok: false, err };
      } finally {
        inFlight = false;
      }
    },
  };
}
