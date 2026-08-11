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
  the download vocabulary). WP 4a.8 backport: an unrecognized status routes
  into `history` with a neutral presentation (`jobIconKind` -> `"none"`,
  `jobStatusWord` -> the raw string) instead of matching no bucket at all
  and silently disappearing — ported from the Android sibling's
  `JobPartition.kt`, which had already made this improvement over the web
  port; does NOT count toward `countPending`.
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

### WP 4a.6 — Settings + onboarding + Steam identity

Same posture as WP 4a.3/4a.5: `web/js/views/settings.js` and
`web/js/onboarding.js` (the DOM-building view/overlay) are not unit-tested
directly — every piece of decision logic they lean on is pulled into a pure
`web/js/lib/` module and tested here. Both modules also import `window`
transitively (via `router.js`), same as every other view, so they cannot be
`import()`-ed under bare Node either — this WP's live verification was done
against a real `uvicorn` instance instead (see the coder's report).

- `steamid.test.js` — `web/js/lib/steamid.js`'s `validSteamId64`: the exact
  17-ASCII-digit, range-checked grammar mirrored from
  `vault_api.steam_relay.valid_steamid64`, using `BigInt` because the
  individual-account SteamID64 base (76561197960265728) already exceeds
  `Number.MAX_SAFE_INTEGER` — a plain `Number()` range check would silently
  round distinct 17-digit inputs onto the same handful of representable
  doubles. Covers the base/max boundary (mutation target: off-by-one either
  direction), wrong length, non-digit characters, and non-ASCII look-alike
  digits (the same Python `str.isdigit()` trap `docs/LEARNINGS.md`'s
  "Parsers" section already documents for other modules).
- `steam-key-form.test.js` — `web/js/lib/steam-key-form.js`: `validSteamWebApiKey`
  (exactly 32 hex characters, either case) and `submitSteamKey`'s orchestration
  — the **load-bearing pin**: the typed key is cleared from the field
  unconditionally in every outcome (validation failure, a rejected `PUT`, a
  network error, success), proven with a plain `{value}`-shaped stand-in
  object rather than a real `<input>` (ADR-0004 addendum: the key must never
  be retained after a submit attempt), and that a thrown error's message
  never contains the raw key.
- `settings-diff.test.js` — `web/js/lib/settings-diff.js`'s `buildSettingsPatch`:
  the **mutation-worthy pin** LEARNINGS asks for — a touched field whose
  draft value equals the current effective value must be DROPPED, not sent
  (removing that equality check makes every touched key appear regardless of
  whether anything changed). Also covers: an untouched field never appears at
  all; `reset` only sends `null` when a `db` override actually exists (a
  reset against an env/default-sourced key is a no-op); blank is a REAL
  override value for `schedule_window`/`webhook_url` (ADR-0009), never
  silently coerced into a reset; `webhook_events` list/comma-string
  equivalence (order- and whitespace-independent); env-only/unrecognised
  keys dropped defensively.
- `settings-presentation.test.js` — `web/js/lib/settings-presentation.js`:
  `appliesText`/`sourceLabel` cover all three real values distinctly (and
  fall back honestly, never silently, for an unrecognised one), `canReset`
  (only a `db`-sourced, non-`env_only` entry offers a reset), and
  `effectiveAsInputValue`'s three special cases — `null` becomes blank (the
  `schedule_window`/`webhook_url` "disabled" state, never the string
  `"null"`), a list (`webhook_events`) becomes a comma-joined string, and an
  empty list becomes `""` rather than `"[]"` or a stray leading comma.
- `onboarding-steps.test.js` — `web/js/lib/onboarding-steps.js`: the
  **mutation-worthy pin** that step 1 cannot be left until the vault API key
  has actually been verified (`canAdvance`/`nextStep` gated on `tested`,
  unlike the mockup's mere form-completeness check — this fork's step 1
  result is used for real, so advancing on an unverified guess would ship a
  broken key into `localStorage`); step 2 (Steam identity) is unconditionally
  optional; `prevStep`/`clampStep` bounds; `progressPercent` monotonicity;
  and the other mutation pin, `shouldShowOnboarding` (only true with no
  stored key AND no demo mode — either alone must suppress it).
- `demo-data-settings.test.js` extends `demo-data.js`'s coverage (WP 4a.2's
  fixture module) with the WP 4a.6 routes it gained: `GET`/`PATCH
  /v1/settings` (db/env/default precedence, all-or-nothing validation, an
  env-only key rejected by name distinct from "unknown key", the Pydantic
  lax-mode boolean trap, `webhook_events` accepting a JSON array) and
  `/v1/steam/*` (unconfigured -> `409`, a malformed key -> `422`, configured
  -> `200` with the fixture library, turning the relay off is immediate,
  `resetDemoData()` clears both). Reuses `lib/steamid.js`/
  `lib/steam-key-form.js`'s validators rather than duplicating the grammar a
  third time.

### WP 4a.4 — Detail sheet + delete flows

Same posture as every prior view: `web/js/components/game-detail-sheet.js`
(the DOM-building sheet) is not unit-tested directly (see
`components/sheet-dialog.js`'s header for the general reasoning) — every
piece of decision logic it leans on is pulled into a pure `web/js/lib/`
module, ported from the reviewed Android sibling's `ui/detail/logic/`
package (WP 4b.6) onto plain tagged objects instead of Kotlin sealed
classes, and tested here.

- `depot-presentation.test.js` — `web/js/lib/depot-presentation.js`: the
  four-state sharing tag (EXCLUSIVE/PROTECTED/SOLE_HOLDER/**ORPHANED** — the
  recorded WP 4b.6 divergence this WP adopts, docs/WORKPACKAGES.md's Phase
  4a header), each state's independence from `thisAppIsHolder` where
  irrelevant, and co-owner name resolution (`gamesByAppid` lookup, "App
  {appid}" fallback, `cached` mirroring `holderAppids` membership).
- `detail-job.test.js` — `web/js/lib/detail-job.js`: `findTrackedJob` is
  deliberately BROADER than `lib/game-status.js`'s `findLiveJob` (includes
  `queued`, excludes GC jobs — pause/resume/download are prefill-only
  concepts) and `detailJobActions`'s exact queued/running/paused ->
  action-set table from api/README.md's "Job control".
- `detail-wording.test.js` — `web/js/lib/detail-wording.js`:
  `confirmedCurrentWording`'s three cases, incl. the post-deletion shape
  (`last_manifest_check` survives, `last_prefill_at` does not) rendering as
  `CONFIRMED_BEFORE_CACHE_CLEARED` rather than a bare, contradiction-reading
  timestamp.
- `gc-log-summary.test.js` — `web/js/lib/gc-log-summary.js`: fixtures are
  the EXACT log text api/README.md quotes for a dry run and an executed run
  (same fixtures the Android sibling's `GcLogSummaryTest.kt` pins) —
  null/blank/no-totals-line input, dry-run `would_delete`/`held_back`
  scoped to the TOTALS line (not an earlier per-depot `held_back` with the
  same key name), the `\b`-guarded `bytes_freed` vs. the `dedupe_`/`total_`
  prefixed lookalikes regardless of key order.
- `gc-flow.test.js` — `web/js/lib/gc-flow.js`: the state machine's one
  guarantee (GC EXECUTE is never sent without an explicit confirm after a
  dry run) as a full parametrised pin — `confirm_execute`/`request_execute`
  rejected from every state except their one accepting predecessor — plus
  the full dry-run-then-execute path, cancellation mid-poll (Cancelled, not
  Error), a stale poll result for a different job id being ignored in BOTH
  polling states, and `start_dry_run`'s Idle/ExecuteDone/Error/Cancelled ->
  accepted, everything mid-flight -> rejected table.
- `detail-render-plan.test.js` — `web/js/lib/detail-render-plan.js`: the
  round-7 patch-vs-rebuild structural key for the sheet (WP brief: "must
  not rebuild animated nodes on poll ticks"). Two named mutation targets —
  a `dispKind` change and a depot's sharing TAG changing (a co-owner's cache
  state moved) must each change the key — plus a `trackedJobStatus`
  change, and the documented fact that a size-only tick is simply not part
  of the key's inputs at all (the caller never feeds bytes into it).
- `demo-data-gc.test.js` extends `demo-data.js`'s coverage with this WP's
  additions: `last_manifest_check` now appears on `GET /v1/games` AND
  `GET /v1/games/{appid}` (it was missing entirely before this WP, a real
  gap since the field has existed on the real API since the WP 4c mini-WP);
  `POST /v1/cache/{appid}/gc` — dry run default, `{execute:true}`, 404s
  (unknown app / no depot mappings), 422s (unrecognised field / non-boolean
  `execute` — no lax-mode coercion), the documented absence of a `409` for
  an active prefill job (GC serializes on the worker in the real API, so
  there is nothing to guard against), dry-run/execute mode-scoped dedupe,
  and a completed job's `log_excerpt` being REAL `GC totals (...)` text
  `lib/gc-log-summary.js` parses correctly end to end (not a fixture string
  tailored to the parser). WP 4a.8 extends the fixtures again: Glass
  Meridian's dry-run/execute log lines now carry the FULL real key set from
  `api/vault_api/gc_execute.py`'s `GcRunReport.log_text` (`orphans`,
  `already_gone`, `dedupe_removed`, `dedupe_bytes_freed`, `problems`,
  `declined`, `depots_touched`, `needs_force_set_for`, ...), including a
  non-zero `held_back` in both modes — before this WP every demo GC scenario
  hardcoded `held_back=0 (0 bytes)`, so that branch of
  `lib/gc-log-summary.js` (and the detail sheet's "N chunks held back" note)
  was never exercised by demo mode at all. Verified the held-back bytes
  survive an execute run (a time-window rule, not something an execute run
  clears).

### WP 4a.8 — End-to-end + a11y pass

Three new files, none of them DOM-building views/components (those stay
verified live, per every WP above's posture) — these are the pure
STACK ARITHMETIC and DOM-WIRING primitives introduced or hardened by this
WP's keyboard-nav/focus-trap work, which genuinely are testable headlessly:

- `fake-dom.js` — not a test file itself, a shared minimal in-memory DOM
  shim (`createElement`/`createElementNS`/`classList`/`dataset`/
  `addEventListener`/`dispatchEvent`/`focus`/`activeElement`) used by the
  two files below. Same spirit as `store-poll-loop.test.js`'s bare-object
  fake `document`, extended just far enough to run `router.js` (touches
  `window.addEventListener` at module load) and `sheet-dialog.js`/
  `status-icon.js` (build real element/SVG subtrees) — NOT a jsdom
  replacement, see its header.
- `modal-stack.test.js` — `web/js/lib/modal-stack.js`, the new shared
  "which overlay is topmost" stack behind the focus trap both
  `onboarding.js` and `sheet-dialog.js` had deferred to this WP: pushing one
  overlay marks `#app` `inert`+`aria-hidden`; NESTING a second overlay (the
  detail sheet's own delete/GC-execute confirm dialogs open ON TOP of the
  already-open sheet) makes the FIRST one inert too, leaving only the
  topmost reachable — the mutation target named in the test is exactly "a
  plain counter instead of a stack cannot express this". Also covers
  out-of-LIFO-order popping, idempotent push, and — the load-bearing half
  found LIVE during this WP's e2e pass, not designed up front — the
  centralized Escape dispatcher: pressing Escape calls ONLY the topmost
  overlay's `onEscape`, never a lower one's. Before this dispatcher existed,
  `sheet-dialog.js` and the confirm dialogs each bound their OWN independent
  `document` keydown listener, and one Escape press fired BOTH — closing a
  GC-execute confirm AND the whole detail sheet behind it in one keystroke
  (reproduced live in the Browser pane, then pinned here as a mutation
  target: "if this dispatcher called every stacked `onEscape` instead of
  only the topmost's...").
- `dialog-wiring.test.js` — DOM-harness regression pins for the two WP 4a.7
  wiring fixes recorded in docs/WORKPACKAGES.md's Phase 4a header as due
  this WP: (1) a sheet opened via `createSheetDialog` closes when the view
  changes (`onViewChange(() => dialog.close())`, the exact one-liner every
  real sheet component uses) and returns focus to its invoker; (2) a status
  icon whose word is already shown visibly elsewhere is marked
  `aria-hidden` WITHOUT deleting its underlying `sr-only` label node (the
  "avoid double announcement" pattern `components/notifications.js`,
  `components/clients-sheet.js`, `views/downloads.js` and
  `components/game-card.js` all apply at their own call sites) — pins the
  shared primitive/contract every one of those call sites relies on, not
  each call site's own module (those stay DOM-building/verified-live per
  their existing posture). Also covers Escape-closes-the-sheet now that the
  WP 4a.8 trap is in place.

#### Real-server e2e checklist (repeatable, scripted — not a from-scratch investigation)

Per the WP 4a.7 "hang lesson" (docs/LEARNINGS.md-adjacent, recorded in this
WP's brief): keep browser sessions SHORT and SCRIPTED, prefer curl/`fetch`/
`dispatchEvent` assertions over waiting on animations or racing timers.
Two independent passes, both re-run for this WP and safe to re-run for any
future one:

**A. Live vault-api (uvicorn, temp DB + cache dir, no Docker needed):**

```
cd api
VAULT_API_KEY=<any string> \
VAULT_DB_PATH=<scratch>/vault.db \
VAULT_CACHE_ROOT=<scratch>/cache \
VAULT_WEB_DIR=<repo>/web \
python -m uvicorn vault_api.main:create_app --factory --host 127.0.0.1 --port 8123
```

Then, with `curl -H "X-Api-Key: <key>"`:

1. `GET /` and `GET /library` both 200 (SPA fallback serves `index.html`);
   `GET /css/theme.css` 200 (static asset mount); `GET /v1/games` with no
   key 401.
2. `GET /v1/settings`, `PATCH /v1/settings` (e.g. `vault_name`), re-`GET`
   confirms the `db`-sourced override round-trips.
3. `GET /v1/steam/owned-games?steamid=...` with no relay key configured ->
   409 (the relay's honest "not configured" branch); `PUT /v1/steam/key`
   with a malformed value -> 422.
4. `POST /v1/prefill {"appids":[...]}` with no `VAULT_STEAMPREFILL_PATH`
   configured -> the job is accepted (202-shaped body), the worker picks it
   up, and `GET /v1/jobs/{id}` settles to `status: "error"` with a clear
   `log_excerpt` diagnostic — **this error path itself is the valid e2e
   assertion**: an honest, actionable message, no crash, `GET /v1/health`
   still 200 afterwards.
5. Then drive the SAME server from the Browser pane: onboarding step 1
   "Test connection" against the real key, keyboard-only card open
   (`dispatchEvent(new KeyboardEvent("keydown", {key:"Enter"}))` — more
   reliable in this harness than an OS-level key press racing element
   focus, see the coder's report), confirm `#app[inert]`/`aria-hidden`
   while the sheet is open and Escape restores both.

**B. Demo mode (`localStorage.setItem("steamvault.demoMode","1")`, reload)
— no server needed, exercises the richer multi-game/GC/bypass fixtures:**

1. Onboarding overlay's `#app` inert/focus-trap (first-run).
2. Library: keyboard-open a card, bulk multi-select (`contextmenu` event ->
   select mode -> click a second card -> bulk bar), bulk delete confirm
   (nested-overlay-free — this one IS the only modal), Keep/Delete.
3. Detail sheet: GC dry run -> plan shown -> Execute -> the SECOND,
   NESTED confirm dialog opens ON TOP of the sheet — verify `#app` inert,
   the SHEET (now second-from-top) also inert, only the confirm reachable,
   Escape closes only the confirm (not the sheet), a second Escape then
   closes the sheet.
4. Delete-from-cache confirm: same nested-overlay checks.
5. Bypass banner -> Details -> clients sheet -> Escape -> banner still
   there (Dismiss is a separate, explicit action).
6. Notification bell: badge clears on open, tapping a `job_finished` row
   navigates to Downloads with that job's history row pre-expanded.

Both passes were run for this WP; see the coder's report for what they
found (a null-`name` fallback bug in `game-card.js` only reachable against
a real, un-Steam-linked server; the `[hidden]`-vs-author-`display` CSS
cascade bug on `.btn`/`.onbnav`; the library.js confirm-dialog-nested-
inside-`#app` bug; the Escape-closes-both-overlays bug — none of which a
demo-mode-only or unit-test-only pass would have surfaced).

#### Honest list of what this pass could NOT verify

Everything below needs a real device/OS or a real Steam-facing network path
— the Browser pane and `node --test` cannot exercise them, and no amount of
additional scripting in this harness closes the gap. Left for the Zeus/
Android session:

- **A real OS-level `prefers-reduced-motion` toggle.** This WP verified
  reduced motion by reading the CSS cascade (theme.css's `!important`
  block wins over every `animation`/`transition` declaration in the
  stylesheet, confirmed by grep — no competing `!important` exists) and,
  after the review fix, by reading `web/js/views/downloads.js`'s
  `prefersReducedMotion()` source — never by actually flipping the setting
  in a real OS and watching the status-icon animations/scroll actually stop.
  The Browser pane exposes no `prefers-reduced-motion` emulation.
- **Real Steam CDN cover art, and its offline/blocked-host fallback, from
  an actual phone.** `lib/cover-art.js`'s CDN URL and the procedural
  fallback tile (`img.addEventListener("error", () => img.remove())` in
  `game-card.js`) were verified against the live vault-api on a desktop
  Browser-pane session with working internet — not on a phone on a LAN
  where the CDN might be genuinely unreachable (the real "offline" case
  this fallback exists for).
- **Demo mode as an actual FIRST-RUN experience** — walked through in this
  WP via `localStorage.setItem("steamvault.demoMode","1")` set directly,
  never by tapping "Skip for now — browse in demo mode" from a truly fresh
  onboarding overlay with no prior app state at all (browser profile,
  service workers, etc.) the way a real first-time visitor would arrive at
  it.
- Real screen reader (NVDA/JAWS/VoiceOver) AUDIO verification — this WP
  confirmed the DOM semantics (`aria-hidden`, `aria-label`, `aria-pressed`,
  `role`, focus targets, and `inert`'s spec-mandated AT-hiding behaviour)
  are correct, never that a real screen reader actually announces them the
  way intended.
- Real touch/long-press timing (the 420 ms press-and-hold that enters
  multi-select) on an actual touchscreen — simulated here via a synthetic
  `contextmenu` event, never a real touch sequence.
- `inert` attribute support outside Chromium (the Browser pane is
  Chromium-based; `inert` is Baseline-widely-available but was not
  cross-checked on Firefox/Safari).
- GC execute against a REAL depot cache with real chunk files on disk —
  this WP's live-server pass only reached the `POST /v1/prefill` honest-
  error path (no `VAULT_STEAMPREFILL_PATH` configured, so no real
  download/cache content ever existed to run GC against); the GC flow
  itself was only exercised through demo mode's simulated model.
- Real multi-client bypass detection (demo fixtures only — no second real
  vault-agent on the LAN during this pass).
- Performance/rendering with a real, large (400+ game) library — the live
  server had exactly one game; demo mode has six.
