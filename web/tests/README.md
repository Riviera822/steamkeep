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
  notification). Extended (WP 4e.6) with the fourth, `keyFn`-less "cache"
  resource: its tick payload shape (`{item}`, never a `{diff}` key),
  `store.snapshot("cache")`'s undefined-before-first-poll/preserved-on-
  failure behaviour, and its own B1-style in-flight/nudge race replay — all
  fake `apiClient` objects in this file gained a `cacheSummary` stub so the
  new loop does not error on every `store.start()` call this file already
  made before this WP existed.
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
  null/zero/negative/non-finite input. Gained `formatBytesGBOrZero` coverage
  (WP 4e.6, rail foot): the deliberately OPPOSITE zero rule from
  `formatBytesGB` — a genuine zero renders (`"0.0 GB"`), only
  null/undefined/negative/non-finite input stays `null`.
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
  replacement, see its header. Extended (WP 4e.6, Opus review should-fix
  S3) with `FakeElement.replaceChildren` for `rail-panel-wiring.test.js`.
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

### WP 4c-web — "Check & update all cached games" (Phase 4c)

Same posture as every prior WP: the DOM-building trigger itself
(`views/library.js`'s new header button) is not unit-tested directly — the
mixed-outcome wording, the forced-run heads-up composition, the mid-loop-5xx
recovery signal, and the in-flight button lock are pulled into a DOM-free
`web/js/lib/cached-prefill-outcome.js` and tested here, plus a demo-mode
extension for the new route. **Round 1 review (Opus): FAIL — one blocker,
four should-fixes, all fixed in this round** — see the inline notes below
for what changed and how each fix was mutation-verified.

- `cached-prefill-outcome.test.js` — `web/js/lib/cached-prefill-outcome.js`:
  - `partitionCachedPrefillOutcome`: sorts a `POST /v1/prefill/cached`
    response into `queued`/`alreadyQueued`/`alreadyRunning`/`alreadyPaused`
    regardless of input order; empty/non-array input treated as empty; a
    deduplicated entry with an unexpected non-`queued`/non-`paused`
    in-flight status still lands in `alreadyRunning` rather than vanishing
    (same "unknown routes somewhere honest, never oblivion" posture as
    `job-partition.js`'s status handling). **S1 (round 1 should-fix):**
    `alreadyQueued` is its OWN bucket, not folded into `alreadyRunning` —
    `enqueue_prefill` returns an existing job with ITS OWN status, and a
    double-press before the single worker claims anything is the COMMON
    case, not an edge case; mutation-verified by folding the `queued`
    branch back into `alreadyRunning` and watching 4 tests fail by name.
  - `summarizeCachedPrefillOutcome` — the mutation-worthy pins the WP brief
    named, each verified by reverting the fix and watching the named test
    fail, then restoring it: (1) *"a paused dedupe is NEVER worded as
    queued/started"* — wording the paused count with "queued for
    check & update" instead of "paused — resume or cancel..." fails this
    test (and others) by name; (2) *"empty selection reads as a normal
    outcome, not a failure"* — wording the empty case as a failure fails
    this test by name. **S1:** a `queued`-status dedupe is worded
    "N already queued", distinct from `alreadyRunning`'s "N already in
    progress" — a job still waiting in the FIFO queue is not "in progress".
    **S2 (round 1 should-fix):** every string carries the full "check &
    update" wording ("N queued for check & update", not "N queued for
    checking") — the plan's honesty rule applies to the whole action's
    language, not just the button label. **Blocker (round 1, live-
    reproduced in headless Chrome): the forced-run note used to be composed
    by the CALLER from its own `GET /v1/games` snapshot, unconditionally —
    "Nothing cached to check. (1 forced...)" claimed work that provably did
    not start.** Composition now lives entirely in this function, gated on
    `partition.queued.length > 0` and scoped to ONLY those appids
    (`countForcedCachedGames`'s new `queuedRefs` parameter, never the whole
    snapshot) — two named regression tests (`BLOCKER REGRESSION: empty
    response + a stale needs_force game...`, `BLOCKER REGRESSION:
    all-deduplicated response + a forced game...`) pin both shapes,
    mutation-verified by reverting the gate/scoping to the exact pre-fix
    behaviour and watching both reproduce the reviewer's literal string
    (`'Nothing cached to check. (1 forced — ...)'`) before being restored.
  - `describeCachedPrefillError` — the mid-loop-5xx honesty rule
    (api/README.md: each app is enqueued in its own committed transaction,
    so a `5xx` partway through can leave earlier apps durably `queued`):
    only `ERROR_KINDS.SERVER` sets `refresh: true` (the signal `library.js`
    maps to `store.refreshNow()`); every other kind (401, 422/409-folded
    `validation`, network, `not_found`, `unknown`) does not force a
    refresh, since those genuinely mean nothing was queued by that call.
    Prefers the server's `detail` text when present, never throws on a
    non-`ApiError` input.
  - `countForcedCachedGames(queuedRefs, games)` — **re-scoped in round 1**
    from a whole-`games`-snapshot count to only the appids present in
    `queuedRefs` (the `queued` bucket) that also carry `needs_force: true`
    in `games`; an appid in `queuedRefs` with no matching `games` entry is
    not counted (fails safe, never guesses).
  - `createCheckAndUpdateAction` — the in-flight guard, DOM-free by
    construction: a `run()` call while a manually-gated fetcher's promise
    is still pending returns `{skipped: true}` WITHOUT invoking the fetcher
    a second time; a `run()` after the previous one settles (success OR
    rejection) calls the fetcher again; a rejected fetch still clears the
    in-flight flag so the guard is usable immediately afterward. **S4
    (round 1 should-fix): the no-op assertion is now SYNCHRONOUS** —
    asserted on the fetcher's call count immediately after issuing both
    `run()` calls, before either promise is awaited or the deferred fetch
    is resolved. The original version awaited the second `run()` first,
    so a broken guard (removed `if (inFlight)` check) made that `await`
    hang on the SAME never-yet-resolved deferred instead of failing —
    mutation-verified: removing the guard now fails this test in under 1ms
    (`calls === 1` assertion, `2 !== 1`) with no `--test-timeout` needed.
- `demo-data-cached-prefill.test.js` extends `demo-data.js`'s coverage with
  the new `POST /v1/prefill/cached` route: selects every game whose `depots`
  array is non-empty (this demo model's stand-in for "has cache content on
  disk", `makeGame()`'s header), sorted ascending, excluding the one seed
  game with no depots at all; response shape matches `PrefillJobRef`
  exactly; a brand-new job dedupes `false`; the seed data's already-`running`
  job dedupes onto itself with no second job created; pausing that job first
  and re-calling the route dedupes onto it with `status: "paused"` — the job
  itself stays paused afterward, proving this route never resumes it.
  **S1 fixture (round 1):** pausing then RESUMING that job (a real,
  reachable sequence — `POST /v1/jobs/{id}/resume` genuinely returns
  `status: "queued"`, api/README.md "Job control") then re-calling the
  route dedupes onto it with `status: "queued"`, exercising the
  `alreadyQueued` bucket end to end through the actual demo route rather
  than only via hand-built fixtures. Also: any request body (including a
  bogus `{"appids": [...]}`) is silently ignored, never read as an explicit
  id list; the empty-selection case after clearing every game's cache
  (reusing the shared-depot two-call dance `demo-data.test.js` already
  exercises) returns `[]`; and that this route shares the exact SAME
  per-appid enqueue helper `POST /v1/prefill` uses (`enqueuePrefillForAppid`,
  extracted from that route in this WP) rather than a second,
  potentially-drifting enqueue mechanism. **N1/N2 (round 1 nitpicks,
  documented in the test file's header, not fixed — both pre-existing,
  neither a two-line change):** a brand-new demo job flips straight to
  `"running"` on creation (the real contract allows `deduplicated: false`
  to arrive as `"queued"`, which this demo model never produces at that
  moment); demo selection keys on `depots.length > 0` while the real grid
  keys on `size_bytes > 0` (agree on every current fixture, but are not the
  same predicate).

### WP 4e.1 — Desktop layout foundation (Phase 4e)

Different posture from every WP above: this package's product is mostly
CSS/breakpoint plumbing, not JS decision logic, so its tests are not
"pull the logic into `lib/`" ports — they are (a) structural static analysis
of the CSS/JS source text itself, and (b) fixture-generation logic that
genuinely is pure and DOM-free.

- `css-hygiene.test.js` — two general-purpose lints, real static analysis
  (re-derived from source on every run, not a hand-typed list):
  1. every CSS class carrying an author `display` rule that is ALSO
     hidden-toggled somewhere in `web/js/` must have a matching
     `SELECTOR[hidden]{display:none}` guard — closes the `.btn`/`h4.sec`/
     `.onbnav` bug class (docs/LEARNINGS.md, "Web UI") permanently, catching
     a NEW instance built the same way, not just re-verifying the three
     known ones. Recognizes `.hidden = ...`, `setAttribute("hidden", ...)`
     and `removeAttribute("hidden")` as toggle sites (the first version of
     this lint only recognized the property form — an Opus review nitpick,
     N1, found and closed in the fix round: a `setAttribute`-based toggle on
     an unguarded class survived undetected until then). Documented
     limitations (same review, same header): a multi-class compound selector
     (`.jobcard.active`) or a descendant selector (`.grid.list .cap`) is
     invisible to the display-rule side even if JS hidden-toggles a matching
     element; the JS-side scan is per-FILE and flat, not block/function
     scoped, so two same-named identifiers in different functions of one
     file share one tracked class set. Neither is exploited by real code as
     of this WP.
  2. no `!important` anywhere except theme.css's `prefers-reduced-motion`
     block — in particular, none inside a NEW `@media (min-width...)`
     breakpoint block, which is exactly the kind of specificity workaround
     that makes later overrides unpredictable.
  Both mutation-verified in the fix round: removing a guard, adding an
  `!important` to a breakpoint block, and (new in the fix round) a
  `setAttribute`-based toggle with no guard at all — each kills its named
  test; reverted afterward.
- `css-layout-foundation.test.js` — structural pins for the spatial tokens,
  breakpoints (BP-M/BP-L/BP-XL) and the nav-to-rail conversion. Two
  anti-regression pins added in the Opus review fix round, both mutation-
  verified: `--w-wall` must equal `960px` in EVERY breakpoint block (a first
  version of this package widened it, which measurably made the mockup's
  already-oversized cover tile BIGGER — see docs/PROJECT_PLAN.md's Phase 4e
  section for the full story); every `--nav-h` assignment must carry an
  explicit `px` unit (blocker B1: a bare `--nav-h:0` makes `.bulk`'s
  `bottom:calc(var(--nav-h) + 14px)` a calc() type mismatch, invalid at
  computed-value time, silently falling back to `bottom:auto` — the bulk
  action bar landed off screen at every width >=1024px until this was
  caught). Also pins the BP-L `.nav-pip` `order:1` fix (blocker B2: DOM-first
  no longer means visually-first once the pip becomes `position:static` in
  a row-direction flex container) and `.banner-wrap` picking up `--w-wall`
  (not `--w-text`) at BP-L so it aligns with `.view-root` in the same visual
  column (should-fix S4). **Correction (Opus review blocker B3):** this
  file's own header used to claim "physically impossible for this WP to
  change anything below BP-M" — false: `.banner-wrap{width:100%}` (the
  shrink-to-fit bug fix) is a deliberate TOP-LEVEL rule change that alters
  real rendering from ~430px up to 719px (measured against a pre-WP
  baseline — see the header for the numbers). Only the mockup's own 390px is
  genuinely byte-identical end to end; "base is untouched" in this file's
  test names means the SHELL/RAIL/BREAKPOINT machinery is correctly gated,
  not that literally nothing renders differently below 720px.
- `demo-data-large-library.test.js` — `web/js/demo-data.js`'s
  `generateSyntheticGames`/`resetDemoData({librarySize})`: deterministic
  (same count -> same shape, modulo the one wall-clock-derived field,
  `last_prefill_at`, whose exact ISO string a millisecond apart is expected
  to differ and is stripped before the determinism comparison — its SHAPE
  is pinned separately: a real ISO string for "done" games, exactly `null`
  for "idle" ones, per N4), appid ranges clear of every other fixture,
  mixed cached/idle shape, and (Opus review should-fix S1) `needs_force`
  is the exact inverse of "cached" — an idle/never-filled row is
  `needs_force=true` and a done row is `false`, matching api/README.md's
  real lifecycle exactly (the pre-fix default produced `idle` +
  `needs_force=false`, a shape the real API can never emit).
- `rail-content.test.js` (WP 4e.6) — `web/js/lib/rail-content.js`'s three
  pure presentation functions, no DOM: `vaultNameFromSettings` (a real
  effective value trims and returns; a malformed response/missing entry/
  empty-after-trim value are all `null`); `cacheFootFromSummary` — the
  headline **unknown-vs-zero** guarantee: no summary yet or a malformed one
  is `null`, but a GENUINE zero (`total_bytes:0`, an empty cache;
  `free_disk_bytes:0`, the disk is full) renders as a real `"0.0 GB"`, never
  collapsed into "unknown" the way the tile-badge helper `formatBytesGB`
  deliberately does for the same input shape (two named mutation targets:
  swapping the module's `formatBytesGBOrZero` import back to `formatBytesGB`
  kills BOTH the total_bytes and free_disk_bytes zero pins); and
  `versionFromSettings` (coordinator addition mid-WP, confirmed shape from
  the parallel WP 4e.7 `api/` package: a top-level `server_version` string,
  sibling of `readonly`) — absent/non-string/empty are all `null`, a real
  value is trimmed and `v`-prefixed (not double-prefixed if already
  `v`/`V`-prefixed), and a pathologically long value is clamped with a
  trailing ellipsis (mutation target: returning `""` instead of `null` for
  the absent case kills the exact-null pin by name).
- `demo-data-settings.test.js` gained a pin (WP 4e.6/4e.7) for
  `GET /v1/settings`'s new `server_version` field: a top-level string
  sibling of `readonly`, never a row inside `settings` (matching the real
  endpoint's shape — the field has no source precedence and `PATCH` rejects
  it as an unrecognised key).
- `rail-panel-wiring.test.js` (WP 4e.6, Opus review should-fix S3, new) —
  `web/js/components/rail-panel.js`'s DOM-wiring, refactored into a
  dependency-injected `createRailPanel()` factory specifically so this file
  could exist (the same `store.js`/`store-singleton.js` split, applied to a
  component for the first time; `app.js` now performs the one real,
  side-effecting call this file used to make on import). Drives it with
  `fake-dom.js` `FakeElement`s and trivial fake store/api objects — no real
  `document`, `store-singleton.js`, or network. Covers: the two mutations
  that used to pass the full suite silently (a fabricated `"Free null"` row
  when `free_disk_bytes` is `null`; a poll failure blanking the rail
  instead of leaving the last real number on screen); the initial-snapshot
  paint (mirrors `bypass-banner.js`'s own pattern); `.rail-head`/
  `.rail-foot`/`#rail-version` all hidden when they have nothing to show
  (should-fix S4), including the AND-not-OR regression test for
  `.rail-foot`'s combined visibility rule; and the settings-fetch gating
  (skipped with no key and demo mode off, runs otherwise, never throws on
  rejection).

#### Honest list of what WP 4e.1's pass did NOT catch on its own — and what closed the gap

**This is the load-bearing lesson of this package's review round, not a
footnote.** The first live-verification pass measured exactly three
selectors — the cover tile, `.view-root`, and the nav rail's own box — and
concluded the shell conversion was safe. It was not: `#app` becoming a CSS
grid changes nothing about `position:fixed` containing blocks by itself
(that theory, floated during review, turned out not to be the actual
mechanism), but two REAL bugs existed anyway and were invisible to that
narrow a check:

- **B1** — the bulk action bar (`.bulk`, `position:fixed`) computed to
  `bottom:-143782px` (off screen) at every width >=1024px, because
  `--nav-h:0` (unitless) made `.bulk`'s `calc(var(--nav-h) + 14px)` invalid
  at computed-value time. Caught only by explicitly measuring `.bulk` itself,
  in multi-select mode, at desktop widths — not implied by checking the tile
  or the nav.
- **B2** — the Downloads nav button's queue-count pip visually preceded its
  own icon at BP-L (DOM order, not visual order, once the pip became
  `position:static`), throwing the whole row ~88px out of line with
  Library/Settings. Caught only by measuring icon/label x-position with an
  ACTIVE JOB running — a library at rest never shows the pip at all.

**The fix, going forward:** any package that changes the app shell's
layout mode (flex<->grid, adding/removing a rail/sidebar, repositioning the
nav) must explicitly re-check every OTHER `position:fixed`/overlay surface
for "does it still land on screen" — `.bulk`, `.sheet-backdrop` (the
notifications panel, the clients sheet, the game detail sheet all share
this), `.dialog-backdrop` (delete/GC-execute confirms), and `#toast` — not
just the surfaces the package's own diff obviously touches. Not their
GEOMETRY/sizing at the new breakpoints (that is later, narrower Phase 4e
work) — just "is it reachable and visible at all". WP 4e.1's fix round did
this for all four listed surfaces at 1024/1280/1920px in multi-select with
an active job (the exact conditions that exposed B1/B2) and found no
further issues; see the coder's report for the live measurements. This
check has no headless equivalent in this suite (it is fundamentally a
real-rendering/layout-engine question, same posture as every other item in
WP 4a.8's "Honest list" above) — it must be repeated by hand for every
future shell-layout-changing package, not assumed safe from CSS pins alone.

### WP 4e.2 — Auto-fill library grid + toolbar band (Phase 4e, D-3/D-4)

Same posture as WP 4e.1: CSS/breakpoint plumbing, not JS decision logic, so
the new pins are structural static analysis of the source text, plus one
small JS relabeling change with no headless test of its own (nothing in
this suite exercises `library.js`'s DOM against jsdom — see the top of this
file; the label strings are verified live, in the running app, alongside
the geometry claims below).

- `css-layout-foundation.test.js` — the two WP 4e.1 anti-regression pins
  that asserted `--w-wall` stays flat at 960px everywhere are UPDATED (not
  deleted, per the brief's own instruction) to the new, intentional
  three-value progression this package ships (960px base -> 1600px BP-L ->
  2000px BP-XL), pinned BY POSITION so a future reordering or drop still
  fails loudly. First-pass new tests: the auto-fill wiring itself (`.grid`/
  `.grid.cols3` both `repeat(auto-fill,minmax(var(--tile-min),1fr))` with
  DIFFERENT `--tile-min` values, `.grid.list` deliberately left with no
  BP-L override at all — relying on CSS specificity over the plain `.grid`
  rule, asserted structurally rather than assumed); `.bulk`'s BP-L override
  re-derived from `--w-wall`/`--rail-w`/`--gutter` instead of `--w-text`;
  `.view-library`'s BP-L named-area grid assigning all six in-flow children
  BY CLASS (`.view-library > .search` etc. — never `nth-child`); `--search-w`
  declared in theme.css. One trap found and worked around while writing the
  `.grid.cols3` pin: this file's own `ruleBody()` helper does a plain
  `indexOf(selector + "{")`, and the combined selector `.grid, .grid.cols3{
  ...}` rule (needed so both share one `grid-template-columns` formula)
  already CONTAINS the substring `.grid.cols3{` right after the comma —
  `ruleBody(block.body, ".grid.cols3")` would silently return THAT rule's
  body instead of the intended standalone `--tile-min` override a few lines
  below it. Worked around with a literal substring check on the exact
  standalone rule text rather than a "preceded by a non-comma boundary"
  regex (tried first — whitespace precedes both the comma-list's embedded
  occurrence AND the standalone rule, so a boundary class that includes
  plain whitespace cannot tell them apart either).

  **Opus review round 1: FAIL — one blocker, five should-fixes, all fixed
  and re-verified.** The first pass's structural pins asserted mechanisms
  were DECLARED without asserting they were WIRED to the claimed VALUE —
  four real mutations survived 456/456 as a result, all now killed by name:
  (1) switching the BASE (phone) `.grid`/`.grid.cols3` rules to `auto-fill`
  — the mockup-frozen surface the file's own header claims is untouched;
  (2) deleting the BP-L `.grid.cols3` reset entirely — the "fix the tile
  guarantee made concrete, not merely asserted" comment right above it in
  app.css; (3) changing `.view-library`'s `grid-template-columns` from
  `var(--search-w) 1fr` to `1fr 1fr` (the cap silently vanishes); (4)
  swapping the area map's `"search chips"` row to `"chips search"`. Two new
  tests close (1)/(2): a `.grid`/`.grid.cols3` base-rule pin (extending the
  house pattern this file already used for `.nav`/`.chips`/`.app`) and a
  literal restatement check of the seven BP-L reset rules (the file
  originally shipped eight — a review nitpick found the eighth,
  `.grid.cols3 .meta .size`, was restating a value already inherited from
  the `.meta` reset one line above it, so it was removed rather than
  pinned; the test asserts its absence explicitly so a "ninth rule quietly
  missing" reading is impossible). (3)/(4) are closed by extending the
  existing `.view-library` test with a `grid-template-columns` value
  assertion and five literal area-row checks (head/check/search+chips/
  cards/hint, in that exact order).

  The two `--w-wall`-progression pins also carried a **wrong rationale**
  (should-fix S2): the fix-round comment claimed BP-L's 1600px "lands at
  ~186px @1920, capped" — false, since 1920px falls in BP-XL's own range,
  where nothing is capped by the BP-L value at all (BP-L's widest possible
  viewport is 1799px, whose main column, 1567px, never reaches 1600px in
  the first place). Both the CSS comment and this test's assertion message
  are corrected to state the real story: BP-L's cap is a deliberate guard
  against a future `--rail-w`/breakpoint change, not an active constraint
  today. `--tile-min`'s pinned value also changed, 210px -> 176px
  (Comfortable) and 168px -> 150px (Compact) — the operator's decision
  after a live measurement showed 210px producing tiles up to 1.42x the
  mockup's own tile size (should-fix S1; see theme.css's `--tile-min`
  comment for the `minmax(F,1fr)` overshoot mechanism this number has to
  respect, and the sawtooth-not-smoothed decision that comes with it).

  **Blocker B1**, pinned here too even though the fix lives in app.css: a
  dedicated test asserts `.empty{grid-column:1/-1}` — see the app.css
  section below for the bug itself.
- `css-hygiene.test.js` — the display/hidden cross-reference lint gains a
  fourth toggle idiom, `el.toggleAttribute("hidden", cond)` (and the bare
  `el.toggleAttribute("hidden")` form) — the brief that shipped the
  `setAttribute`/`removeAttribute` pair in WP 4e.1 explicitly named this as
  the next gap. No file in `web/js/` uses it yet, so — unlike the sanity
  test that exercises the other three forms against the real `.btn`/
  `h4.sec`/`.onbnav` offenders — this is pinned against a throwaway fixture
  file written to and removed from `web/tests/` inside the single test that
  needs it (never `web/js/`, so it can never leak into the real scan),
  proving the new regex recognizes both the two-argument and bare forms.
  **Opus review nitpick, fix round:** all three attribute-based regexes
  (`setAttribute`/`removeAttribute`/`toggleAttribute`) were case-SENSITIVE —
  `toggleAttribute("HIDDEN", true)`/`setAttribute("Hidden", "")` both
  genuinely set the `hidden` attribute in a real DOM (HTML attribute names
  are case-insensitive by spec) and both survived the lint silently. Fixed
  with an `i` flag on all three, pinned by a second fixture test exercising
  the reviewer's own mixed-case spellings.

  **Opus review round 2: FAIL — one blocker (B2), plus S6/S7 and further
  nitpicks, all fixed and re-verified.**

  **B2** was round 1's OWN should-fix S1 sentence, still wrong after being
  "fixed" once: it claimed 176px keeps the range at "~176-205px (mockup
  ±18%)", which is wrong on both the range and the percentage (a
  4px-resolution sweep over 396 widths — 1024-2600px plus 3440px, both
  densities — measured the real range: 177-222px, 1.02x-1.29x the mockup
  tile; 220/173 is +27%, not ±18%) and directly self-contradicted by this
  same comment's OWN sawtooth example ten lines below (220.0px, sitting
  under the 205px ceiling the sentence right above it had just asserted).
  Corrected in `theme.css` with the clause a bare number-fix would have
  omitted: a <=205px ceiling at every width is unachievable with
  `minmax(F,1fr)` at all — it would need F<=158px, already below Compact's
  own 150px floor — so this was never a value the token failed to find.

  **S6 (operator decision):** narrowing the two floors to 176px/150px (to
  keep the corrected S1 range honest) made Comfortable and Compact render
  IDENTICALLY — same column count, same tile width within 0.2px — across
  several sub-1400px bands (1024-1076, 1208-1238, ~1396px up). Accepted and
  documented in `theme.css` and the plan's D-3 entry rather than narrowing
  Compact further (a ~135px floor to force visible separation would sit 22%
  below the mockup's design size, with no measurement behind it).

  **S7:** the round-1 B1 pin (`.empty{grid-column:1/-1}`, below) is
  class-specific — a hypothetical future class appended into `.grid` with
  no `grid-column` rule would reproduce the identical bug silently.
  Generalised into a new test scanning `renderGrid()` for
  `els.grid.appendChild(<call>)` sites, resolving each callee (excluding
  `buildCard`, the grid's actual content) to its function definition in the
  same file, and collecting every literal `className = "..."` it assigns.
  Two classes exist today (`.noresult`, `.empty`) and both are already
  correct — this closes the CLASS of bug, not just the one instance.
  Mutation-verified against a synthetic new class (`libnotice`) inserted
  into a copy of `library.js` and confirmed to fail the test by name, then
  reverted.

  Further nitpicks: the hidden-toggle lint's `.hidden = ` and
  `toggleAttribute(` regexes were still one character away from two more
  real idioms the reviewer's own probe found surviving — `el.hidden ||=
  true` / `el.hidden ??= true` (the literal `\s*=` required the `=`
  immediately, but `||=`/`??=` put extra characters in that gap) and
  `el.toggleAttribute?.("hidden", true)` (optional chaining put `?.`
  between the identifier and the call parenthesis). Both closed with a
  small character class each, pinned against a new fixture test exercising
  exactly those three forms, mutation-verified by reverting each regex in
  isolation and watching the test fail by name. `Object.assign(el,
  {hidden:true})` remains genuinely out of reach for a forward regex scan
  (the identifier and the property name never sit next to each other in
  the source) — documented as an acknowledged gap in the file's header
  rather than silently implied covered by the four idioms actually scanned
  for.

#### app.css (B1, blocker — Opus review round 1; corrected round 2)

`.empty` (the "no results" fallback `<p>`, appended as a direct child of
`.grid` by `library.js`'s `renderEmptyState`) had no `grid-column` rule,
unlike `.noresult` right above it in the same file. Under `.grid`'s BP-L
`auto-fill` rule, `.empty` collapsed into a single auto-fill track instead
of spanning the row — measured live: an 8.5%-of-row-wide block hard against
the left edge at 2560px. Reachable one click away in practice, not a
corner case: `renderChips` renders every filter chip including zero-count
ones, so a library with zero failed downloads still shows a clickable,
functioning "Failed 0" chip. Fixed with `grid-column:1/-1`, and re-verified
live via the exact reproduction: the real "Downloading 0" chip on the
400-game demo fixture now shows `.empty` measuring the grid's own full
width, not a narrow left-aligned sliver.

**This is a correction, not a new divergence (round 2):** the frozen
mockup already applies `style="grid-column:1/-1"` inline to this exact
element (`docs/design/vault-app-mockup.html:1826`), while its other two
`.empty` uses (the Downloads "no download running" line, and the
notifications-panel empty state) carry no such style — the WP 4a.3 port
dropped the inline style when translating the mockup's markup into this
stylesheet, so this restores mockup-faithful behaviour rather than
inventing new behaviour.

The fix's own "safe for every other `.empty` use" claim was also corrected
(round 2): the real inventory is ONE `emptyMessage()` caller in
`downloads.js` (its Active-section "no download running" fallback — an
earlier version of this comment said two) plus `settings.js:553`'s
`el("p", "empty", "Loading settings…")` loading state, omitted from the
earlier count entirely. Both live inside plain block containers (never
`display:grid`), where `grid-column` is simply ignored.

#### Live verification (no jsdom/browser here — done against a running vault-api + the 400-game demo fixture, per the brief)

Six-width table (390/768/1280/1440/1920/2560), both densities, plus `.bulk`
alignment in multi-select with an active+paused job, plus the fixed/overlay
surface re-check at 1024/1280/1920/2560 — see the coder's report (delivered
alongside this package) for the full numbers. Headline results, RE-measured
after the fix round with the operator's 176px/150px `--tile-min` values
(the numbers below supersede the first pass's 210px/168px-based table): the
cover tile is genuinely byte-identical to the WP 4e.1/pre-4e.1 baseline
below BP-L (173.0×259.5 at 390px, 354.5×531.8 at 768px — this package
changes NOTHING there); from BP-L up, both densities produce real,
width-derived column counts (e.g. 8×194.6px "Comfortable" / 10×153.3px
"Compact" at 1920px, 10×186px / 12×153px at the 2560px BP-XL cap); `.bulk`'s
left edge matched `.view-root`'s own content-area left edge to the pixel at
every one of 1024/1280/1440/1920/2560px, in both the `--w-wall`-capped and
uncapped regimes, reconfirmed after the tile-min change (which does not
affect `.bulk` at all — verified, not merely assumed); the toolbar band
(search capped at 420px, chips alongside) only applies from BP-L up — at
768px search and chips are still stacked, full width, byte-unaffected by
this package. A full 400-card grid rebuild (toggling density) measured
21-27ms, matching WP 4e.1's own ~29-33ms baseline within noise — column
count changing arrangement, not node count, confirmed live rather than
merely reasoned about.

Also re-measured for the S2 fix: BP-L's 1600px `--w-wall` genuinely never
binds within BP-L's own range — `cap binding?` false at 1024/1400/1700/
1799px viewports, no exception — confirming the corrected comment/test
message rather than the fix round's own first wrong claim. And for the S1
sawtooth documentation: 220.0px at a 1195px viewport (4 columns) drops to
177.6px at 1215px (5 columns), a ~19% shrink from a 20px WIDER window —
replacing the first pass's stale 210px-based example numbers, which no
longer applied once the token's value changed.

One methodology note for whoever verifies this live next: a raw
`document.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape", ...}))`
used to close the notifications sheet during this verification left `#app`
stuck `inert` (the sheet's own close path evidently expects a real,
trusted-adjacent event sequence this synthetic dispatch didn't fully
replicate) — this blocked the NEXT click in the same session (the bulk
delete button) with no console error, only "nothing happened" as the
symptom. Recovered by clearing `#app`'s `inert` property directly and
re-verified the underlying delete flow works correctly once unstuck; closing
overlays via their actual UI controls (a real click on the panel's own close
affordance, or a trusted keypress) avoids the issue entirely. Not a product
bug — recorded here because it cost real time to diagnose and the next
person driving this suite by hand should not have to rediscover it.

### WP 4e.6 — The rail narrower, and earning its width (Phase 4e)

`--rail-w` 232px -> 180px (operator verdict: "232px feels unnecessarily
large for the three things in it"), plus two new rail-content pieces (vault
name, cache used/free) and a third added mid-WP by the coordinator (server
version) — see `docs/PROJECT_PLAN.md`'s Phase 4e section and
`docs/WORKPACKAGES.md`'s D-12 for the full narrative. Test additions:
`format.test.js` (+5, `formatBytesGBOrZero`), `rail-content.test.js` (new,
+21, all three pure content functions), `store-poll-loop.test.js` (+4, the
new "cache" resource loop), `css-layout-foundation.test.js` (+3, `--rail-w`/
display-toggle/`margin-top:auto`), `demo-data-settings.test.js` (+1,
`server_version`'s shape) — 462 baseline + 34 first pass, +2 more after the
coordinator's `server_version` shape correction (an "already starts with
v" pin and a real-world-shaped value pin) = 498 green, first-round PASS.

**Opus review: PASS, no blockers — four should-fixes, all addressed, suite
now 515 green.** S1 (`css-layout-foundation.test.js` +1): the WP 4e.2
`--w-wall:1600px` comment's own "unreachable at BP-L" claim went stale the
moment THIS package narrowed `--rail-w` — at 180px the cap genuinely binds
at BP-L's own top end (measured: `.view-root` 1600px, capped, at 1799px).
Comment corrected; a new structural pin computes the same breakeven
arithmetic from the live `--rail-w`/`--w-wall` tokens so the next such
change fails a named test instead of leaving a comment stale again. S2
(`store-poll-loop.test.js` +1): the cache loop's cadence
(`intervals.gamesMs`) was completely unpinned — mutating it to
`jobsFastMs` (2s in production, 7.5x more often) survived all 498 prior
tests, since none of them gave the cache loop a cadence distinct from
every other interval. A live-timing pin (short `gamesMs`, huge everything
else) closes it — wrapped in `try`/`finally` around `store.stop()` after a
REAL hang was measured while developing it (a failing assertion skipped
cleanup, leaving a 50s timer alive and hanging the test file's exit past a
120s harness timeout with zero output, until the retry with cleanup-on-
failure came back in under 100ms). S3 (`rail-panel-wiring.test.js`, new,
+15): `rail-panel.js` had NO test coverage at all — deleting
`if (payload.error) return;` (blanks the rail on a transient poll failure)
and deleting `if (foot.freeText !== null)` (renders a literal `"Free
null"`) both passed the full suite. Fixed by refactoring `rail-panel.js`
into a dependency-injected `createRailPanel()` factory — the SAME
`store.js`/`store-singleton.js` split applied to a component for the first
time, with the real wiring call moved into `app.js` — so it can be driven
headlessly with `fake-dom.js` elements (extended with `replaceChildren`)
and trivial fake store/api stand-ins; both mutations now die by name. S4
(`rail-panel-wiring.test.js`, folded into the same new file): "render
nothing, never a placeholder" had only been applied to the TEXT inside the
rail's elements, not their wrapping containers — a default install showed
an empty `.rail-head` with a bare divider line above the nav, and
`.rail-foot` carried dead space from `#rail-version`'s own margin even with
real cache data. `headEl.hidden`/`footEl.hidden`/`versionEl.hidden` are now
toggled alongside the text (via the plain `hidden` attribute, guarded in
app.css's BP-L block against the `display:block` override each would
otherwise lose to, same cascade fix as `.btn[hidden]`/`h4.sec[hidden]`);
`.rail-foot` hides only when BOTH the cache summary AND the version line
have nothing to show, pinned as its own AND-not-OR regression test.

Nitpicks also closed: `theme.css`'s rail geometry comment corrected its
136px/169px/12px figures to the actual measured 135px/157px/21.5px (the
`.nav` `border-right:1px` was missing from the original arithmetic; the
"12px slack" figure implicitly assumed a 3-digit pip, which `api.js`'s
`jobs(limit=20)` makes unreachable); the `--tile-min` overshoot band's low
end is now stated as exactly 176px (the token's own floor, by definition of
`minmax()`, not a swept approximation that can drift again) rather than a
third slightly-wrong sampled figure; this file's own WP 4e.6 write-up
corrected "paints... before subscribing" to the actual order (subscribe
first, then paint from whatever snapshot already exists).

**A correction to this WP's OWN brief, found before any code shipped, not
after (unlike WP 4e.1/4e.2's review-round corrections).** The brief that
opened this package asserted `GET /v1/cache/summary` was "already polled by
the store's slow loop". `git log -- web/js/store.js` showed exactly one
commit (WP 4a.2) since that loop's creation, and `api.cacheSummary()` —
defined in `api.js` since the same WP — had zero call sites anywhere in
`web/js/`. Rather than either (a) silently building on a false premise or
(b) invoking the brief's own fallback ("if a piece of data is not already
in the store, say so and leave it out") and shipping a rail with only ONE
content piece, the fourth `ResourceLoop` was added to `store.js` itself —
a real endpoint, the EXISTING `ResourceLoop` class (no new race-handling
code), the EXISTING slow cadence value (`intervals.gamesMs`, not a new
number) — on the reasoning that the brief's INTENT (the operator explicitly
rejected a rail with only one content piece: "A narrower rail with nothing
else in it would answer half of what they said") outweighed a literal
reading of a fallback clause written for the case where the underlying data
genuinely has no source at all, which is not what this is.

**Unknown-vs-zero, the headline guarantee, verified in BOTH directions —
this phase has burned a review round before on testing only one direction
of a fail-closed rule (LEARNINGS "Testing discipline").** `formatBytesGB`
(WP 4a.3, the library tile badge) deliberately treats 0 the SAME as
null/negative/non-finite ("nothing to print" — a never-downloaded game
shows the icon alone). Reusing it here would have been the natural,
WRONG choice: "0 bytes free" (disk full) and "0 bytes cached" (empty vault)
are both real, DIFFERENT-from-unknown facts the rail must show, not hide
behind the same "nothing to print" the tile badge uses for a genuinely
different situation. `formatBytesGBOrZero` exists specifically to invert
that one rule while keeping every other input (null/undefined/negative/
non-finite) mapped the same way — pinned with a `formatBytesGBOrZero(0) !==
formatBytesGB(0)` assertion in `format.test.js` so the two helpers cannot
silently converge again, plus the two dedicated "GENUINE zero" tests in
`rail-content.test.js` whose mutation target (aliasing the import back to
`formatBytesGB`) is recorded in this file's own header and re-verified live
by the coder (temporarily applied, watched both tests fail by name, then
reverted).

**A poll failure is NOT treated as "unknown" — a decision the pure-function
layer alone cannot prove, because it lives in the wiring, not the data.**
`cacheFootFromSummary(null)` is unknown (correct — no summary object at
all). But `rail-panel.js`'s subscription to the store's "cache" resource
deliberately does NOT call that function with the payload's `item` when the
payload is `{error}` — it leaves the LAST successful render on screen,
matching the exact convention `bypass-banner.js`'s own "clients"
subscription already uses (`if (!Array.isArray(items)) return;`). This is
DOM-wiring logic, outside what a pure-function test can see, so it was
verified the only way available: live, in the running browser, by
importing the already-loaded `api.js`/`store-singleton.js` modules from the
console, monkey-patching `api.cacheSummary` to reject, calling
`store.refreshNow()`, and confirming `#rail-cache`'s `textContent` was
byte-identical before and after the failure, then recovered on the next
successful poll after restoring the original function. Not encoded as a
headless test (there is no DOM/component-level test file for `rail-
panel.js`, same posture as `bypass-banner.js`/`notifications.js` — see this
file's own "Scope" section for why DOM-wiring components are deliberately
left to live verification, not unit-tested directly); recorded here as the
live-verification evidence a reviewer would otherwise have to re-derive
from source reading alone.

#### Live verification (no jsdom/browser here — done against a running vault-api + the 400-game demo fixture, per the brief)

Rail geometry (Chromium, the `--ui` font stack, live measurement, not
assumed — **corrected once already, Opus review round 1 nitpick: the
FIRST pass's 136px/169px/12px figures omitted `.nav`'s own
`border-right:1px`, restated below with the real measured edges**): at
180px, `.nav`'s content box is 159px (180 - 10px padding/side - the 1px
border-right), and a `.nav-btn`'s OWN content box (inside its further 12px
padding/side) is 135px, spanning x=22 to x=157; "Downloads" (the longest
label) spans x=55-120.5 (65.5px) at the rail's 12.5px `.nav-lb` size. Since
`.nav-pip`'s `margin-left:auto` always pushes it flush against the 157px
edge regardless of digit count, the number that matters is the label-to-pip
GAP, not a "slack past the button edge" a flush-right element can never
have: 21.5px for a one-digit pip, 17.4px for the worst REACHABLE case (a
two-digit "20" — `api.js`'s `jobs(limit=20)` makes a three-digit pip
impossible, not merely unlikely). No wrap, no overlap at any of
1024/1920/2560px.

Column/tile numbers, re-measured after the narrowing (compare against WP
4e.2's own table at 232px):

| width | Comfortable (was @232px) | Compact (was @232px) |
|---|---|---|
| 1024 | 4 cols / 190.3px (n/a — untested at 232px) | — |
| 1920 | 9 cols / 177.4px (was 8 / 194.6px) | 10 cols / 158.5px (was 10 / 153.3px) |
| 2560 | 10 cols / 186px (unchanged — BP-XL's 2000px `--w-wall` cap already bound before AND after) | 12 cols / 153px (unchanged, same reason) |

The freed 52px buys a genuinely extra column at 1920px (both densities
shift), but changes NOTHING at 2560px — the cap was already binding there
at 232px, and 52 more px of available column has nowhere left to go. Not a
bug: the exact "cap binding? false/true" mechanism WP 4e.2's own report
already established, re-confirmed rather than re-derived.

`.bulk`'s Δleft/Δwidth against `.view-root`'s own content-area edges
(`left+16`/`right-16` on `.view-root`'s border-box rect): measured exactly
`0`/`0` at 1024px, 1920px, and 2560px, in multi-select, with the bulk bar's
`.22s` slide-up transition settled — confirming WP 4e.2's
`left:calc(var(--rail-w) + var(--gutter))`/`right:var(--gutter)` formula is
genuinely parametric on `--rail-w` (no formula change was needed for this
WP to ship correctly), not merely re-verified by coincidence at one width.

Base (<720px) re-confirmed byte-unaffected at 375px (mobile-emulation
preset) and 719px: `.rail-head`/`.rail-foot` both computed `display:none`,
the bottom nav's `grid-template-columns` still exactly `121px 121px 121px`
at 375px, nav height unchanged.

Rail content, all three degrade states, live: `#rail-vault-name` /
`#rail-cache` / `#rail-version` all render correctly in demo mode
(`"steamvault-demo"`, `"Used 6054 GB" "Free 466 GB"`, `"v0.1.0"` — the last
one only after the demo fixture was extended with `server_version` for 1:1
parity with the confirmed real shape); the cache-failure case is described
above (byte-identical before/after a monkey-patched rejection). The
"before the first poll" state could not be caught mid-flight in the BROWSER
(demo mode's fixture resolves too fast, sub-frame, to reliably observe the
gap from outside) — covered instead by the headless
`store.snapshot("cache")`-is-`undefined`-before-the-first-tick pin in
`store-poll-loop.test.js` plus `rail-panel.js`'s own order (subscribe FIRST,
then unconditionally paint from whatever snapshot already exists — which is
`undefined` at that point), which a source read, and now
`rail-panel-wiring.test.js`'s own headless pin, both confirm renders nothing
rather than a placeholder.

### WP 4e.3 — Overlay geometry at BP-L (Phase 4e, D-13)

The frozen mockup's overlays (`.sheet-backdrop`/`.sheet`,
`.dialog-backdrop`/`.dialog`) are a single 390px-phone-frame shape with no
responsive layer — on a >=1024px shell they rendered as a ~480px card glued
to the bottom edge, far from whatever control opened it (the operator's own
words: "strangely pressed against the bottom edge"). Operator decision (see
`docs/PROJECT_PLAN.md`'s Phase 4e section and `docs/WORKPACKAGES.md`'s D-13):
the game detail sheet becomes a CENTRED CARD at eye level; notifications and
the clients sheet become a right-edge DRAWER; mobile keeps its bottom
sheets, unchanged. `sheet-dialog.js`'s new `variant` option ("center" |
"drawer") appends a static modifier class at construction — presentation
only, `lib/modal-stack.js`'s push/pop/Escape stack is completely unaware of
it. New test file: `css-overlay-geometry.test.js` (structural CSS pins in
the same style `css-layout-foundation.test.js` established, plus one
fake-DOM behavioural pin for the nesting/Escape-ordering claim, using the
shared `fake-dom.js` harness). 524 baseline -> 539 green, first-round PASS.

**Opus review: FAIL, one blocker — found by the reviewer's own headless-
Chrome pass against a copy of the tree (screenshots at
1024/1184/1424/2544px), not by any structural pin in this file.** Every
structural claim held (13 mutations re-run and dying by name, the
`padding-left` centring measured exact — card centre == content-axis centre
to 0.0px at all four widths — presentation-only confirmed by grepping every
added line for listener/inert/focus vocabulary and finding none). **B1:**
the bulk-delete/GC-execute confirm dialogs (`.dialog-backdrop`, reused
verbatim by three call sites) still centred on the FULL viewport while the
sheet's own new centring axis moved to the content area — a live-measured
constant −90px (`--rail-w`/2) mismatch between a confirm and the card it
covers, the exact failure mode this package's own design comment had
already named for the sheet and then shipped anyway for the dialog. Fixed
with `padding-left:calc(var(--rail-w) + 22px)` on `.dialog-backdrop` at
BP-L (the `+ 22px` preserves the base rule's own uniform 22px inset, which
a bare `var(--rail-w)` would have replaced, landing 11px off); a new pin
computes both insets from the live `--rail-w` token and asserts they
resolve to the same axis rather than re-typing it as two literals that
could drift apart again — mutation-verified twice (reverting to the bare
`var(--rail-w)`, and deleting the override entirely both die by the same
named test). Suite: 539 -> 540 (one pin added).

Should-fixes, all addressed: **S1/S2** — this WP's divergence (D-13,
`docs/WORKPACKAGES.md`) and this README entry, previously missing (every
prior 4e web package shipped its divergence entry in the same commit).
**S3** — two first-pass test names claimed "byte-identical to the pre-WP
rule"; true of the shipped DIFF (purely additive, `git diff --stat` shows 0
deletions) but not of what those specific pins check — the reviewer added
an unrelated property to the base `.sheet-backdrop` rule and the suite
stayed green, since a regex-based pin asserts specific property values are
present, not that the rule contains nothing else. Renamed to "keeps every
pre-WP property value" (picked over switching to a literal rule-body
comparison, to stay consistent with `css-layout-foundation.test.js`'s
existing value-pin convention rather than adding a second, byte-snapshot
style only this file would use). **S4** — the mutation report had
mis-attributed a kill: reverting `variant:"center"` in
`game-detail-sheet.js`'s own `createSheetDialog(...)` call kills ONLY the
source-grep wiring test (section 5, "the three real overlay components
request the operator-decided variant"), never the fake-DOM nesting pin
(section 6) — that test builds its OWN `createSheetDialog({variant:
"center"})` instance directly and cannot see a regression in a different
file's call site at all. Corrected in both the coder's report and a new
comment on the nesting test stating this scope explicitly. **S6** — "slides
in from the right edge" appeared in `app.css`'s comments and two of the
three JS components; no transition/animation exists anywhere in this
codebase's overlays (verified: none of `.sheet`/`.sheet-backdrop`/
`.dialog`/`.dialog-backdrop` carry a `transition`/`animation` property, in
any state). Reworded to "appears at the right edge" — no motion added; this
package is geometry (position/shape), and a reduced-motion-guarded slide-in
transition would be a new, separately-scoped feature with its own testing
surface, not a fix to a wording bug. **N1/N2** (cheap, folded into the
existing drawer test rather than new ones): the drawer's now-full-height
top/bottom edges are flush against the viewport exactly like the (already
borderless) right edge — `border-top` dropped to match, `border-bottom`
left as the base rule's existing `none` rather than the fix round's own
first draft, which had mistakenly ADDED one; and with `.grab` hidden, the
drawer's `h2` sat 10px under the screen edge against the topbar's 14px —
matched via `.sheet--drawer .body{padding-top:14px}`.

**S5 — the operator's decision arrived in a second fix round: the detail
card widens to 680px at BP-L.** A DEDICATED token, `--w-sheet-l`
(`theme.css`), not a redefinition of the shared `--w-sheet` (which the
drawer, and every other sheet, still need at 480px) and not a literal
inside `.sheet--center` — `app.css`'s BP-L block needs both widths to
coexist at the same breakpoint, and WP 4h.3's header art is expected to
build against this same token. 680px, not the operator's own suggested
720px: chosen from the type measure of the card's own longest running
prose (the status-icon legend/depot-unknown captions, ~11.5px), which
yields ~102 characters/line at 680px against the classic 45-75-character
ideal — worse at 720/760px — so the LOW end of the operator's approved
680-760px range was the least-bad choice, not an arbitrary pick within it
(full character-per-line arithmetic in `theme.css`'s own token comment).
The plan item's "sizing and placement" is now genuinely delivered, not
merely placement; the confirm dialogs stay at their existing, deliberately
narrow `.dialog` width (420px) — "that is their job" (operator) — and the
drawer stays at `--w-sheet` (480px) for the same reason.

Three new pins for this decision (`css-overlay-geometry.test.js`):
`--w-sheet-l`'s existence and 680-760px range, `.sheet--center` sourcing
its `max-width` from `--w-sheet-l` rather than `--w-sheet`, and
`.sheet--drawer` gaining no such override at all. Mutation-tested in both
directions: deleting the token, and pointing `.sheet--center` back at
`--w-sheet`, each died by name.

Suite 543 green after both fix rounds (539 first pass + 1 B1's centring-
axis pin + 3 the width-token pair — N1/N2/S3/S4/S6 all extended or renamed
EXISTING assertions/comments rather than adding new test cases, matching
how WP 4e.2's own N5/N6 nitpicks were closed in place).

### WP 4e.4 — Pointer and keyboard interaction model (Phase 4e, D-14)

An inventory pass (every shipped view — library grid, game detail card,
notifications/clients drawers, downloads, settings, bulk bar, rail) found
the desktop shell (WP 4e.1-4e.3) still touch-first underneath: almost every
`:hover` rule was ungated (live at every width, every pointer type,
including touch — the sticky-hover risk `(hover:hover)`/`(pointer:fine)`
exists to prevent), three interactive controls sat flush against an
`overflow:hidden` ancestor that clipped the global `:focus-visible` ring,
the bulk bar kept its two buttons in the tab order while invisible, and
three surfaces hid their scrollbar with no other affordance for a mouse
user. Keyboard operability of the primary flows needed no new mechanism at
all — the grid card, the depot co-owner toggle and every delete/GC-execute
confirm were already fully wired (WP 4a.3/4a.4/4a.8) — but the composed,
NESTED sheet+confirm flow had no test of its own walking it end to end.
Full inventory and the roving-tabindex judgment call (explicitly NOT built,
and why, including its full measured cost) are recorded as D-14,
`docs/WORKPACKAGES.md`.

**Opus review round 1: FAIL — one blocker (B1), one must-fix (B2), plus a
D-14 sharpening (S1) and two cheap fixes (S2/S3), all fixed in round 2.**
B1: relocating all 11 pre-existing hover rules into one trailing block
(instead of gating each in place) inverted the cascade for five
equal-specificity state-vs-hover pairs, measured live in real Chrome —
`.iconbtn.on`, `.segs button[aria-pressed]`, `.chip[aria-pressed]`,
`.btn:disabled` (a disabled button visibly lit up under the cursor), and
`.notif.unread` each used to WIN on hover only by being written AFTER the
hover rule; moving every hover rule to the file's end flipped all five
ties. The round-1 test file's own header claimed a "moving a hover rule out
changes nothing else" pin existed — it did not; every hover assertion was
positional, never a cascade-OUTCOME check, so 22 passing tests coexisted
with all five regressions. Fixed by gating every hover rule in place, at
its exact original source position, plus six new named cascade-outcome
pins (source order for the five pairs, a higher-specificity override for
the `.nav-btn`/S2 sixth pair the review found in the ORIGINAL WP 4e.1
precedent). B2: `.bulk`'s opacity/pointer-events-only hide left its two
buttons reachable by Tab while invisible (measured: Tab from the last
library card landed on an invisible Cancel, then Download, then BODY) —
fixed with `visibility:hidden`/`.up{visibility:visible}`, composed with the
existing fade transition. S1: D-14 now states the Delete-selected button's
~590-Tab-press cost on the 394-game fixture, corrects the false "a skip
link exists" implication (`.skiplink` is onboarding's demo-mode button,
not a skip-to-content link, and appears nowhere on the library view), and
corrects stops-per-card to `>1` (measured 9 focusables for 6 demo-mode
games — action buttons are separate stops). S3: the scrollbar block's "no
layout shift" framing is corrected to scope that claim to the hover
tweaks only — an 8px scrollbar track is a real, if small, layout cost,
stated rather than hidden.

- `keyboard-pointer-model.test.js` — four structural CSS checks (same
  static-analysis posture as `css-hygiene.test.js`/`css-layout-
  foundation.test.js`, self-contained parsing rather than importing either
  file's helpers, matching this suite's existing per-file convention) plus
  a fake-DOM behavioural pin:
  1. **Hover gate.** Every `:hover` rule in `css/app.css`/`css/theme.css`
     must sit inside a media context requiring BOTH `(hover:hover)` and
     `(pointer:fine)` IN THE SAME and-chain — checked against the real tree
     (a `mediaStack` array per parsed rule, tracking enclosing `@media`
     headers) plus a synthetic mutation-proof fixture covering five shapes:
     a properly gated rule, a width-only-gated rule, a top-level rule, an
     `(any-hover: hover)` probe (a DIFFERENT, real media feature the round-1
     regex matched by bare substring — "any-**hover: hover**" — fixed by
     anchoring each feature check to its own opening parenthesis), and a
     comma-OR probe (`(hover:hover), (pointer:fine)` is EITHER-alone, not
     AND — the round-1 check ran two independent `.test()` calls against
     the whole header and would have been satisfied by either branch
     containing either substring; fixed by splitting the header on
     top-level commas first and requiring both features inside the SAME
     branch). Named pins for the pre-existing `.nav-btn:hover` precedent
     and for every new hover affordance this WP adds (`.hrow > button`,
     `.banner .acts button`, `.icnact`, `.card:hover .cap`, `.grid.list
     .card:hover`).
  2. **Cascade-outcome pins (the B1 fix, round 2's core addition).** For
     each of the five order-dependent pairs above, the STATE rule's source
     INDEX must be strictly greater than its hover rule's (the parser now
     records each rule's character offset for exactly this) — proven
     non-vacuous with a fixture asserting the check flags a
     deliberately-wrong-order pair and clears a correctly-ordered one. The
     sixth pair (`.nav-btn[aria-current="page"]` vs `.nav-btn:hover`)
     cannot use order at all — the BP-L breakpoint block the hover rule
     lives in structurally cannot move before the base rule — so it is
     checked by specificity arithmetic instead (a small selector-component
     counter) plus the override rule's own existence and `color` value.
  3. **Focus convention.** The base `:focus-visible` rule (theme.css) is a
     bare, universal selector using `var(--accent)`. A "real cross-section"
     pin (brief requirement, explicitly NOT one hand-picked example) checks
     ten interactive classes spanning every inventoried view
     (`.btn`/`.chip`/`.nav-btn`/`.card`/`.notif`/`.segs button`/
     `.depotwrap.sh .depot`/`.hrow > button`/`.iconbtn`/`.qx`) for any rule
     that suppresses `outline` entirely — none do. The one real,
     pre-existing exception (`.search input`/`.inp input`'s `outline:none`,
     substituted by a `:focus-within` border-color change on the wrapper,
     WP 4a.3/4a.6) is asserted as exactly that, by name, so it cannot be
     mistaken for something the cross-section pin should have caught.
     Named pins for the three new `outline-offset:-2px` overflow-clip
     fixes (`.segs button`, `.hrow > button`, `.depotwrap.sh .depot`), plus
     the B2 pin: `.bulk` is `visibility:hidden` while closed, `.bulk.up`
     restores `visibility:visible` — with its own mutation-proof fixture
     showing the pre-fix (opacity/pointer-events-only) shape does not
     satisfy the check.
  4. **Reduced motion.** The override is the universal wildcard `*,
     *::before, *::after` (not an enumerated list), so every hover-
     triggered transition this WP rides on (e.g. `.cap`'s pre-existing
     `transition:box-shadow`, now also triggered by the new `.card:hover
     .cap` rule, and `.bulk`'s new `visibility` transition) is covered by
     construction — pinned by asserting the wildcard itself, plus a
     synthetic fixture proving a NARROWED selector provably would not cover
     an arbitrary class the way the wildcard does.
  5. **Keyboard flow (fake-DOM).** Four tests walking the sheet +
     nested-confirm flow via `click` (standing in for native Enter/Space
     activation of a real `<button>` — a browser guarantee this harness
     does not re-test) and `keydown` `Escape`/`Tab` events only, using the
     shared `fake-dom.js` harness (`createFakeDom`/`fakeKeyEvent`/
     `fakeClickEvent`) already established by `dialog-wiring.test.js`/
     `modal-stack.test.js`. A confirm dialog is hand-built to the EXACT
     shape every real one in this codebase uses (`document.body`-level
     sibling, `pushModal`/`popModal`, safe-default focus on open, invoker
     focus restored on close) rather than importing `game-detail-sheet.js`
     directly — that module is a singleton DOM-building component this
     codebase deliberately does not unit-test (pulls in `store-
     singleton.js`/`api.js`, which need a real fetch-capable environment).
     Covers: the confirm receiving focus on its safe default (Keep) when
     opened on top of an already-open sheet; Escape unwinding INNER-first
     (first Escape closes only the confirm, restoring focus to the sheet's
     own Delete button; second Escape then closes the sheet, restoring
     focus to the original invoker); a Tab keydown never reaching the
     centralized Escape dispatcher at ANY stack depth (extends `modal-
     stack.test.js`'s single-overlay "Enter never triggers onEscape" check
     to the nested case, with the specific key — Tab — native focus order
     depends on staying uncaptured); and that closing the confirm via a
     real click on its own button restores focus identically to Escape.
     (Round 1's file also imported `router.js`'s `onViewChange` unused —
     dropped in round 2, a reviewer nitpick.)

  543 baseline → 565 green round 1 (undetected B1) → **574 green round 2**.
  Mutations applied to the REAL files and reverted, each dying by the exact
  name below:
  - Moving `.chip:hover` out of its in-place gate back to a top-level rule
    → **"real tree: every :hover rule in app.css/theme.css is gated behind
    (hover:hover) and (pointer:fine)"**.
  - Deleting `.hrow > button:focus-visible{ outline-offset:-2px }` →
    **"named pin: .hrow > button:focus-visible{ outline-offset:-2px }
    exists (overflow-clipped ancestor fix)"**.
  - Narrowing `theme.css`'s reduced-motion selector from `*, *::before,
    *::after` to `.btn, .chip` → **"the reduced-motion override is the
    universal wildcard *, *::before, *::after with !important on
    transition/animation duration"**.
  - Reverting `lib/modal-stack.js`'s `onEscapeKeydown` to call every
    stacked overlay's `onEscape` instead of only the topmost's (the exact
    historical WP 4a.8 bug) → **both** this WP's **"keyboard flow: Escape
    unwinds inner-first — the confirm closes before the sheet, restoring
    focus to the confirm's own invoker"** AND the pre-existing
    `modal-stack.test.js` pin **"Escape calls the topmost overlay's
    onEscape and nothing else"** — confirming the new, composed-level test
    and the old, primitive-level test agree on exactly what breaks.
  - **Round 2's B1 re-verification (the reviewer's explicit ask):**
    reconstructing the ORIGINAL bug shape — reversing source order for all
    five order-dependent pairs at once (`.iconbtn.on`/`.iconbtn:hover`,
    `.segs button[aria-pressed]`/`.segs button:hover`, `.chip[aria-pressed]`/
    `.chip:hover`, `.btn:disabled`/`.btn:hover`, `.notif.unread`/
    `.notif:hover`) → all five named cascade-outcome tests died
    SIMULTANEOUSLY, by name: **"cascade outcome: .iconbtn.on still wins
    over .iconbtn:hover on hover..."**, **"...segs button[aria-pressed]..."**,
    **"...chip[aria-pressed]..."**, **"...btn:disabled..."**,
    **"...notif.unread..."**.
  - **Round 2's B2 re-verification:** reverting `.bulk`/`.bulk.up` to the
    pre-fix opacity/pointer-events-only shape (no `visibility`) →
    **"B2: .bulk is visibility:hidden while closed and .bulk.up restores
    visibility:visible"**.
  - Every mutation above was reverted immediately after observing the named
    failure; suite re-confirmed at 574 green after each individual revert.

  Live-verified in the Browser pane against a static-served copy of `web/`,
  since neither a screenshot nor the polling store's first tick reliably
  completes in this pane's non-displayed/headless posture (`document.hidden`
  reads `true` there, parking every `store.js` loop by design — the same
  park-while-hidden behavior `store.js`'s own module header documents, not
  a bug). Round 1: `matchMedia('(hover:hover)')`/`(pointer:fine)` are `true`
  at a 1280×900 desktop viewport and `false`/`(pointer:coarse)` `true` at an
  emulated 375×812 touch viewport; a genuine CDP-level mouse hover (not a
  dispatched event) over the "Comfortable" segmented button painted
  `color:var(--text)` on its icon; a genuine CDP-level keyboard Tab (a
  JS-only `.focus()` call was confirmed NOT to trigger `:focus-visible` in
  this same browser, so the check specifically avoided that) onto the same
  button showed `outlineOffset: "-2px"` in its computed style. Round 2 (the
  reviewer's explicit re-measurement ask): six synthetic probe elements
  (carrying the exact class/attribute combination of each restored pair —
  `.iconbtn.on`, `.segs button[aria-pressed="true"]`,
  `.chip[aria-pressed="true"]`, a disabled `.btn`, `.notif.unread`, a
  `.nav-btn[aria-current="page"]`) were injected into the live page and
  hovered with genuine CDP mouse events; every one resolved to its STATE
  value under hover, not the plain hover value — `color:rgb(46,217,206)`
  (`--accent`) for the first three and the sixth, `filter:"none"` for the
  disabled button, and the accent gradient background for the unread
  notification. `.bulk`'s B2 fix was checked the same way: closed, its
  computed `visibility` is `"hidden"` and calling `.focus()` directly on its
  Cancel button does NOT move `document.activeElement`, confirming it is
  genuinely unreachable, not merely unpainted.

  No production JS changed — the inventory's keyboard-operability findings
  were all "already correct" (game-card.js, game-detail-sheet.js's depot
  toggle, lib/modal-stack.js's existing inert+Escape stack), so this
  package's only footprint is `css/app.css` (in-place hover gating, six
  cascade fixes, three focus-ring fixes, the B2 visibility fix, new hover/
  scrollbar affordances) and the one test file.

### WP 4e.5 — Downloads/Settings desktop layout (Phase 4e, D-15, last of the phase)

Both views "just stretched": a single, phone-width column of job cards/
queue rows/history rows (Downloads) or form fields (Settings), pulled
across whatever the shared `.view-root` BP-L cap happened to be —
`--w-wall` (960-2000px, the Library grid's own ceiling), never reviewed for
either view's own content shape. Full per-view inventory and the
multi-column/hybrid alternatives considered and rejected for each are
recorded as D-15, `docs/WORKPACKAGES.md`. Fix: one additive rule,
`.view-downloads, .view-settings{ max-width:var(--w-text); margin:0 auto;
}`, in `css/app.css`'s existing BP-L block, right after the pre-existing
`.view-root{ max-width:var(--w-wall); }` it composes with rather than
fights (two different elements — the child `<section>` and its parent
`<main>` — so no cascade tie exists to resolve). `--w-wall`, not `--w-text`,
stays on `.view-library`, unchanged, for its own auto-fill tile grid. No
new token: `--w-text` already existed (960px base, 760px from BP-M up) and
stays exactly that; `theme.css`'s own comment on it gained a short
addendum documenting the new BP-L consumer, and the pre-existing BP-XL
comment's "Settings/Downloads are --w-text-based content" claim — true in
prose since WP 4e.2, false in the actual shipped rule until this package —
was corrected in place rather than left stale.

- `css-downloads-settings-layout.test.js` (new, +13) — same structural,
  no-jsdom posture as `css-layout-foundation.test.js`/`css-overlay-
  geometry.test.js`, self-contained parsing utilities per this suite's
  per-file convention:
  1. **The layout itself.** The BP-L block contains the
     `.view-downloads, .view-settings` rule, referencing `var(--w-text)`
     (never a bare px literal) plus `margin:0 auto`; `.view-library` is
     confirmed excluded from the same selector and confirmed to keep its
     own, independent BP-L rule untouched.
  2. **Flatness.** `--w-text` has exactly two assignments anywhere in
     `theme.css`+`app.css` (the `:root` base, 960px, and BP-M, 760px) — a
     mutation-protection against a future BP-L/BP-XL redefinition silently
     widening Downloads'/Settings' own cap without anyone touching this
     WP's rule at all. BP-XL is asserted to touch neither `--w-text` nor
     either view's selector.
  3. **No cascade tie.** `.view-downloads` is referenced exactly once in
     `app.css` (nothing else touches it, so nothing can tie with this
     rule); `.view-settings` is referenced exactly twice — this WP's own
     rule plus the one pre-existing, unrelated WP 4a.6
     `h4.sec:first-of-type` descendant rule, which is checked by literal
     text to confirm it styles `h4.sec`'s own margin, never `max-width`/
     `margin` on the bare `.view-settings` class itself.
  4. **Mobile/base untouched.** Six "keeps every pre-WP property value"
     pins (same naming convention `css-overlay-geometry.test.js`
     established) across the actual inventory: `.dl-head`/`.dl-sub`,
     `.jobcard`/`.jobtop`, `.qrow`, `.hrow`/`.hrow > button`, `.field`/
     `.srow`. One final pin confirms the capped-column selector text
     itself appears nowhere outside `@media` (the pre-existing, unrelated
     top-level `.view-settings h4.sec:first-of-type` rule is the reason
     this check is scoped to the exact selector string, not "no mention of
     either class name at all", which would have false-positived on it).

  574 baseline → 587 green, first pass. Every pin's mutation target was
  applied to the real files and confirmed dying by name, then reverted
  (suite reconfirmed 587 green after each individual revert):
  - Deleting the `.view-downloads, .view-settings{...}` block entirely →
    **4 tests died simultaneously**: "BP-L (min-width:1024px) exists and
    gives .view-downloads/.view-settings their own capped, centred
    column", "`.view-library` is deliberately excluded from the
    capped-column rule...", and both no-cascade-tie tests (with the rule
    gone, `.view-downloads`/`.view-settings` drop to 0/1 references
    respectively, not the expected 1/2).
  - Changing `max-width:var(--w-text)` to a hardcoded `max-width:760px` →
    **"BP-L (min-width:1024px) exists and gives .view-downloads/
    .view-settings their own capped, centred column"** (the `/max-width:\s*
    var\(--w-text\)/` assertion, specifically — the rule otherwise still
    "exists").
  - Adding `--w-text:900px;` inside the BP-L block's own `:root{...}` (the
    exact drift the flatness pin exists to catch) →
    **"--w-text has exactly two assignments anywhere (theme.css :root
    base, BP-M) — never redeclared at BP-L or BP-XL"** (`3 !== 2` in the
    actual failure output).
  - Changing `.qrow`'s pre-existing `border-radius:var(--r-m)` to
    `var(--r-s)` → **"base .qrow keeps every pre-WP property value (the
    Queue row shape)"** — confirming this pin is a real value check, not a
    vacuous "rule exists" one.

  No production JS changed: a plain container-width cap needed no DOM
  restructuring in either view, so `web/js/views/downloads.js`/
  `web/js/views/settings.js` are untouched, and neither view gained a test
  file of its own beyond the shared CSS pins above (same "DOM-building
  views are not unit-tested directly" posture every prior Phase-4e/WP-4a
  package in this file already documents).
