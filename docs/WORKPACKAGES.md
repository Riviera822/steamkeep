# Remaining work packages — branch-dispatch handbook

Written 2026-08-09 at the Phase-3 close-out so remaining work can be
dispatched as parallel work packages on branches, each in its own session.
Canonical scope stays in `docs/PROJECT_PLAN.md`; this file adds the
*execution structure*: what a package contains, what it must read first,
what may run in parallel and what must stay serial — and why.

## How to dispatch a package

1. Branch off current `main`: `wp/<id>-<slug>` (e.g. `wp/3.9-oracle`).
2. Start a session whose brief is the package section below, verbatim, plus:
   read `docs/LEARNINGS.md` and the listed ADRs BEFORE writing code.
3. Pipeline inside the session is unchanged and mandatory:
   coder implements → reviewer (Opus) reviews → FAIL loops back → PASS.
   Packages marked **[Fable]** additionally get a second pass at the
   irreversible boundary before merge.
4. One package = one branch = one conventional commit (squash if needed).
   Update `docs/PROJECT_PLAN.md` checkboxes (with evidence notes) and
   append distilled findings to `docs/LEARNINGS.md` in the same commit.
5. Merge to `main` only after PASS. Rebase onto `main` first; the only
   expected cross-branch conflicts are `LEARNINGS.md` / `PROJECT_PLAN.md` /
   `README.md` — all append-biased: resolve by keeping BOTH sides.
6. After merge, re-read the "Parallelism map" below — merging a package can
   unblock others.

## The parallelism rule (proven in Phases 0–3)

Packages are parallel-safe when their **file-tree footprints are disjoint**.
The tracks:

| Track | Tree | Notes |
|---|---|---|
| api | `api/` | ONE package at a time unless marked branch-parallel |
| core | `core/` | independent of api until a package says otherwise |
| web | `web/` (new) | Phase 4a; talks to api only over HTTP |
| app | `app/` (new) | Phase 4b; talks to api only over HTTP |
| release | `.github/`, root `README.md`, `SECURITY.md`, `CONTRIBUTING.md` | Phase 5 |
| docs | `docs/` | orchestrator-owned; append-biased merges |

`deploy/` is shared: packages touching it say so explicitly and are serial
against each other.

---

## Phase 3 — remainder (backend)

Status at writing: 3.8b merged; **3.10 and 3.12 are in flight in the main
session** (do not dispatch); 3.9, 3.11, 3.13 are open.

### WP 3.9 — Manifest oracle, opt-in (api) — **branch-parallel** [Opus coder]
*May run in parallel with 3.11/3.13: it is a new module + one config row;
expected conflicts are only config.py/README appends.*
- ADRs: 0006 (Tier-2 oracle decision, fail-soft, default OFF), 0007 beta
  addendum **decision B**.
- Scope: `VAULT_MANIFEST_ORACLE=steamcmd_api` queries api.steamcmd.net for
  depot lists + current manifest gids; enables pre-emptive stale badges and
  depot info for never-cached games; **decision B**: open (non-passworded)
  beta-branch manifest gids join the GC keep set when the oracle is on.
  Fail-soft everywhere: oracle down/garbage ⇒ behave as if OFF, never block
  a job, never poison depot_manifests (oracle data lives in its own table or
  is clearly provenance-tagged). Strict validation of everything returned
  (LEARNINGS "Parsers" section is binding — ids feed SQL and paths).
- Tests: recorded fixtures (no live network in tests), poisoned/hostile
  responses, oracle-off default pinned by mutation, GC keep-set union with
  beta gids (mutation: dropping the union must kill a named test).
- DoD: suite green; README oracle section incl. privacy note (queries leave
  the LAN); PLAN checkbox.

### WP 3.11 — Event sweep: miss-trigger + client stats/bypass (api) — **serial after 3.12 merge** [Opus coder] **[Fable]**
*Serial because it wires into jobs/worker/scheduler that 3.12 reshapes, and
it auto-enqueues jobs (job-storm risk = the Fable boundary).*
- ADRs: 0008 (the feed contract — read the WP 3.10 format spec in
  `core/README` after 3.10 merges), 0001 (hybrid), 0003/0007 (mapping gate).
- Scope: cursor-based sweeper on the WP 3.5 scheduler cadence reading the
  vault-core event log (format-version checked, unknown version ⇒ skip +
  warn, never misparse). Persisted byte-offset cursor; rotation only after
  a successful sweep. Derives: (a) miss-triggered prefill — MISS on a depot
  mapped to a non-current app enqueues a non-forced prefill, strict dedupe
  + per-app cooldown (config), unmapped depots counted but NEVER trigger;
  (b) per-client hit/bytes stats; (c) bypass detection — agent-report source
  address never seen in the event log within the report window ⇒
  `bypass_suspected` on `GET /v1/clients`.
- Tests: cursor crash-recovery (sweep dies mid-file ⇒ re-read, no loss/dup
  beyond idempotent counters), enqueue-storm mutation (drop the cooldown ⇒
  named test dies), truncated/hostile log lines, log-absent = feature off.
- DoD: suite green; README; PLAN checkboxes (miss-trigger + client stats).

### WP 3.13 — Generic webhooks (api) — **serial after 3.12 merge** [Sonnet coder]
*Serial: consumes the 3.12 job-lifecycle events (done/error/cancelled).*
- Scope: optional outbound POST on job finished/failed and bypass-warning
  events; generic JSON body (Discord/Slack/ntfy-compatible via templates is
  a NON-goal — one generic schema, documented). URL from config, off by
  default; fire-and-forget with bounded retry, NEVER blocks the worker;
  secrets never logged; SSRF posture documented (operator-supplied URL is
  trusted by definition — say so).
- Tests: fake receiver, retry/backoff bounded, worker latency unaffected
  (webhook endpoint hanging ⇒ job completion not delayed — pin it).
- DoD: suite green; README; PLAN checkbox. Phase 3 is COMPLETE when 3.9,
  3.10, 3.11, 3.12, 3.13 are merged.

---

## Phase 4a — Web UI (track: web) — dispatching these packages IS the user's frontend go

Stack decision (made here so packages don't re-litigate it): **no-build
vanilla SPA** — ES modules, the mockup's design language, served by
vault-api as static files. No node toolchain: keeps `compose up` the whole
deploy story (plan §9) and CI trivial. Revisit only if a package hits a
hard wall and then via ADR.

The interactive mockup — **approved by the user at round 7 (2026-08-09)
and frozen** — is the design source of truth:
`docs/design/vault-app-mockup.html` + `vault-app-mockup-NOTES.md` (open the
HTML in any browser). It carries the status-icon system, capsule pills,
in-place tick patching (round 7), bulk semantics, stale-requires-cache
invariant and the notification model. One accepted divergence: the mockup
shows a paused job holding the worker slot; the WP 3.12 backend releases
it — UI packages follow the BACKEND semantics. Remaining polish is
expected to surface through real use, not further mockup rounds.

Recorded divergence (WP 4a.3, 2026-08-10, orchestrator decision — user
veto welcome): the library filter chips ship All / Cached / Not cached /
Downloading / **Failed**, where the mockup has **Update ready** in the
fifth slot. Rationale: `GET /v1/games` has no stale/update field yet
(Phase-4 decision pending), while a persistent per-app `error` status is
real shipped API surface the mockup never modeled — a failed prefill
needs a findable home. When the stale field lands, Update ready returns
and the chip set is re-decided against the mockup.

Recorded cross-frontend divergence (WP 4b.5, 2026-08-11,
reviewer-endorsed): the ANDROID job partition routes a job with an
unknown status into History (neutral glyph, raw status word) instead of
silently dropping it from every section as `web/js/lib/job-partition.js`
does; unknown statuses deliberately do NOT count toward the nav pip
(an unknown terminal status must not pin an uncloseable badge; an
unknown active status self-corrects and stays visible in History).
BACKPORT NOTE for the web: adopt the same routing in job-partition.js —
candidate for the WP 4a.8 polish pass. The log-excerpt truncation-marker
pin (startsWith, not contains) named here as a shared test gap is CLOSED
on both sides as of WP 4b.5 (Kotlin, `LogExcerptTest`'s named mutation
pin) and WP 4a.8 (web) — re-verified during WP 4b.9's carry-over pass;
this line stayed stale after 4a.8 landed and is corrected here rather than
left to mislead the next reader.

Recorded divergences (WP 4a.7, 2026-08-11, reviewer-endorsed): (1) the
notification bell lives in the app-shell topbar, not the mockup's
Library header — reachable from every view, and 4a.7 may not edit
library.js; functional superset. (2) The notification log is
session-only (in-memory) — the NOTES' poll+diff model has no
persistence mandate, and persisting derived events would need its own
dedupe design; revisit only if real use demands it. For the 4a.8 pass:
add the update_ready→detail-sheet target upgrade once 4a.4 lands, and
put the two DOM-wiring fixes (sheet closes on navigation, icon
aria-hidden) into the DOM-harness or live-check list.

Recorded divergence (WP 4b.6, 2026-08-11, reviewer-endorsed): the depot
sharing presentation has a FOURTH state beyond the mockup's three —
ORPHANED, for a shared depot whose co-owning apps all have no cache
content (ADR-0003 last-remnant case, the mockup's Meridian Rally
scenario). Collapsing it into "sole holder" would state a falsehood
(the viewed game holds nothing). Web 4a.4 adopts the same four-state
wording when it builds the detail sheet. User veto welcome.

Recorded divergence (WP 4a.5, 2026-08-10, reviewer-endorsed, same class):
a `cancelled` status-icon kind exists (distinct stop glyph, neutral
colour, own screen-reader word) because `cancelled` is a real terminal
job status (WP 3.12) the mockup never modeled; re-skinning it as Failed
would misreport an operator action. Also recorded for the 4a.8 polish
pass: job cards ship without the mockup's decorative mini cover
thumbnail (no information lost; mockup itself hides all content inside
`.thumb`).

Recorded divergence (WP 4b.10, 2026-08-17, reviewer-endorsed): the ANDROID
clients sheet has no persistent app-shell bypass banner, unlike web's
WP 4a.7 banner (`web/js/components/bypass-banner.js`, present on every
view as a third entry point alongside the notifications panel and the
Settings button this WP adds). Deliberately not ported: the WorkManager
background differ (WP 4b.8) already covers the PUSH half of bypass
awareness independently of any banner, and adding a new persistent,
always-visible surface is itself a frozen-mockup-scope decision (same
class as the WP 4a.1 "Clients is a sheet, not a nav item" ruling), not
something to slip in as a side effect of closing the notification-routing
gap. User veto welcome; revisit if real use shows the two notification-
only entry points (Settings button, bypass tap) are not discoverable
enough.

Recorded divergence (WP 4c-web, 2026-08-18, orchestrator decision — user
veto welcome): the Library view gains a full-width header row below
`.lib-head` for a "Check & update all cached games" button
(`web/js/views/library.js`) — an element the frozen round-7 mockup does
not have at all, because Phase 4c (`docs/PROJECT_PLAN.md` §7) is itself
post-mockup scope: the mockup's `doRefresh()` only ever re-polls vault-api,
it never contacts Steam or starts a download, so there was nothing in the
mockup for this control to extend. Resolved alongside it, same WP: the
`doRefresh()` divergence the plan asked this package to settle stays
resolved as "kept separate" — `store.refreshNow()` (this app's `doRefresh()`
equivalent, wired to pull-to-refresh's `visibilitychange` nudge in
`web/js/store.js`) remains a passive re-poll only; the new button is a
SEPARATE control calling `POST /v1/prefill/cached`, never folded into
pull-to-refresh (the plan calls a refresh gesture that can start downloads
a trap, and this WP agrees). The Android port (separate, still-open work
package) should adopt both decisions verbatim: its own equivalent header/
toolbar gains the same button, worded with the same "Check & update, never
Check" rule, and its own pull-to-refresh equivalent stays passive-only.

**Adopted verbatim on Android (WP 4c-app, 2026-08-18).** Both decisions
carried over exactly as recorded above, with the layout stated precisely
rather than reusing web's "full-width header row" description for a
materially different one (Opus review, this WP): `LibraryScreen.kt` gains
an end-aligned button inside its own `fillMaxWidth()` row directly below
`LibraryToolbar` (the Android equivalent of `.lib-head`) — the row's
container spans the width, the button does not (web's own button is
`width:100%`), and the placement order differs too (web: tools → check row
→ search; Android: search → layout/select → check row) — for the same
"Check & update all cached games" control, worded with the same "check &
update, never bare check" rule (`library_check_update_button`/`_busy` in
`strings.xml`); and `ui/library/` has no pull-to-refresh gesture at all
(`LibraryController.pollGamesForever`/`pollJobsForever` are fixed-cadence
foreground polls only), so the "stays passive-only" rule is satisfied by
the gesture's absence rather than by an explicit carve-out. The pure
wording/partition/error/in-flight logic is a line-for-line Kotlin port of
`web/js/lib/cached-prefill-outcome.js` in
`ui/library/logic/CachedPrefillOutcome.kt`, pinned by a literal
cross-frontend wording-contract test
(`CachedPrefillOutcomeWordingContractTest`) in addition to the ported
functional suite (`CachedPrefillOutcomeTest`, including both BLOCKER
REGRESSION cases by name).
Recorded divergences (WP 4e.1, 2026-08-18, orchestrator decision — **user
approved the full desktop-layout proposal, all divergences included, on
2026-08-18** — "setze alles so um"; implemented as designed, not narrowed):
the frozen round-7 mockup is a single 390×844 phone frame with no responsive
layer at all, and Phase 4e is the first package to deliberately diverge from
it above phone width. Three divergences land in this foundation package:

- **D-1 (bottom nav → left rail).** At BP-L (`min-width:1024px`) the
  mockup's 3-item bottom nav (`web/css/app.css`'s base `.nav`, unchanged
  below BP-L) becomes a sticky, full-height LEFT rail — `#app` turns into a
  `var(--rail-w) 1fr` grid (`grid-template-areas: "rail top" "rail banner"
  "rail main"`), and each `.nav-btn` switches from icon-above-label
  (mockup shape) to icon-beside-label rows. The mockup has no concept of a
  rail at all (it is phone-only chrome); `aria-current`, the queue pip and
  every click/keyboard handler carry over unchanged (`app.js` reads nav
  buttons by `data-view`, never by layout shape).
- **D-4 (derived column count — no auto-fill grid yet).** The brief's own
  measured motivation (a 173×260 cover tile rendering at a fixed 458×687
  from 1280 to 2560px, identically, because nothing before this WP capped
  `.view-root` at anything narrower than 960px) is **not resolved** by this
  package — deliberately out of scope, and this WP does not widen
  `.view-root` past that same 960px baseline either, on purpose. A first
  version of this package DID widen it (`--w-wall:1440px`/`1720px` at BP-L/
  BP-XL), and an orchestrator review measured live that this made the
  problem WORSE, not better, before the auto-fill grid exists to use the
  room (~815×1222 at 1920px, ~838×1257 at 2560px) — a foundation package
  regressing the exact problem its phase exists to fix, on its own, is not
  independently shippable. Fixed by sequencing: `--w-wall` now stays 960px
  in every breakpoint block (base/BP-L/BP-XL all say so explicitly), so the
  tile is IDENTICAL to the pre-WP baseline at 1280–2560px, not merely
  no-worse — re-measured live and pinned headlessly (mutation-verified:
  restoring 1440px/1720px kills two named tests in
  `web/tests/css-layout-foundation.test.js`). `--tile-min` is declared in
  `theme.css` as the future `minmax()` floor but wired to no
  `grid-template-columns` yet. The next package (auto-fill grid) is what
  actually derives the column count from viewport width AND raises
  `--w-wall` past 960px in the SAME change — this one only gives it the
  breakpoint/token plumbing to do so without re-litigating it, deliberately
  holding the width cap flat until then.
- **D-11 (nav DOM order).** `web/index.html`'s `<nav>` now precedes
  `<main id="view-root">` — reading/focus order matches the rail being
  visually first once BP-L applies, even though the mockup (phone-only,
  bottom nav) never had this ordering question. Visual order below BP-L is
  unchanged: `css/app.css`'s `.nav{ order:1 }` keeps it pinned at the
  bottom exactly as the frozen mockup shows, byte-for-byte in the rendered
  output (see `web/tests/css-layout-foundation.test.js`'s "base is
  untouched" pins). `app.js` finds nav buttons via `data-view`, not DOM
  position, so no script changed.

  **Phone-side consequence, recorded honestly (Opus review should-fix S2,
  WP 4e.1 fix round) — this register's original wording only claimed
  VISUAL order is unchanged below BP-L, which is true but incomplete.**
  `order` is a purely VISUAL/paint-order property; it does not affect tab
  order or DOM/accessibility-tree reading order, both of which follow
  source order regardless of `order`. Below BP-L (phone/tablet, the same
  surface WP 4a.8's a11y pass signed off on), a keyboard/screen-reader user
  now reaches the bottom nav's three buttons BEFORE the page's actual
  content on every view — the same phone experience 4a.8 verified now tabs
  "nav, then content" instead of "content, then nav". This is a defensible,
  conventional trade-off (most responsive sites with a DOM-first nav/rail
  make the same call, and a skip-link-style pattern is the usual mitigation
  if it proves to matter) — but it is a real, user-facing change to a
  surface a PRIOR work package already signed off on, not a no-op. Recorded
  here for the phone-side a11y record; revisit if real use surfaces it as a
  problem (a "skip to content" link is the natural fix if so). User veto
  welcome, same as every other entry in this register.

The remaining Phase 4e divergences (auto-fill grid density, overlay
geometry at BP-L, Downloads/Settings-specific layout, the pointer/keyboard
model) belong to later packages in this phase and are deliberately NOT
recorded here — this entry covers only what WP 4e.1 itself shipped.

### WP 4a.1 — Static serving + app shell (api/ + web/) — **first, serial**
- vault-api mounts `web/` (StaticFiles, SPA fallback, sane CSP/security
  headers, no-cache for index). Router auth must NOT cover static assets;
  the app itself authenticates API calls with the key the user enters.
- App shell: nav, views scaffold, dark theme tokens from the mockup, the
  status-icon component (icons + motion incl. reduced-motion), toasts.
- DoD: served page loads against a running vault-api; suite green.

### WP 4a.2 — API client + polling store (web/) — **serial after 4a.1**
- Fetch wrapper (X-Api-Key, error taxonomy, demo mode), polling loops with
  backoff (jobs fast-poll while active; games/clients slow-poll), the
  client-side notification differ (mockup NOTES: poll + diff, no push v1).
- DoD: headless tests (node:test or plain harness) for differ + backoff.

### WP 4a.3 — Library view (web/) — **branch-parallel after 4a.2**
- Grid 2/3/list, search+chips (ANDed), capsule pill (icon+GB, actionable),
  multi-select incl. bulk download split semantics AND bulk delete with
  multiPlan arithmetic (port from the mockup, it is already set-aware),
  in-place progress patching (round-7 rule: never rebuild animated nodes on
  a tick).
### WP 4a.4 — Detail sheet + delete flows (web/) — **serial after 4a.3**
- Depots with computed sharing (holders/sole-holder/orphan wording), delete
  confirm with per-depot freed/kept list, pause/resume/cancel actions,
  GC action per game (dry-run first, show plan, then execute — the API is
  already shaped for this).
### WP 4a.5 — Downloads view (web/) — **branch-parallel after 4a.2**
- Active job card (pause/resume/cancel per 3.12 semantics — note the slot
  decision in api/README, the mockup may diverge), FIFO queue, history with
  log excerpts.
### WP 4a.6 — Settings + onboarding + Steam identity (web/) — **branch-parallel after 4a.2**
- Ports the 3-step onboarding, connection profiles, vault name.
- **DECIDED (user, 2026-08-09): option A with C's input UX** — vault-api
  gains an opt-in relay for `GetOwnedGames`/`GetPlayerSummaries`; the Web
  API key is entered in the web UI settings and stored server-side.
  Boundaries in the ADR-0004 addendum (public-profile reads only, key
  never logged/echoed, relay off until configured, Android keeps the
  device-local path). NOTE: the relay endpoint itself is an api/-track
  mini-package (serial against other api/ work); 4a.6 consumes it.
### WP 4a.7 — Notifications + bypass surfaces (web/) — **serial after 4a.3/4a.5 merge**
- Bell + panel with navigate-to-target (round-6 semantics), bypass banner
  + clients sheet backed by the real `/v1/clients` (bypass_suspected from
  WP 3.11).
### WP 4a.8 — End-to-end + a11y pass (web/) — **last, serial**
- Real-server e2e (demo dataset + live vault-api), keyboard navigation,
  screen-reader words for every icon (mockup rule), reduced-motion audit.
- DoD: Phase 4a complete; PLAN checkboxes.

---

## Phase 4b — Android app (track: app)

minSdk 26, Kotlin/Compose, English UI first, System-VPN profile first
(tsnet post-v1). Same design source of truth as 4a. Steam login via
OpenID in a Custom Tab; the PHONE fetches the library from Valve
(no CORS problem natively — ADR-0004 holds unmodified here).

- **WP 4b.1** project skeleton + theming + status-icon component — first.
- **WP 4b.2** API client + connectivity profile abstraction (one interface,
  System-VPN impl; public-domain impl behind it) — serial after 4b.1.
- **WP 4b.3** Steam identity (OpenID + GetOwnedGames on-device) —
  branch-parallel after 4b.2.
- **WP 4b.4** Library (grid/layouts/search/pills/multi-select incl. bulk
  delete) — branch-parallel after 4b.2.
- **WP 4b.5** Downloads + job control — branch-parallel after 4b.2.
- **WP 4b.6** Detail + delete/GC flows — serial after 4b.4.
- **WP 4b.7** Onboarding + settings — branch-parallel after 4b.3.
- **WP 4b.8** Notifications (polling via WorkManager, respecting Doze) —
  serial after 4b.5.
- **WP 4b.10** Clients/bypass detail surface (`GET /v1/clients` sheet,
  closing the WP 4b.8 recorded routing gap: bypass notifications land on
  the sheet directly instead of Settings) — serial after 4b.8. **DONE
  2026-08-17** — see `docs/PROJECT_PLAN.md` §7 Phase 4b evidence notes and
  `app/README.md`'s "Clients sheet (WP 4b.10)" section.
- **WP 4b.9** Release build + signing docs, APK distribution (no Play
  Store requirement; F-Droid long-term) — last. **DONE 2026-08-17** — see
  `docs/PROJECT_PLAN.md` §7 Phase 4b evidence notes and `app/README.md`'s
  "Release build, signing, distribution, and carry-over cleanup (WP 4b.9)"
  section. Carry-over items from reviews, each individually re-verified
  against the code before acting (not assumed from this list's own
  wording):
  - move the unreferenced GalleryScreen (4b.1) and IdentityScreen (4b.3,
    superseded by SettingsScreen in 4b.7) to src/debug/ keeping their
    tests — **done**; GalleryScreen had already moved during WP 4b.4
    itself (this list was never updated to drop it), IdentityScreen moved
    in WP 4b.9;
  - re-check the security-crypto 1.1.0-alpha pin for a GA release — **done
    (reported, not bumped)**: a GA 1.1.0 now exists and is API-compatible
    (verified via the downloaded .aar's class list); the WP 4b.9 brief
    explicitly required reporting + recommending rather than silently
    bumping a security dependency — see `gradle/libs.versions.toml`'s
    comment for the finding and the recommendation to bump in its own
    reviewed change once a device is available to re-verify
    EncryptedCredentialStore against the new artifact;
  - the OpenID state parameter landed in 4b.7 (residual closed) — already
    marked closed before this WP, unchanged;
  - pin the Kotlin LogExcerpt truncation marker to position 0 (web side
    pinned in 4a.8; the 4b.5 twin is still open) — **turned out to already
    be closed since WP 4b.5** (`startsWith`, not `contains`, with its own
    named mutation-pin test in `LogExcerptTest`; `docs/PROJECT_PLAN.md`
    §7's own WP 4b.5 evidence note already said so) — this list entry was
    stale, not open work;
  - release notification icon art (4b.8 reuses the launcher monochrome) —
    **decided for v1**: kept, as a deliberate final decision rather than a
    standing "revisit" pointer — the launcher's monochrome layer is
    already the flat white-on-transparent silhouette Android's own
    notification-icon guidance asks for, not merely convenient reuse; a
    bespoke glyph is a general future art-pass item, not a WP 4b.9
    blocker.

---

## Phase 5 — Community release (track: release)

- **WP 5.1 — CI** (`.github/`) — **DONE 2026-08-09** (was: dispatchable NOW, parallel to everything):
  GitHub Actions running api pytest (Linux), agent `go build/vet/test`
  (Linux + Windows), `nginx -t` on rendered core templates, PS 5.1 syntax
  checks for packaging scripts. NO image publishing here. Container-network
  dependent tests (real CDN) stay local-only — mark them skipped-in-CI
  honestly.
- **WP 5.2 — README + architecture diagram + 5-minute quickstart** — parallel;
  MUST include the works-for-guests FAQ (ADR-0001 store-on-miss) already
  outlined in PLAN Phase 5.
- **WP 5.3 — SECURITY.md + threat model + pre-release security review** —
  after code freeze of api/core; **[Fable] mandatory** (standing policy).
- **WP 5.4 — CONTRIBUTING.md, issue templates, example configs** — parallel.
- **WP 5.5 — Multi-arch images to ghcr.io** — **DECIDED (user,
  2026-08-09): move to a GitHub organization.** Target naming:
  `ghcr.io/steamvault/{core,api,dns}` (public). Prerequisite the USER
  performs (account-level, never autonomous): create the org — name
  availability decides the final slug — and move the repo into it
  (GitHub auto-redirects the old URL). After the move: update the git
  remote, README badges/links, compose image references; publishing
  itself stays a with-the-user step.
- **WP 5.6 — Announcement** (r/selfhosted, r/homelab, LanCache Discord;
  complement-not-killer framing) — **with the user only. DECIDED gate
  (user, 2026-08-09): no announcement until the user has personally
  tested the stack end-to-end WITH the Android app** — i.e. after the
  Zeus rollout AND Phase 4b are usable; the post carries the user's own
  real-world numbers.

---

## Deployment (not a branch package)

Zeus rollout is a joint interactive session (SSH, Dockge stack, IP alias on
eno1, AdGuard rewrite, `zfs create -o recordsize=1M -o atime=off
-o compression=off hdd_pool/steamvault-cache`, `VAULT_CACHE_PATH`). Two
user-side blockers noted earlier: the HADES Tailscale route still advertises
the stale subnet, and the Fritz!Box announces itself as IPv6 DNS via RA
(live IPv6 bypass) — fix both before measuring hit rates.

## Parallelism map (what can be in flight at once)

```
NOW (main session):        3.10 (core)   3.12 (api)   mockup R7 (scratchpad)
dispatchable in branches:  3.9 (api, new-module isolation)   5.1 (.github)   5.2/5.4 (root docs)
after 3.12 merges:         3.11 (api)  → then 3.13 (api)
after 3.10+3.11+3.12+3.13: Phase 3 COMPLETE
4a: 4a.1 → 4a.2 → {4a.3, 4a.5, 4a.6(decision pending)} → 4a.4, 4a.7 → 4a.8
4b: 4b.1 → 4b.2 → {4b.3, 4b.4, 4b.5} → 4b.6, 4b.7, 4b.8 → 4b.10 → 4b.9
5.3 after api/core freeze · 5.5/5.6 gated on user
4a and 4b tracks are independent of each other and of Phase-3 remainder
(they consume the HTTP API only) — but 4a.5/4b.5 want 3.12's semantics
merged first, and 4a.7 wants 3.11.
```

## User decisions — all resolved 2026-08-09

1. **4a.6** Steam library in the browser: **A with C's input UX** (opt-in
   vault-api relay, key entered in the web UI, stored server-side —
   ADR-0004 addendum).
2. **5.5** ghcr/repo home: **move to a GitHub organization**
   (`ghcr.io/steamvault/*`; org creation + repo move are user-performed
   account actions).
3. **5.6** announcement: **only after the user has personally tested
   end-to-end with the Android app** (post-Zeus, post-4b).

Post-v1 backlog (documented, undispatched): embedded tailscale (tsnet),
iOS app, queue reordering, drag-to-reorder, server-side notification
cursor, per-depot grace windows.
