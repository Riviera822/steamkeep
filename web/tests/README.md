# web/tests

Headless tests for the `web/` no-build vanilla SPA. No dependencies, no
bundler, no browser — plain ES modules run directly by Node's built-in test
runner (`node:test`, confirmed available: `node --version` reported
v24.12.0 on this machine when WP 4a.2 was implemented).

## Run

```
node --test "web/tests/*.test.js"
```

**Note (measured on this machine, Node v24.12.0, Windows):** `node --test
web/tests` (a bare directory, no glob) fails hard with a CJS
`MODULE_NOT_FOUND` for `web\tests` instead of discovering the files inside
it — Node's own default-pattern directory walk did not trigger for an
explicit directory argument here. The explicit glob above is the form that
actually works; use it, not the bare-directory form some `node --test` docs
show.

Run a single file directly the same way:

```
node --test web/tests/backoff.test.js
```

## Scope

- `backoff.test.js` — exponential backoff growth, cap, jitter bounds
  (including that the zero-floor is load-bearing, not just coincidentally
  satisfied), reset (`web/js/backoff.js`).
- `errors.test.js` — `classifyHttpStatus` across all six `ERROR_KINDS`
  (`web/js/errors.js`).
- `diff-utils.test.js` — the generic `diffByKey` list differ used by both
  the notification differ and the polling store
  (`web/js/diff-utils.js`).
- `notifications.test.js` — the client-side notification differ: every
  event type (`job_finished`, `job_failed`, `update_ready`,
  `bypass_suspected`, `bypass_resolved`), the no-change case, the
  first-poll case (must never fire a notification storm), the "stale
  requires cache content" invariant, and that an item aging out of a
  bounded list (e.g. `GET /v1/jobs?limit=20`) fires no event
  (`web/js/notifications.js`).
- `store.test.js` — the pure scheduling-decision helpers exported from
  `web/js/store.js` (`hasActiveJob`, `nextJobsIntervalMs`).
- `store-poll-loop.test.js` — the timer/in-flight orchestration in
  `web/js/store.js` (`ResourceLoop`), using a fake `document` object (only
  `hidden`/`addEventListener`/`removeEventListener` — `store.js` never
  touches more than that) and a manually-gated fetcher. No jsdom, no real
  browser: `store.js` only reads `document` lazily inside functions, never
  at module load, so a plain object stand-in is enough. Regression
  coverage for review blocker B1 (a nudge racing an in-flight poll used to
  fork a second, permanently duplicating timer chain and double-fire every
  notification).
- `demo-data.test.js` — `web/js/demo-data.js`'s request routing: the exact
  `CacheDeletionOut` response shape, ADR-0003 shared-depot protection
  (`DELETE /v1/cache/{appid}` skips a depot still cached by another game,
  frees it once every co-owner is uncached), 404/409 cases, all-or-nothing
  `POST /v1/prefill` body validation, that a cancelled job settles to
  `'cancelled'` and stays there rather than continuing to tick toward
  `'done'` (WP 4a.2), and (WP 4a.3) the `GET /v1/mapping` route added for
  the Library view's bulk-delete confirm dialog. This module imports only
  `errors.js` — no `window`, `document` or `fetch` — so it runs in bare
  Node with no fake environment at all.

`web/js/api.js` is the one module NOT covered here: exercising its real
`request()` path meaningfully needs a `fetch`/`localStorage`-capable
environment, and WP 4a.2's DoD is scoped to the differ and the backoff
math. Its one pure export, `classifyHttpStatus`, is re-exported from
`errors.js` and IS covered, by `errors.test.js`.

### WP 4a.3 — Library view

The view itself (`web/js/views/library.js`) and its DOM-building card
component (`web/js/components/game-card.js`) are NOT unit-tested directly
(same posture as `status-icon.js`'s DOM builder, WP 4a.1) — instead every
piece of DECISION logic they lean on is pulled into a pure `web/js/lib/`
module and tested headlessly here:

- `game-status.test.js` — `web/js/lib/game-status.js`: `dispKind` (a live
  job overrides cache state; the "cached requires visible bytes" invariant
  for the `status: "done"`-with-`size_bytes: null` "last cached remnant"
  case; `status: "error"` always shows as Failed), `statusAction` (which
  states are actionable and what they offer, including the deliberate
  "error is retryable" extension over the mockup), `findLiveJob`/
  `indexLiveJobsByAppid` (GC jobs and queued jobs never drive a card),
  `hasProtectedCacheContent` vs `hasVisibleCacheContent` (the two
  DIFFERENT "has cache content" predicates — see that module's header —
  and the remnant case where they disagree), and `isJobStateTransition`
  (the round-7 "a growing `log_excerpt` on an otherwise-unchanged job must
  never look like a transition" guard).
- `library-filters.test.js` — `web/js/lib/library-filters.js`: search is a
  case-insensitive substring match, search AND chips (a live job moves a
  game out of "Not cached" and into "Downloading" at the same time),
  `chipCounts` recomputing against the current query, and that there is no
  "stale"/"Update ready" chip (no oracle data on `GET /v1/games` yet).
- `bulk-plan.test.js` — `web/js/lib/bulk-plan.js`: busy/needsDownload/
  current classification (a GC job never counts as "busy" for a download
  decision; `error` joins `none` in "needs a download"), all three
  `buildBulkDownloadPlan` outcomes (something to download and the skip
  count; everything already cached with an explicit re-download secondary;
  everything already busy), and (WP 4a.3 review fix, should-fix 1)
  `classifyBulkDeleteEligibility`: has-cache-content, not "status is not
  none" — an `error` game with ZERO visible bytes is excluded (it has no
  depot mappings left; `DELETE /v1/cache/{appid}` would 404), an `error`
  game WITH bytes (a half-deleted/partial run) is included, and a busy
  game is excluded even if it has bytes.
- `multiplan.test.js` — `web/js/lib/multiplan.js`: the round-6 mockup
  scenario ported almost verbatim (deleting two of three co-owners of a
  shared depot keeps it; adding the third to the SAME batch frees it — this
  is the real **mutation target** for "dropping the set-dedupe": removing
  the `others.filter(appid => !idSet.has(appid))` exclusion makes this
  test fail), the last-cached-remnant rule (an idle, never-prefilled,
  job-free co-owner does NOT protect a depot; an active job on an
  otherwise-idle co-owner DOES), fail-closed on an unresolvable owner
  appid, and a regression pin that a depot two selected games both list is
  counted once in `occupiedBytes` (NOT a mutation-kill for the `if
  (!depotsSeen.has(...))` line itself — see the comment on that line in
  multiplan.js and on the test: the Map's own keying already prevents
  double-counting regardless of that guard).
- `cover-art.test.js` — `web/js/lib/cover-art.js`: the exact CDN host +
  asset path (matches the CSP entry in `api/vault_api/webui.py` 1:1), and
  that the fallback hue/pattern hash is deterministic (same appid -> same
  look, every time — no flaky randomness) and stays in range.
- `format.test.js` — `web/js/lib/format.js`: `formatBytesGB`'s
  under/over-100-GB rounding and that it never fabricates a number for
  null/zero/negative/non-finite input.
- `render-plan.test.js` — `web/js/lib/render-plan.js` (WP 4a.3 review fix,
  blocker B1): the pure games-tick patch-vs-rebuild decision. First poll,
  and added/removed rows, always mean a full render (grid membership can
  change). An updated row not currently on screen is skipped entirely. Two
  named **mutation targets**, one each direction: a game whose structural
  key CHANGED must land in `rebuild` (flip that branch and a card would
  silently keep showing the wrong icon shape forever); a game whose
  structural key is UNCHANGED (e.g. only `size_bytes` drifted while a
  download runs) must land in `patch`, NOT `rebuild` — flip THAT branch
  (treat any update as structural) and every games-poll tick would
  recreate, and thereby restart the animation of, a running download's
  status-icon node: the exact round-7 mockup bug, now on the games poll
  instead of the jobs one. `views/library.js` is the DOM-side executor of
  this plan (`applyGamesTick`) and is not unit-tested the same way — see
  the "WP 4a.3 — Library view" section above.

### WP 4a.5 — Downloads view

Same posture as WP 4a.3: `web/js/views/downloads.js` (the DOM-building
view) is not unit-tested directly — every piece of decision logic it leans
on is pulled into a pure `web/js/lib/` module and tested here.

- `job-partition.test.js` — `web/js/lib/job-partition.js`: `partitionJobs`
  buckets a `GET /v1/jobs` snapshot by status regardless of input order,
  treats a missing/non-array snapshot as empty, sorts `queued` FIFO by job
  id (NOT the snapshot's own newest-first order) while `history` keeps
  that newest-first order as-is. The load-bearing case is **the slot-release
  divergence** (api/README.md "The worker slot — a paused job does NOT
  hold it", recorded in docs/WORKPACKAGES.md's Phase 4a header): a paused
  job for one app and a running job for a DIFFERENT app coexist in two
  independent buckets, proving `running`/`paused` are not a single
  mutually-exclusive "active slot" like the mockup's — this is the exact
  presentation data the Downloads view's separate "Active"/"Paused"
  sections are built on. Also covers `countPending` (the nav-pip count:
  queued+running+paused, never done/error/cancelled), `queuePosition`,
  `jobIconKind` (every real status, including the real `cancelled` the
  mockup never modeled), and `jobStatusWord` (cancelled worded distinctly
  from failed — job outcome honesty; GC jobs get GC-specific wording, never
  the download vocabulary).
- `downloads-render-plan.test.js` — `web/js/lib/downloads-render-plan.js`:
  the pure jobs-tick patch-vs-rebuild decision for the Downloads view. First
  poll and added/removed rows always mean a full render. Two named
  **mutation targets**, one each direction: ANY `status` change anywhere in
  the batch must force `full: true` (flip that branch and a job that just
  transitioned section — e.g. running -> done — could sit in the wrong
  section with stale action buttons indefinitely); a `stop_request`-only
  change with the SAME `status` (the operator's pause/cancel request being
  acknowledged, or cleared once the worker actually stops the job) must
  land in `patchStopRequest`, NOT force `full` — flip THAT branch (treat
  any update as structural) and every pause/cancel click would recreate the
  running card's animated status-icon node the instant the server
  acknowledged it: the round-7 mockup bug, reintroduced on the one live
  field the real `JobSummary` actually has (no byte-level progress field
  exists — see that module's header for why this narrows the round-7
  concern versus the games poll). Also covers a mixed batch (one genuine
  patch + one no-op update) and a batch where a status change on one job
  and a stop_request-only change on another must still resolve to `full`
  (the stricter branch wins).
- `log-excerpt.test.js` — `web/js/lib/log-excerpt.js`: the lazy
  `GET /v1/jobs/{id}` history-row excerpt display selection. Collapsed
  always wins even over a completed fetch or an in-flight load (a
  fast re-collapse must not leak stale content); loading; error (and error
  taking priority over a STALE excerpt from a previous successful fetch);
  empty for `null`/`undefined`/whitespace-only excerpt (the `undefined`
  case is "never fetched", distinct from a job that genuinely produced no
  output, but both display the same way); ready with normal multi-line and
  single-line text; and the truncation-marker handling from api/README.md's
  documented `log_excerpt` shape — detected and STRIPPED from the displayed
  body (never leaks into the first line), only recognised as a literal
  prefix (a log line merely containing the word "truncated" mid-file must
  not false-positive), and blank lines immediately after a stripped marker
  are not shown as spurious empty lines.
- `format.test.js` gained `formatTimestamp` coverage (WP 4a.5): null/
  undefined/unparseable input never fabricates a time (returns "—", same
  posture as `formatBytesGB`); a valid ISO timestamp renders through
  (asserted loosely — containing the year — since the exact locale-formatted
  string is runtime-locale/timezone-dependent).
