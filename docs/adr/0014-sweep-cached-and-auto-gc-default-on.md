# ADR-0014: The cached-apps sweep and auto-GC ship on by default, paired

Date: 2026-08-22
Status: Accepted (operator decision, 2026-08-22, WP SWEEP-1 — deviation from
the WP 4d/WP 3.12 defaults recorded below)

## Context: the previous defaults, and why they were chosen that way

Two independent-looking switches, shipped by two different work packages,
turn out to be one decision:

- **`VAULT_SWEEP_INCLUDE_CACHED` / `Settings.sweep_include_cached`** (WP 4d,
  plan §7 Phase 4d) widens the nightly sweep's target set from "every app
  the gaming PCs currently report installed" to that union PLUS every app
  that already holds cache content on disk, installed anywhere or not.
  Shipped `False` by default. The reasoning at the time (`config.py`'s own
  comment on `DEFAULT_SWEEP_INCLUDE_CACHED`, `docs/PROJECT_PLAN.md`'s Phase
  4d entry): the cached set is "a much larger, operator-unbounded set that
  spends bandwidth (checking) and, on real updates, disk (fresh chunks) on
  games nobody currently asked for" — "that must be an explicit operator
  opt-in, not a byte-for-byte-free upgrade to the existing sweep."
- **`VAULT_AUTO_GC` / `Settings.auto_gc`** (WP 3.12, plan §7 Phase 3) decides
  whether a prefill that actually updated something automatically queues a
  garbage-collection job for that app. Shipped `off` by default (a
  three-value ladder — `off` | `dry-run` | `execute` — precisely so an
  operator could watch what automatic collection *would* reclaim before
  trusting it with real deletions). The reasoning at the time: "a feature
  that can delete files does not switch itself on."

Both defaults were the conservative, standard choice for their own work
package considered in isolation, and both remain individually defensible in
isolation. What WP 4d's own scheduler code (`scheduler.cached_sweep_gc_risk`)
already named, at ship time, is that the two are not actually independent:
every app the cached-apps sweep mode keeps current adds fresh chunks for the
new manifest while the *old* manifest's chunks become orphans — that is what
a game update is, on disk. A vault that keeps itself current without
collecting garbage keeps itself current straight into a full disk. WP 4d
shipped the sweep mode off by default specifically so this coupling would
never arise un-asked-for; `cached_sweep_gc_risk` and its scheduler warning
exist to catch an operator who turns the sweep on without also turning on
real collection, not to bless that combination.

## Decision

**Both defaults flip, together, as one change:**

- `DEFAULT_SWEEP_INCLUDE_CACHED` — `False` → `True`
- `DEFAULT_AUTO_GC` — `AUTO_GC_OFF` → `AUTO_GC_EXECUTE`

A fresh SteamHangar install now keeps every cached game current on its own
schedule, whether or not any gaming PC currently reports it installed, and
reclaims the orphaned chunks that keeping it current leaves behind — with no
`.env` edit and no `PATCH /v1/settings` call required.

**On whose decision, and on what basis.** The operator asked for "keep the
cache current" to be the shipped, out-of-the-box behavior rather than an
opt-in an installer has to discover and switch on. The cost this ADR's
"previous defaults" section names above — disk spent on superseded chunks
for games nobody explicitly asked to have refreshed, and a real behavior
change for every existing install that pulls this update — was presented
to the operator **twice**, explicitly, before this package was written.
The operator chose the new default anyway, with that cost understood, not
because the cost stopped being real. It did not: see "Upgrade impact"
below. This ADR is the record of a decision that deviates from WP 4d's and
WP 3.12's own stated reasoning, not a reversal of that reasoning — the
argument for "opt-in was the safer default" in `docs/PROJECT_PLAN.md`'s
Phase 4d entry and the pre-existing `config.py` comments stays visible
rather than deleted; it is superseded, by this decision, for this
project, from this date.

## The pairing argument: why these two cannot split

`scheduler.cached_sweep_gc_risk(settings)` already states the mechanism
exactly: `sweep_include_cached` on AND `auto_gc` anything other than
`execute` (including `dry-run` — it reports what could be reclaimed and
reclaims nothing) is the condition that grows a vault's disk usage without
bound. That function does not change in this package; it is exactly the
guard this decision needs kept alive, because after this ADR it protects
against a **half-configuration** (an operator, or a future default change,
turning one of the pair on without the other) rather than against the
*feature itself*, which is now the shipped default. Shipping
`sweep_include_cached=True` alone, with `auto_gc` left at `off`, would be
precisely the configuration `cached_sweep_gc_risk` exists to warn about — so
this package changes both constants in the same commit, and treats splitting
them across two separate changes as a defect, not a smaller, safer step.

**What the pairing argument does NOT settle by itself (review round 1,
should-fix S2).** The pairing above answers "is GC actually reclaiming
something" — it does not by itself answer "is GC reclaiming the RIGHT
things, safely, unattended." `VAULT_GC_GRACE_DAYS` (default 14) is the only
thing standing between an unattended `execute` run and a chunk a LAN client
stored against a manifest this vault never recorded (ADR-0007's beta-branch/
store-on-miss addendum). An operator who sets that to `0` — a supported,
documented value, "no grace window" — gets exactly that: unattended deletion
with no recency protection at all. And this pairing is the **first**
shipped configuration in which `execute` runs automatically, immediately,
after any qualifying prefill, with nobody having asked for that specific GC
run — which makes it the first configuration in which ADR-0007's own known
residual (content that outlived its grace window, in no manifest this vault
has recorded) can be collected without an operator ever choosing to run GC
at all. Both failure modes here are re-download-only, never corruption
(ADR-0007's bound) — which is why they are not blockers to this decision —
but the pairing argument above is not itself sufficient reasoning for them;
`VAULT_GC_GRACE_DAYS` is doing real, separate work here that this ADR does
not get to assume away.

## Shipping an enabled nightly schedule (review round 1, should-fix S3 — operator decision, 2026-08-22)

**The gap S3 found.** The first version of this ADR, and the package it
described, flipped `sweep_include_cached`/`auto_gc` without ever shipping a
`VAULT_SCHEDULE_WINDOW` — `deploy/compose.yaml` did not forward it,
`deploy/.env.example` had no line for it, `DEFAULT_SCHEDULE_WINDOW` was (and
remains) empty in `config.py`'s own bare-metal default, and
`Settings.scheduler_enabled` is exactly `schedule_window is not None`. So
out of the box, before this section's fix, **the sweep half of the flip did
nothing**: with no window, the scheduler thread never sweeps, so
`sweep_include_cached=True` had no sweep to apply itself to. The auto-GC
half was live regardless, because `_maybe_queue_auto_gc` fires after ANY
successful, updating prefill — manual, miss-triggered, or scheduled — not
only a scheduled one. The pairing argument above, which is this ADR's entire
authority for defaulting a deleting feature on, silently assumed a sweep
that, absent a window, was not running.

**The decision.** Put to the operator directly: ship a suggested default
window, always in local (`TZ`-resolved) time, rather than document the gap
and leave the sweep inert by default. The operator chose to ship the window.
This is decided HERE, separately from the sweep/auto-GC default flip above —
enabling an unattended nightly schedule by default is its own behavioural
choice, not a footnote to that one, even though the same package delivers
both.

**What ships:**

- `VAULT_SCHEDULE_WINDOW` defaults to `03:00-07:00` (`deploy/compose.yaml`,
  `deploy/.env.example`) — a suggested quiet-hours window, not a claim about
  any specific operator's actual schedule.
- `TZ` defaults to `UTC`, forwarded for the first time by this project's
  Compose file at all. **Deliberately UTC, not a guessed populated zone**:
  this project ships to operators in every timezone, and defaulting to one
  populated zone (e.g. `Europe/Berlin`) would be silently wrong for most of
  them. UTC is the one value that is honestly correct for everyone by
  construction — at the real cost that the window above does not land at
  3 AM *local* for anyone who does not also set `TZ` explicitly.
- **The local-time requirement, made visible rather than assumed.**
  `vault_api/scheduler.py`'s existing local-time handling
  (`local_now()` = `datetime.now().astimezone()`) was already correct and is
  UNCHANGED by this package — the window is evaluated on the container's
  wall clock every tick, a DST transition only ever affects the advisory
  `next_eligible_at` estimate (never a live sweep decision), and that was
  already documented before this ADR. What was missing is an operator-facing
  signal that a wrong or absent `TZ` produces a wrong window silently.
  `scheduler.describe_resolved_schedule` now logs one line, once, on the
  scheduler thread's first tick (review round 2, blocker R2-B2b moved this
  off `start()` and onto the tick loop, against `effective_settings` rather
  than the boot snapshot — see the review-round-2 consequences entry below),
  naming the REQUESTED `TZ` value alongside the resolved local UTC offset
  and zone name, and the next opening in both local and UTC time — so an
  operator who set nothing sees their window expressed against `UTC+00:00`
  and can tell at a glance it is not their night, and an operator with a
  typo'd `TZ` sees the mismatch named directly rather than a
  plausible-looking wrong answer (round 2, blocker R2-B2a). `GET
  /v1/schedule`'s existing `server_timezone`
  field (the resolved UTC offset, already shipped since WP 3.5) is judged
  sufficient for the API surface — it already lets any caller compute local
  time from `next_eligible_at`'s UTC value, and a second, redundant zone
  field was judged not to earn its keep; the startup log line is the new
  surface, not a new API field.

**Upgrade impact of this section specifically:** an existing deployment that
already set its own `VAULT_SCHEDULE_WINDOW` (env or `PATCH /v1/settings`)
is UNCHANGED — the substitution only resolves for a variable that was never
set (see "The no-colon substitution fix" below for exactly which Compose
syntax that requires, and the bug in an earlier draft of this package that
this ADR's own review round 2 caught). A deployment that never touched it
goes from "scheduler disabled" to "scheduler enabled, sweeping 03:00-07:00
UTC (or the deployment's own `TZ`, if set) nightly" the moment it recreates
its containers. See "Upgrade impact" below for how this combines with the
sweep/auto-GC flip, and "Night one on an upgraded install" for what that
first sweep actually does.

**The no-colon substitution fix (review round 2, blocker R2-B1, measured).**
An earlier draft of this package forwarded `VAULT_SCHEDULE_WINDOW` as
`${VAULT_SCHEDULE_WINDOW:-03:00-07:00}` — the colon form, which Compose
(like POSIX shell parameter expansion) substitutes the default for BOTH an
unset variable AND one that is present but explicitly blank. Measured
directly: a `.env` file containing the line `VAULT_SCHEDULE_WINDOW=` (present,
blank) still rendered `03:00-07:00` in `docker compose config`'s output —
there was no `.env` expression of "disabled" at all, while this ADR's own
"How an operator opts out" section (and `deploy/README.md`,
`deploy/.env.example`'s UPGRADE NOTE) claimed one existed. The fix is the
no-colon form, `${VAULT_SCHEDULE_WINDOW-03:00-07:00}`, which substitutes
ONLY when the variable is unset entirely and leaves an explicit blank value
as `""` — exactly what `config.Settings.from_env` already treats as
"scheduler disabled" (that side of the config-parsing logic was always
correct; only the Compose-substitution side was wrong). `TZ` deliberately
KEEPS the colon form (`${TZ:-UTC}`): unlike the window, an explicitly blank
`TZ` and this variable's own default (`UTC`) are not two different states —
POSIX `tzset()` already treats an empty `TZ` as UTC, so the two substitution
forms are observably identical for this one variable, and the simpler,
more common form was kept deliberately rather than changed for
consistency's own sake.

## Upgrade impact

This is a real, user-visible behavior change on upgrade, not only a new
default for fresh installs:

- **An existing deployment that has never set `VAULT_AUTO_GC`,
  `VAULT_SWEEP_INCLUDE_CACHED`, or `VAULT_SCHEDULE_WINDOW`** — the common
  case, since none of the three was forwarded by `deploy/compose.yaml`
  before this package — will, after pulling this update and recreating its
  containers, go from "scheduler disabled, no automatic sweep at all" to
  "sweeping nightly from 03:00-07:00 (UTC unless `TZ` is also set), widening
  that sweep to every cached app, and actually deleting the orphaned chunks
  that widening produces." **All three defaults changed together, and the
  sweep half is not inert the way an earlier draft of this ADR described —
  it now runs on the shipped default window.** Disk usage patterns change:
  more bandwidth spent keeping cached-but-not-installed games current, and —
  the offsetting half — actual reclamation of the orphans that habit
  produces, where before this update nothing reclaimed them automatically at
  all. **Separately, and regardless of the window:** `VAULT_AUTO_GC=execute`
  also applies immediately to manual and miss-triggered prefills, which
  needed no schedule to begin with — this half of the change was never
  gated on the window even in the earlier draft.
- **An existing deployment that explicitly set any of the three variables**
  keeps its own explicit value; `${VAR:-default}`/`${VAR-default}`
  passthrough syntax in `deploy/compose.yaml` only changes what an *unset*
  variable resolves to. Nothing changes for an operator who already made an
  explicit choice here — including an operator who deliberately left
  `VAULT_SCHEDULE_WINDOW` blank to disable the scheduler: blank is a real,
  explicit value (not "unset"), and the no-colon form
  `${VAULT_SCHEDULE_WINDOW-03:00-07:00}` this package now uses does not
  touch it (see "The no-colon substitution fix" above — an EARLIER draft of
  this exact sentence claimed this while the colon form still shipped,
  which review round 2 measured as false; the claim is true now that the
  substitution itself is fixed).
- **An existing deployment using `PATCH /v1/settings` to override
  `sweep_include_cached`, `auto_gc`, or `schedule_window` in the database**
  keeps that override — ADR-0009's `db > env > default` precedence is
  unchanged by this package. A DB override continues to mean what it says
  regardless of what the env default now is.

**Night one on an upgraded install, stated plainly.** The first sweep after
upgrade enqueues one prefill per app in the union of "installed anywhere"
and "already cached" — for a library that has never been swept this can be
the entire tracked library. These run sequentially, one at a time, on the
single job worker (plan §3) — each one a real Steam login (~3 s for an
already-current app, real transfer time for one that is not) — and every
app an updating run touches queues a GC job behind it (`VAULT_AUTO_GC=execute`),
also sequential, on the same worker. There is no per-sweep cap on how many
apps one sweep enqueues (`VAULT_MISS_TRIGGER_MAX_PER_SWEEP` bounds the
EVENT-log miss trigger, WP 3.11, a different mechanism entirely — nothing
analogous exists for this scheduled sweep). This is by design, not an
oversight: plan A7/A8 and `scheduler.py`'s own "why enqueue-everything is
already the rate limiting" section treat the single-worker queue itself as
the throttle. It is still worth an operator with a very large library
knowing about on the specific night this ADR's defaults first take effect
for their install.

## How an operator opts out

Both keys remain ordinary, individually settable configuration — this ADR
changes only the *value nothing else resolves to*, not the mechanism:

- **Environment (persists across a lost `vault-db` volume, requires editing
  `deploy/.env`/`deploy/compose.yaml` and restarting `vault-api`):**
  ```
  VAULT_SWEEP_INCLUDE_CACHED=false
  VAULT_AUTO_GC=off
  ```
  Either line alone is enough to silence `cached_sweep_gc_risk`'s warning in
  its own direction; setting both back to their pre-ADR-0014 values restores
  the exact pre-upgrade sweep/auto-GC behavior. To also disable the
  scheduler itself (restoring the pre-2026-08-22 "no automatic sweep at all"
  state — this does NOT silence auto-GC on manual/miss-triggered prefills,
  see the schedule-window section above), blank the window explicitly:
  ```
  VAULT_SCHEDULE_WINDOW=
  ```
- **`PATCH /v1/settings` (immediately for `auto_gc`, next sweep for
  `sweep_include_cached`/`schedule_window` — see api/README.md "Which keys
  are overridable, and when a change takes effect"):** all three keys are,
  and remain, in `settings_store.OVERRIDABLE_SPECS` (ADR-0009). An operator
  can turn any or all of them off at runtime without touching `deploy/.env`
  at all, from the web UI's settings screen once it exists, or directly:
  ```
  curl -X PATCH -H "X-Api-Key: $VAULT_API_KEY" \
       -d '{"sweep_include_cached": "false", "auto_gc": "off", "schedule_window": ""}' \
       http://<server>:8080/v1/settings
  ```
- **`VAULT_SETTINGS_READONLY=1` closes a gap this package also had to close
  to make the opt-out above genuine.** `sweep_include_cached` (and, since
  the S3 fix round, `schedule_window`) being DB-overridable, and not
  forwarded through `deploy/compose.yaml`, was the deliberate design
  through WP P1 (`da79aca`) precisely because `PATCH /v1/settings` was
  considered the supported path to change it. That reasoning silently
  inverted the moment the *default* became "on": an operator running the
  documented, supported `VAULT_SETTINGS_READONLY=1` headless/GitOps mode has
  `PATCH` refused with `403` and, before this package, no `.env` line that
  reached either variable at all — a real deployment shape that would have
  had no way to turn the now-default-on cached sweep, or the now-default-on
  nightly schedule, back off. `deploy/compose.yaml` now forwards
  `VAULT_SWEEP_INCLUDE_CACHED` and `VAULT_SCHEDULE_WINDOW` alongside
  `VAULT_AUTO_GC` for exactly this reason (see that file's own comments on
  each key), pinned by `api/tests/test_p1_compose_env_defaults.py` the same
  way every other forwarded key already is.

## Consequences

- `vault_api/config.py`'s `DEFAULT_SWEEP_INCLUDE_CACHED` and `DEFAULT_AUTO_GC`
  constants change; their doc comments are inverted to state the new
  reasoning while keeping the original cost analysis intact rather than
  deleted (a reader should be able to see exactly what was weighed and
  which way the operator decided).
- `deploy/compose.yaml` gains a `VAULT_SWEEP_INCLUDE_CACHED` passthrough line
  next to `VAULT_AUTO_GC`'s (whose own default string also changes, `off` →
  `execute`), and `deploy/.env.example`'s existing "deliberately not settable
  from this file" passage — accurate while the default was `False` — is
  rewritten; leaving it in place would itself become the "advertised the
  opposite of what the code does" bug class this project has hit before
  (`docs/LEARNINGS.md` "Containers").
- `scheduler.cached_sweep_gc_risk` and `warn_once_if_cached_sweep_without_gc`
  are unchanged in code — their job is now guarding the half-configuration a
  future deviation (or an operator's own `PATCH`) could still produce, not
  the feature this ADR makes the default.
- (S3 fix round) `deploy/compose.yaml` gains `VAULT_SCHEDULE_WINDOW`
  (default `03:00-07:00`, no-colon substitution form — see "The no-colon
  substitution fix" above) and `TZ` (default `UTC`, colon form, forwarded to
  BOTH `vault-api` and `vault-runner` for log-timestamp consistency though
  only `vault-api` has scheduling logic) passthrough lines, both newly
  documented in `deploy/.env.example`. `GET /v1/schedule`'s existing
  `server_timezone` field was judged sufficient for the API surface (see
  the schedule-window section above for the reasoning); no new field was
  added there.
- (Review round 2 fix round) The resolved-schedule log line
  (`scheduler.describe_resolved_schedule`) moved from `start()` — logged
  synchronously against the boot-time `Settings` snapshot — to the
  scheduler thread's first `_tick()` call, logged against
  `effective_settings` (the SAME `db > env > default` resolution every real
  sweep decision already uses). Measured both directions (blocker R2-B2b):
  the boot-snapshot version could claim "DISABLED" while a DB-stored window
  was about to sweep with executing GC (the serious direction — this line
  is the mechanism this ADR offers as the reason unattended deletion is
  acceptable), or claim a window was active when a DB override had turned
  it off. The line also now prints the REQUESTED `TZ` value
  (`os.environ.get("TZ")`) alongside the resolved zone/offset (blocker
  R2-B2a, measured in the real image: `TZ=Europe/Berlinn`, a one-letter
  typo, silently resolves to a plausible-looking wrong zone named `Europe`
  at `UTC+00:00` rather than erroring — printing only the resolved side
  would never have shown anything wrong). A boot landing inside the window
  says the window is open rather than "next opening `<now>`" (N2,
  should-fix).
- **The complete, `git status`-confirmed file list this package touches,
  across every round** (this bullet's own history: the first draft said
  "every place in the tree", which review round 1 correctly called an
  unverified overclaim; the round-1 rewrite below then named
  `deploy/README.md` as corrected for content review round 2 had not yet
  required — a THIRD instance of the same completeness-claim failure, in
  the same ADR, about the same subject. This version is generated by
  reading `git status` for this package's actual diff, not composed from
  memory of what should have happened):
  - `api/vault_api/config.py` — the two `DEFAULT_*` constants and their
    doc comments.
  - `api/vault_api/scheduler.py` — module docstring, `compute_targets`
    docstring, `TargetSet` docstring (review round 1 blocker B2 plus the
    round-2 should-fix on the same parenthetical); `format_utc_offset`,
    `describe_resolved_schedule`, and the `_tick`/`start()`/`__init__`
    wiring for the resolved-schedule log line (round 2, blockers R2-B2a/b
    and should-fix N2) — all new in this package, not corrections of stale
    text.
  - `api/vault_api/worker.py` — `_maybe_queue_auto_gc`'s docstring (round 1
    blocker B2).
  - `api/vault_api/routers/schedule.py` — the `sweep_include_cached` field
    docstring (round 1 blocker B2); `_format_offset` de-duplicated onto
    `scheduler.format_utc_offset` (round 2 should-fix).
  - `api/.env.example` — both stanzas rewritten from live `off`/`off`
    assignments to `true`/`execute` (round 1 blocker B1 — this file does
    not merely describe the old defaults, it **sets** them), plus the new
    `config.DEFAULT_*`-derived value pin this file never had.
  - `api/tests/test_config.py`, `api/tests/test_job_control.py`,
    `api/tests/test_scheduler.py`, `api/tests/test_settings_api.py`,
    `api/tests/test_p1_compose_env_defaults.py` — the default-flip re-pins
    (round 1) and the schedule-window/TZ/offset-dedup/typo/db-override pins
    (round 2), detailed in this package's own test-change report.
  - `deploy/compose.yaml` — `VAULT_AUTO_GC`'s default string (round 1);
    `VAULT_SWEEP_INCLUDE_CACHED` forwarding (round 1); `VAULT_SCHEDULE_WINDOW`
    forwarding, first with the colon form then corrected to the no-colon
    form (round 2 blocker R2-B1); `TZ` forwarding on both `vault-api` and
    `vault-runner` (round 2, plus its own should-fix reasoning comment).
  - `deploy/.env.example` — the `VAULT_AUTO_GC`/`VAULT_SWEEP_INCLUDE_CACHED`
    stanzas and the first UPGRADE NOTE (round 1); the
    `VAULT_SCHEDULE_WINDOW`/`TZ` stanzas, the UPGRADE NOTE's three-line
    (not two-line) restore recipe, and the stale `PrefillScheduler.start()`
    cross-reference (round 2).
  - `deploy/README.md` — the Phase-3 knobs table's `VAULT_AUTO_GC` row and
    the "sixth" `VAULT_SWEEP_INCLUDE_CACHED` note, plus the 185→187 check
    count (round 1 — this much was genuinely done then, contrary to what
    review round 2 could confirm was still missing for the NEWER content
    below). The "seventh/eighth" `VAULT_SCHEDULE_WINDOW`/`TZ` note, the
    three-line restore recipe, and the 187→191→193 check-count updates are
    round 2 (blocker R2-B3 — this file had never mentioned either new
    variable, and the check count was stale against the reviewer's own
    191/191 and 193/193 measurements).
  - `deploy/tests/verify-stack.sh` — step 3e's `VAULT_AUTO_GC` assertion and
    the new `VAULT_SWEEP_INCLUDE_CACHED` checks (round 1); the
    `VAULT_SCHEDULE_WINDOW`/`TZ` checks and the new step 3e-bis
    (blank-value live proof, round 2 blocker R2-B1).
  - `README.md` — bullets 1 and 4, the staleness FAQ entry, the Roadmap
    settings-switch bullet, the ADR list (round 1).
  - `api/README.md` — the Configuration table, Auto-GC and
    Sweep-target-set sections, the `GET /v1/schedule` JSON example, the
    relay-privacy cross-reference (round 1); the Scheduler section's
    "Off by default" opening and the Timezone subsection, plus the
    strikethrough-Done fix to the stale "wiring TZ into Compose is a
    follow-up" line (round 2, blocker R2-B3).
  - `docs/PROJECT_PLAN.md` — present-tense prose only, in the sweep-mode
    section and §11 item 4 (round 1; its checklist ticks and evidence log
    remain the historical record of WP 4d's and WP 3.12's own decisions,
    which this ADR supersedes rather than rewrites).
  - `docs/adr/0014-sweep-cached-and-auto-gc-default-on.md` — this file,
    both rounds, including this bullet.

  If a future pass finds another stale spot this list does not name, extend
  the list — do not restore "every place in the tree" or any other summary
  phrasing that cannot be checked against a diff.
