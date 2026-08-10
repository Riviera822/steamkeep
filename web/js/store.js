/**
 * Polling store (WP 4a.2).
 *
 * Owns the three poll loops the notification model depends on
 * (docs/design/vault-app-mockup-NOTES.md, round 5 "Notifications are a poll,
 * not a push"): `GET /v1/jobs` fast while any job is active, `GET /v1/games`
 * and `GET /v1/clients` slow otherwise. Every tick is diffed against that
 * resource's previous snapshot (diff-utils.js) before subscribers see it, so
 * a view can patch DOM nodes for `added`/`updated`/`removed` instead of
 * replacing the whole list — the round-7 mockup rule applies generally, not
 * just to its own renderer: "rebuild DOM only when a card's STATE changes...
 * patch the volatile values in place... touch no animated node", because a
 * fresh array on every tick is exactly what makes a naive re-render destroy
 * and recreate animated status-icon nodes.
 *
 * Backoff (backoff.js) grows a resource's interval on consecutive failures
 * and resets to the normal schedule the instant a poll succeeds again.
 * `document.hidden` PARKS every loop (no timer re-armed at all) rather than
 * polling on a 1s keep-alive — an unwatched tab gets zero requests until
 * `visibilitychange` fires, at which point every loop is nudged back to
 * life immediately. Not a failure, so it never touches the backoff counter;
 * the notification model already tolerates missed intervals (it diffs
 * whatever the next successful poll returns, however much time passed).
 *
 * **In-flight safety (WP 4a.2 review, blocker B1).** A poll's `fetch` is
 * awaited, and during that await `this.timerId` is not a live id to cancel
 * (the timer already fired to start this tick) — a naive "cancel the timer
 * and reschedule for now" nudge (the mockup's pull-to-refresh /
 * `visibilitychange` gesture) used to `clearTimeout` a dead id and start a
 * SECOND, independently self-re-arming timer chain, compounding on every
 * subsequent nudge and — because both chains shared `this.prev` — firing
 * every notification event twice. Fixed with two independent guards, one
 * belt, one suspenders:
 *   1. an `inFlight` flag: a nudge arriving while a fetch is outstanding
 *      does NOT start a new timer at all, it just sets `pendingRefresh` so
 *      the in-flight tick polls again immediately once it settles instead
 *      of waiting out its normal interval;
 *   2. a generation token bumped on every real `_scheduleNext` call and
 *      captured by the `_tick` it schedules: if the token has since moved
 *      by the time that tick's timer fires (or its await resolves), the
 *      tick drops its own result and does not reschedule, so at most one
 *      live chain can ever exist per loop even if some future call site
 *      manages to call `_scheduleNext` directly.
 * Regression coverage: web/tests/store-poll-loop.test.js (poll count does
 * not compound under repeated nudges; a single job completion fires
 * `job_finished` exactly once even when nudges race it).
 *
 * The pure decision helpers below (`hasActiveJob`, `nextJobsIntervalMs`)
 * are covered in web/tests/store.test.js; the timer/in-flight orchestration
 * itself is covered in web/tests/store-poll-loop.test.js using a fake
 * `document` and a manually-gated fetcher — no jsdom, no real browser,
 * plain `node:test` (see that file's header for why this needed no new
 * dependency).
 */

import { api } from "./api.js";
import { diffByKey } from "./diff-utils.js";
import {
  diffJobsForNotifications,
  diffGamesForNotifications,
  diffClientsForNotifications,
} from "./notifications.js";
import { createBackoffState } from "./backoff.js";

export const DEFAULT_INTERVALS = Object.freeze({
  jobsFastMs: 2000,
  jobsSlowMs: 15000,
  gamesMs: 15000,
  clientsMs: 20000,
});

const JOB_ACTIVE_STATUSES = new Set(["queued", "running", "paused"]);

/** Pure: does this `GET /v1/jobs` snapshot contain a still-in-flight job? */
export function hasActiveJob(jobs) {
  return Array.isArray(jobs) && jobs.some((j) => JOB_ACTIVE_STATUSES.has(j.status));
}

/**
 * Pure: which poll interval applies to the jobs loop given its most recent
 * snapshot (mockup cadence: fast while anything is queued/running/paused).
 */
export function nextJobsIntervalMs(jobs, { jobsFastMs, jobsSlowMs } = DEFAULT_INTERVALS) {
  return hasActiveJob(jobs) ? jobsFastMs : jobsSlowMs;
}

function isDocumentHidden() {
  return typeof document !== "undefined" && document.hidden === true;
}

/** One resource's poll loop: fetch -> diff -> notify -> schedule next tick. */
class ResourceLoop {
  constructor({ fetcher, keyFn, getIntervalMs, backoffOptions, onTick, onError }) {
    this.fetcher = fetcher;
    this.keyFn = keyFn;
    this.getIntervalMs = getIntervalMs;
    this.backoff = createBackoffState(backoffOptions);
    this.onTick = onTick;
    this.onError = onError;
    this.prev = undefined; // undefined = no snapshot yet (the differ's "first poll")
    this.timerId = null;
    this.stopped = true;
    // Generation token: bumped by every _scheduleNext call; a _tick captures
    // the token it was scheduled with and drops its result (no reschedule)
    // if the token has since moved — see the B1 fix note in the module
    // header. Belt.
    this.gen = 0;
    // True from the moment a fetch is issued until it settles. Suspenders:
    // a nudge arriving in this window must not touch the timer at all.
    this.inFlight = false;
    this.pendingRefresh = false;
  }

  start() {
    if (!this.stopped) return;
    this.stopped = false;
    this._scheduleNext(0);
  }

  stop() {
    this.stopped = true;
    this.pendingRefresh = false;
    this._clearTimer();
  }

  _clearTimer() {
    if (this.timerId !== null) {
      clearTimeout(this.timerId);
      this.timerId = null;
    }
  }

  _scheduleNext(delayMs) {
    if (this.stopped) return;
    this._clearTimer();
    const token = ++this.gen;
    this.timerId = setTimeout(() => this._tick(token), delayMs);
  }

  /**
   * Ask this loop to poll again as soon as possible. Safe to call at ANY
   * time, including while a fetch is already in flight: in that case it
   * does not start a second timer — it only records that the in-flight
   * tick should schedule an immediate follow-up once it settles, instead
   * of waiting out its normal interval. This is what makes repeated nudges
   * (a `visibilitychange` storm, mashing pull-to-refresh) coalesce into at
   * most one extra poll rather than one fork per nudge.
   */
  nudge() {
    if (this.stopped) return;
    if (this.inFlight) {
      this.pendingRefresh = true;
      return;
    }
    this._scheduleNext(0);
  }

  async _tick(token) {
    if (this.stopped || token !== this.gen) return;
    this.timerId = null; // this timer has fired; nothing left to clear for it

    if (isDocumentHidden()) {
      // Parked: no timer is re-armed. The store's own visibilitychange
      // listener calls nudge() when the tab comes back, which is the only
      // thing that wakes this loop up again while hidden.
      return;
    }

    const prev = this.prev;
    this.inFlight = true;
    let curr;
    try {
      curr = await this.fetcher();
    } catch (err) {
      this.inFlight = false;
      if (this.stopped || token !== this.gen) return;
      const delay = this.backoff.next();
      if (this.onError) this.onError(err);
      this._scheduleNext(delay);
      return;
    }
    this.inFlight = false;
    if (this.stopped || token !== this.gen) return; // stop()/a newer schedule fired while the fetch was in flight

    this.backoff.reset();
    const diff = diffByKey(prev, curr, this.keyFn);
    this.prev = curr;
    if (this.onTick) this.onTick({ prev, curr, diff });

    const runAgainNow = this.pendingRefresh;
    this.pendingRefresh = false;
    this._scheduleNext(runAgainNow ? 0 : this.getIntervalMs(curr));
  }
}

/**
 * Build (but do not start) the polling store.
 *
 * @param {{
 *   apiClient?: typeof api,
 *   intervals?: typeof DEFAULT_INTERVALS,
 *   backoffOptions?: import("./backoff.js").BackoffOptions,
 * }} [options]
 */
export function createPollingStore({
  apiClient = api,
  intervals = DEFAULT_INTERVALS,
  backoffOptions,
} = {}) {
  const subscribers = { jobs: new Set(), games: new Set(), clients: new Set(), notifications: new Set() };

  function emit(kind, payload) {
    for (const cb of subscribers[kind]) cb(payload);
  }

  const jobsLoop = new ResourceLoop({
    fetcher: () => apiClient.jobs(),
    keyFn: (j) => j.id,
    getIntervalMs: (curr) => nextJobsIntervalMs(curr, intervals),
    backoffOptions,
    onTick: ({ prev, curr, diff }) => {
      emit("jobs", { items: curr, diff });
      const events = diffJobsForNotifications(prev, curr);
      if (events.length) emit("notifications", events);
    },
    onError: (err) => emit("jobs", { error: err }),
  });

  const gamesLoop = new ResourceLoop({
    fetcher: () => apiClient.games(),
    keyFn: (g) => g.appid,
    getIntervalMs: () => intervals.gamesMs,
    backoffOptions,
    onTick: ({ prev, curr, diff }) => {
      emit("games", { items: curr, diff });
      const events = diffGamesForNotifications(prev, curr);
      if (events.length) emit("notifications", events);
    },
    onError: (err) => emit("games", { error: err }),
  });

  const clientsLoop = new ResourceLoop({
    fetcher: () => apiClient.clients(),
    keyFn: (c) => c.client_id,
    getIntervalMs: () => intervals.clientsMs,
    backoffOptions,
    onTick: ({ prev, curr, diff }) => {
      emit("clients", { items: curr, diff });
      const events = diffClientsForNotifications(prev, curr);
      if (events.length) emit("notifications", events);
    },
    onError: (err) => emit("clients", { error: err }),
  });

  const loops = [jobsLoop, gamesLoop, clientsLoop];

  function onVisibilityChange() {
    // Coming back into view: nudge every loop to refresh immediately
    // instead of waiting out whatever interval was in progress when the
    // tab was hidden (the mockup's pull-to-refresh / refresh-icon
    // gesture does the same "reload what the screen showed" thing).
    // ResourceLoop.nudge() is the single safe entry point for this — see
    // the B1 fix note in the module header for why this must never
    // clearTimeout+reschedule directly from here.
    if (!isDocumentHidden()) {
      for (const loop of loops) loop.nudge();
    }
  }
  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", onVisibilityChange);
  }

  return {
    start() {
      for (const loop of loops) loop.start();
    },
    stop() {
      for (const loop of loops) loop.stop();
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibilityChange);
      }
    },
    /** Force every loop to poll on its very next tick (e.g. pull-to-refresh). */
    refreshNow() {
      for (const loop of loops) loop.nudge();
    },
    /**
     * Subscribe to a resource's ticks (`"jobs"|"games"|"clients"`, payload
     * `{items, diff}` or `{error}`) or to derived notification events
     * (`"notifications"`, payload an array of event objects from
     * notifications.js). Returns an unsubscribe function.
     */
    subscribe(kind, callback) {
      if (!subscribers[kind]) throw new RangeError(`Unknown subscription kind: ${kind}`);
      subscribers[kind].add(callback);
      return () => subscribers[kind].delete(callback);
    },
    /** Latest snapshot for a resource, or `undefined` before its first poll. */
    snapshot(kind) {
      if (kind === "jobs") return jobsLoop.prev;
      if (kind === "games") return gamesLoop.prev;
      if (kind === "clients") return clientsLoop.prev;
      throw new RangeError(`Unknown resource: ${kind}`);
    },
  };
}
