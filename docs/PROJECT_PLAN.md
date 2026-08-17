# SteamVault — Project Plan

A Steam game cache with true per-game management — self-hosted, Docker-first.

Status: IMPLEMENTATION — Phases 0–3 and 4a complete, Phase 4b at 4b.8 of
4b.9; pre-release (see §11 Next Steps) · License: Apache-2.0

> SteamVault is a community project and is not affiliated with Valve Corporation.
> "Steam" is a trademark of Valve Corporation.

---

## 1. Vision & Problem Statement

LanCache is the de-facto standard for LAN caching of game downloads, but it has
a well-known structural limitation: the cache is a generic nginx HTTP cache
(files stored under hashed cache keys), which makes **deleting individual games
from the cache impossible**. Community tools like lancache-manager reconstruct
the game-to-file mapping after the fact by parsing access logs — clever, but
error-prone and maintenance-heavy.

SteamVault inverts the approach: the **depot ID is already part of the Steam
CDN URL** (`/depot/<depotid>/chunk/<hash>`). By storing the cache path-faithfully
(nginx `proxy_store` instead of `proxy_cache`), the game mapping becomes part of
the directory structure from the start. Deleting a game = deleting its depot
folders. No log parsing, no key reconstruction, no heuristics.

**Target audience:** Homelab operators and LAN party organizers who want to know
which game occupies how much space — and want to clean up selectively.

**Deliberate scope cut:** Steam only. No Epic/Battle.net/Riot multi-service
support like LanCache — this keeps the URL-schema problem manageable and the
project focused. (Extensibility via a plugin architecture is kept open, but not
for v1.)

---

## 2. Requirements

| # | Requirement | Component |
|---|---|---|
| A1 | Prefill Steam games onto the server remotely | Backend + Prefill |
| A2 | Serve downloads at LAN speed from the cache at home | Cache Core |
| A3 | Browse the Steam library visually in an Android app (covers, names) | Android App |
| A4 | Per-game cache status badge (cached / running / not cached) | Backend + App |
| A5 | Per-game download trigger from the app (start → done status) | Backend + App |
| A6 | App "homecall" works over Tailscale (embedded tsnet), Twingate, or a public domain — user-selectable connectivity profile | Android App |
| A7 | Prefill updates automatically during the day (cron) | Scheduler |
| A8 | Cron criterion: games actually installed on the gaming machines (Windows PC and SteamOS/Linux devices such as Steam Deck / Steam Machine); removals are detected and reflected | PC Agent |
| A9 | Delete individual games from the cache to free up space | Cache Core + Backend |
| A10 | Per-game size overview | Backend |
| A11 | Community-ready: Docker-first, documented, licensed, CI | Project Infra |
| A12 | Detect clients silently bypassing the cache (DoH/DoT, IPv6, Linux client quirks) and surface it | Backend |
| A13 | Reclaim space from outdated chunks per game (manifest-based garbage collection) | Backend |
| A14 | Works without a pre-existing local DNS server (bundled optional DNS container, or DNS-free hosts-file mode) | vault-dns / PC Agent |

---

## 3. Architecture

```
                    ┌─────────────────────────────────────────┐
                    │            Cache Server                 │
   Android App      │                                         │
  ┌────────────┐    │  ┌─────────────┐    ┌───────────────┐   │
  │ Compose UI │    │  │ vault-core   │    │ vault-api     │   │
  │ Steam Lib  │────┼─▶│ nginx        │    │ FastAPI       │◀──┼── PC Agent
  │ tsnet      │    │  │ proxy_store  │◀───│ SQLite        │   │   (gaming PC,
  └────────────┘    │  │ /cache/depot/│    │ prefill ctrl  │   │    reports
        │           │  └─────────────┘    └───────┬───────┘   │    installed
        │           │         ▲                   │           │    games)
        ▼           │         │           ┌───────▼───────┐   │
  Steam Web API     │   DNS rewrite       │ SteamPrefill  │   │
  (library+covers,  │   *.steamcontent    │ (subprocess/  │   │
   regular internet)│   .com → server     │  container)   │   │
                    └─────────────────────────────────────────┘
```

### Components

**vault-core** — the cache itself
- nginx container with `proxy_store`: Steam CDN responses are stored
  path-faithfully under `/cache/depot/<depotid>/...`
- No LRU, no automatic eviction — cleanup is deliberately explicit
  (that's the feature, not the flaw)
- A lean, purpose-built nginx config set; NOT a LanCache fork — only the
  DNS-redirection principle is shared (rewrite `*.steamcontent.com` to the
  cache server via any local DNS: AdGuard Home, Pi-hole, dnsmasq, ...)

**vault-dns** — optional bundled DNS (for users without a local DNS server)
- dnsmasq container, enabled via a Compose profile (`--profile dns`)
- Answers `*.steamcontent.com` with the cache IP, forwards everything else
  to a configurable upstream. IMPORTANT (verified in Phase 0): modern
  dnsmasq (2.9x) FORWARDS non-matched record types upstream, so `address=`
  alone leaks AAAA answers and IPv6-capable clients silently bypass the
  cache — the zone must additionally be declared `local=/steamcontent.com/`
  so AAAA queries get a local NODATA answer. Required design element for
  vault-dns.
- Not needed if the user already runs AdGuard Home, Pi-hole, dnsmasq or
  Unbound — a rewrite there does the same job (recommended for homelabs)

**vault-api** — brain & API
- FastAPI + SQLite (single file, no DB container — deliberately simple
  for easy adoption)
- Responsible for:
  - Depot→app mapping (from SteamPrefill data / Steam PICS)
  - Prefill orchestration (SteamPrefill as subprocess/sidecar, job queue)
  - Per-app status tracking (idle / running / done / error / stale)
  - Per-game size calculation (du over depot folders, cached)
  - Per-game deletion (remove the app's depot folders)
  - Scheduler (configurable daytime window, runs over the installed list)
- REST API (see section 5)

**vault-agent** — PC listener
- Small static Go binary (ADR-0005: single-file distribution, trivial
  cross-compilation for windows/amd64, linux/amd64, linux/arm64) on the
  gaming machine — Windows PC first, plus a Linux/SteamOS variant
  (Steam Deck, Steam Machine) in Phase 2
- Reads `steamapps/appmanifest_*.acf` from all library folders
  (parses `libraryfolders.vdf` for multiple drives); the ACF/VDF format
  is identical on Linux/SteamOS, only library paths and packaging differ
  (XDG paths under `~/.local/share/Steam`, systemd user service instead
  of a scheduled task)
- Reports the FULL list of installed app IDs periodically (e.g. every
  30 min) via HTTP POST to vault-api — over Tailscale. Removed titles
  are derived server-side by diffing against the previous report — the
  agent stays stateless and dumb by design
- Runs as a scheduled task / optional tray icon; config: one URL + API key
- Deliberately dumb: read + report only, no control logic
- **Optional hosts-file mode (opt-in, requires admin rights):** writes a
  `lancache.steamcontent.com → cache IP` entry into the Windows hosts file.
  The Windows Steam client checks this hostname itself and uses it as a
  cache when it resolves — no DNS server needed at all. Windows-only
  (the Linux/Steam Deck client does not perform this lookup).
  *Note: this hostname is hardcoded by Valve in the Steam client and lives
  on Valve's own `steamcontent.com` domain — it is the client's built-in
  cache-discovery interface, not a LanCache-project dependency. It cannot
  be renamed.*

**vault-app** — Android app
- Kotlin + Jetpack Compose
- Steam identity via "Sign in with Steam" (OpenID against Valve's login
  page — the app never sees credentials, see ADR-0004); library + covers
  via Steam Web API (`GetOwnedGames`, device-local user-owned API key,
  regular internet, never proxied through vault-api)
- **Connectivity profiles** (user-selectable, abstracted behind one API-client
  interface — the server never knows or cares which one is used):
  - **Embedded Tailscale (tsnet):** Go Mobile `.aar` bridge, auth-key based.
    Zero-config for the user beyond pasting an auth key. Tailscale only.
  - **System VPN:** plain HTTPS to an internal hostname/IP; works with the
    Tailscale app, Twingate client, WireGuard, or any other VPN the OS
    provides. (Twingate has no embeddable SDK — this profile covers it.)
  - **Public domain:** plain HTTPS to a public URL fronted by the user's
    reverse proxy (Traefik, Caddy, Nginx Proxy Manager, Cloudflare Tunnel).
    Requires TLS; strongly recommends forward-auth/OIDC in front of the API
    in addition to the API key.
- Grid view with status badges, multi-select, trigger, polling until "done",
  delete function with size display ("Game X occupies 43 GB — delete?")

---

## 4. Cache Design (Core Innovation)

### Storage layout
```
/cache/
└── depot/
    ├── 441/                    ← depot ID (belongs to app 440, TF2)
    │   └── chunk/
    │       ├── <sha>...
    │       └── <sha>...
    ├── 442/
    └── manifest/               ← manifest responses stored separately
```

### Depot→app mapping
- A game consists of multiple depots (content, languages, DLC)
- Source: Steam PICS via SteamKit — SteamPrefill already uses this;
  vault-api keeps its own mapping table in SQLite and updates it during
  prefill (SteamPrefill knows the mapping at download time anyway)
- Fallback: manual mapping via the API (edge cases / delisted games)

### Deletion
```
DELETE /cache/{appid}
  → mapping: appid → [depotids]
  → rm -rf /cache/depot/<each depotid>
  → reset status to "idle"
```
Shared depots (redistributables, shared content): before deleting, check
whether a depot ID is mapped to multiple tracked apps → skip those and
report them in the result ("2 depots shared with game Y, not deleted").
Exception (ADR-0003 addendum): a shared depot whose co-owning apps ALL have
no cache content (idle, never prefilled, no active job) is the last cached
remnant — it IS deleted and reported distinctly, otherwise its bytes would
be unreclaimable forever once every co-owner has been deleted.

### Staleness / updates
- vault-api stores the manifest ID of the last prefill per app
- The scheduler periodically compares against the current manifest ID
  (Steam API) → if it differs: status "stale", an update prefill fetches
  only the changed chunks
- App badge logic: green=current, yellow=running, orange=stale, gray=not cached

---

## 5. Known LanCache Pain Points SteamVault Addresses

Documented community issues (GitHub issues, Steam forums, LanCache docs) that a
Steam-only, prefill-first design can solve better:

| Pain point | How SteamVault addresses it |
|---|---|
| **No per-game visibility or deletion** — cache is opaque hashed storage | Core design: path-faithful depot storage, per-game size, per-game delete |
| **Slow cache-miss downloads** — nginx slice mechanics + CDN back-off behave poorly with the Steam client; users resort to multi-IP workarounds | **Prefill-first philosophy, hybrid miss path (Phase 0 decision, ADR-0001):** misses are stored synchronously (`proxy_store`, no slice mechanics — measured overhead within noise) AND trigger an async prefill job that completes the affected app. Prefill remains the primary fill mechanism |
| **Clients silently bypassing the cache** — Linux/Steam Deck clients not honoring `lancache.steamcontent.com`, DoH/DoT ignoring local DNS, ISP DNS hijacking | **Bypass detection:** vault-api tracks per-client hit statistics; a client that reports installed games (agent) but never appears in cache logs triggers a visible warning in app/API. Setup docs cover the Linux-client and DoH caveats explicitly |
| **IPv6 undermines DNS redirection** — clients resolve AAAA records of the real CDN and bypass the cache | Documented stance + setup guidance (block/rewrite AAAA for `*.steamcontent.com` in the local DNS); bypass detection catches the failure case |
| **Stale chunks waste space forever** — a generic HTTP cache never learns that a game update obsoleted old chunks | **Manifest-based garbage collection:** per depot, diff cached chunks against the current manifest and delete orphans — only possible because storage is depot-structured |
| **No insight without extra tooling** (Grafana/ELK stacks or lancache-manager needed) | Stats are first-class API citizens: summary, per-game, per-client — the app is the dashboard |

---

## 6. API Design (vault-api)

| Method | Endpoint | Purpose |
|---|---|---|
| GET | /v1/games | All tracked games: status, size, last prefill |
| GET | /v1/games/{appid} | Detail incl. depot list |
| POST | /v1/prefill | Body: `{appids: [..]}` → create jobs (response marks deduplicated entries) |
| GET | /v1/jobs | Recent jobs, newest first (app polling UI) |
| GET | /v1/jobs/{id} | Job status (for app polling) |
| DELETE | /v1/jobs/{id} | Cancel a queued or running job (Phase 3, user decision 2026-08-06) |
| POST | /v1/jobs/{id}/pause | Pause a running download — terminates the subprocess; cached chunks make resume cheap |
| POST | /v1/jobs/{id}/resume | Resume: re-runs the prefill; already-cached chunks replay as disk-speed HITs |
| GET | /v1/schedule | Scheduler state: window, interval, last/next sweep |
| DELETE | /v1/cache/{appid} | Delete a game from the cache |
| POST | /v1/cache/{appid}/gc | Garbage-collect orphaned chunks (manifest diff) |
| GET | /v1/cache/summary | Total usage, top consumers, unmapped depots, free space |
| GET | /v1/oracle/{appid} | Oracle snapshot: depots, branch manifest gids, staleness hints (WP 3.9; 404-equivalent `enabled:false` when oracle off) |
| POST | /v1/oracle/{appid}/refresh | Explicitly re-query the manifest oracle for one app (never automatic; `ok:false` on oracle failure, never 5xx) |
| DELETE | /v1/oracle/{appid} | Drop stored oracle data for one app |
| GET | /v1/clients | Per-client hit stats incl. bypass warnings |
| GET | /v1/stats | Cache-event sweep state: cursor, lines read/skipped, miss-trigger activity, unmapped depot misses (WP 3.11, ADR-0008) |
| POST | /v1/agent/installed | PC agent reports installed app IDs |
| PUT | /v1/mapping/{depotid} | Manual depot→app mapping fallback (additive, see ADR-0003) |
| GET | /v1/mapping | List all depot→app mappings |
| DELETE | /v1/mapping/{depotid}/{appid} | Remove one mapping pair (repair path) |
| GET | /v1/health | Liveness (for external monitoring) |

Auth: static API key in a header (v1). Since everything is only reachable
over the tailnet, this is sufficient; OIDC/forward-auth is a later option
for users who expose the API differently.

---

## 7. Phase Plan

### Phase 0 — Feasibility PoC (CRITICAL, before anything else)
**Goal: verify the central assumption before writing product code.**
- [x] Test setup: nginx with `proxy_store` + hosts-file redirect
      *(run natively on Windows instead of a container — deliberate decision:
      develop natively first, containerize at end of Phase 1; see `poc/`)*
- [x] Route a real Steam download through it and verify:
  - [x] Does Steam consistently use the `/depot/<id>/chunk/<hash>` scheme?
        *(2026-08-04: 187 real-client requests, 100% chunk/manifest
        conformance, zero non-conforming URIs — see
        `poc/steam-client-test/RESULTS-*.md`)*
  - [x] Do range requests work cleanly with `proxy_store`?
    (known risk: `proxy_store` only stores complete responses —
    may need the `slice` module, or chunks may be small enough)
    *(2026-08-04: the real Windows client sent ZERO Range headers
    (~1 MiB chunks, full-body GETs); the CDN edge tested ignores Range
    on miss and always returns full 200; warm-cache ranges served
    natively. Production follow-up: strip client Range upstream +
    store-only-200 guard — see `poc/RANGE-FINDINGS.md`)*
  - [x] Cache hit on second download? Speed LAN-limited?
        *(2026-08-04: 93/93 HIT after uninstall/reinstall, zero upstream
        contact, disk-limited — 81.8 MiB served in ~1 s)*
  - [x] **Miss-handling decision:** synchronous store vs. transparent
    passthrough + async prefill (see pain-points section) — measure both
    *(measured in `poc/MISS-HANDLING-FINDINGS.md`: perf difference within
    noise. 2026-08-05 gate decision: HYBRID — store-on-miss (Phase 1)
    plus miss-triggered prefill completion (Phase 3). See ADR-0001)*
- [x] Run SteamPrefill against the PoC cache — does it fill correctly?
      *(2026-08-04: SteamPrefill v3.7.1 auto-detected the cache via the
      lancache-heartbeat contract and prefilled path-faithfully — layout
      cross-check PASS, repeat requests served as HITs. Notable: 0 manifest
      requests through the cache, and `?nocache=1` speed probes that
      production vault-core should honor as a cache bypass — see
      `poc/steamprefill/RESULTS-STEAMPREFILL-*.md`)*
- [x] Verify behavior of the Linux/Steam Deck client (known upstream quirk:
      does not perform the `lancache.steamcontent.com` lookup like Windows)
      *(2026-08-05, Ubuntu 26.04/WSL2, current stable client: the quirk is
      OUTDATED — the Linux client DOES perform lancache discovery and sent
      3574 requests through the cache with real CDN Host headers, zero
      Range headers. Cross-client sharing confirmed: chunks cached by the
      Windows client served to the Linux client as HITs. A transient
      upstream-IP outage also produced 502/stall evidence motivating
      production upstream design (honor client Host header, short
      connect timeouts, retry) — see
      `poc/linux-client-test/RESULTS-20260805-083353.md`)*
- **Abort criterion:** If `proxy_store` fails on range requests with no clean
  workaround → fall back to Plan A (unmodified LanCache + a manager layer in
  the spirit of lancache-manager; the rest of the project stays usable as-is)

### Phase 1 — vault-core + vault-api (server MVP)
- [x] Production-ready nginx config (log rotation, healthcheck) —
      implements the Phase-0 requirements from ADR-0001: lancache
      heartbeat, Range-strip + 200-only store guards (incl. retried-200
      handling), nocache=1 bypass, client-Host upstream with short
      timeouts/retry, store-on-miss
      *(caveat: log rotation is documented, implemented with the
      container in WP 1.9; tmp/ and cache/ must share one volume)*
- [x] FastAPI skeleton, SQLite schema, depot mapping import
      *(WP 1.2/1.3: skeleton with secure-by-default auth, schema v1,
      manual mapping endpoints per ADR-0003; the prefill-driven mapping
      import lands with the orchestration in the next package)*
- [x] Prefill orchestration (SteamPrefill subprocess, job queue, one job at a time)
      *(WP 1.4: selection-file driven — SteamPrefill has no app-id CLI;
      hybrid miss-trigger itself remains Phase 3 per ADR-0001)*
- [x] Size calculation + deletion incl. shared-depot protection
      *(WP 1.5/1.6: TTL-cached sizes; deletion with path guards,
      link-safe removal, execute-time shared recheck closing the
      TOCTOU, settle-and-recheck against racing deletes; mapping rows
      survive deletion by design. Opus + Fable double review)*
- [x] Docker Compose (2 services + volume), .env convention, pinned image tags
      *(WP 1.9: tag+digest-pinned images, preflight guards, json-file log
      rotation, SteamPrefill v3.7.1 with TOFU checksum and a dedicated
      HOME volume, 62-check container verification incl. real CDN
      MISS/HIT through the Linux container. Opus + Fable double review)*
- [x] Optional vault-dns container behind a Compose profile (`--profile dns`)
      *(WP 1.9: envsubst entrypoint with IPv4 validation and address=/
      local= re-assertion, fail-closed loopback default binding)*
- [x] **MVP test: prefill a game via curl, query its size, delete it — no app**
      *(2026-08-05, WP 1.7: full HTTP-only cycle against the real Steam
      CDN through the resident vault-core — prefill 79.7 MiB via job
      queue, size and summary internally consistent to the byte,
      deletion freed exactly the app's size, disk verified. Evidence:
      `core/tests/mvp/RESULTS-*.md`. Note: unattended real-account
      prefills are blocked by the local permission classifier — the
      run is operator-executed by design)*

### Phase 2 — vault-agent (PC listener) — COMPLETE 2026-08-09
- [x] ACF/VDF parser (appmanifest + libraryfolders, multiple drives)
      *(WP 2.1 Python reference with spec-parity pins, ported to Go in
      WP 2.1b per ADR-0005; reference implementation removed at close-out
      (WP 2.6), embedded EAppState bit table preserved in go/acf/acf.go)*
- [x] HTTP reporter with retry (tolerate VPN/network outages)
      *(WP 2.2: 46/46 validation parity, ctx-cancellable backoff, 429
      retryable, no secret in flag defaults)*
- [x] Optional hosts-file mode (opt-in, admin rights, clean uninstall path)
      *(WP 2.3: marker-block management with byte-exact preservation,
      fail-closed backup, measured Windows ACL write strategy, UTF-16/
      symlink refusal, SIGKILL/ENOSPC fault-injection evidence. Note:
      Phase 0 proved CURRENT Linux clients also perform lancache
      discovery, so hosts mode works beyond Windows; Opus + Fable
      double review. Real-Windows verification done 2026-08-06: hosts
      status on the live machine correctly reported the user's manual
      Phase-0 entry as a conflict without touching anything)*
- [x] Document Windows scheduled-task setup, optional installer script
      *(WP 2.6: per-user task via install-task.ps1/uninstall-task.ps1,
      API key in an owner-only env file, never on the command line
      (verified with a canary key against schtasks + task XML),
      icacls-before-write ACL, idempotent re-install, 36-assertion
      real-machine harness; Python reference implementation removed per
      ADR-0005 addendum, fixtures kept, EAppState bit table preserved
      in go/acf/acf.go)*
- [x] Linux/SteamOS agent variant (Steam Deck / Steam Machine): library
      discovery under `~/.local/share/Steam`, systemd user service
      packaging, SteamOS read-only-rootfs-friendly install (home dir only)
      — see ADR-0002
      *(WP 2.5: OnCalendar=*:0/30 + Persistent=true (monotonic timers
      proved a silent no-op for catch-up), umask-077-first secret env
      files)*
- [x] vault-api: scheduler uses the installed list as the prefill set;
      server-side diff of consecutive agent reports surfaces removed
      titles (status update / optional cleanup hint, see ADR-0002)
      *(WP 2.4 report chains ordered by rowid + WP 3.5 window sweeps)*

### Phase 3 — Scheduler & Update Logic — COMPLETE 2026-08-09

All packages merged: 3.1–3.6 (manifests, ingestion, summary, needs_force,
scheduler, last-remnant), 3.7/3.8/3.8b (GC plan/execute/grace window),
3.9 (oracle, branch session), 3.10 (event log), 3.11 (event sweep),
3.12 (job control), 3.13 (webhooks). Suite at close: 1239 green.

Closing plan (2026-08-09, user decision "implement everything"): remaining
packages are 3.8b grace window (merged) · 3.9 oracle incl. open-beta
branch manifests (ADR-0007 addendum B) · 3.10 vault-core cache-event log
(ADR-0008) · 3.11 event sweep = miss-trigger + client hit stats/bypass ·
3.12 job control + optional auto-GC · 3.13 webhooks. api/-packages run
strictly serially; 3.10 (core/) runs in parallel.

Branch-dispatch structure for ALL remaining phases (package briefs,
parallel/serial decisions, merge discipline, open user decisions):
`docs/WORKPACKAGES.md` (2026-08-09).

- [x] Miss-triggered prefill completion: a cache miss on an unknown/partial
      app queues a prefill job for that app (hybrid decision, ADR-0001;
      feed design decided in ADR-0008 → WP 3.10 core log + WP 3.11 sweep)
      *(WP 3.11: non-forced enqueues under cap → active-job → cooldown →
      cached-and-current guards; unmapped/shared/manifest misses counted,
      never triggered)*
- [x] Job outcome honesty: a prefill run that observed zero depots and has
      zero cached bytes must not end 'done' (WP 1.7 finding: SteamPrefill
      exits 0 for unowned apps → green badge for a never-cached game)
      *(WP 3.3: summary parsing with digits-and-separators row detection,
      cp850-safe decode chain, 0/0 ⇒ error)*
- [x] Job control: pause/resume/cancel (`DELETE /v1/jobs/{id}`,
      pause = terminate the SteamPrefill subprocess, resume = re-run —
      already-cached chunks replay as disk-speed HITs, so the cache
      itself is the progress store; user feature decision 2026-08-06)
      *(WP 3.12: distinct 'cancelled' terminal + 'paused' in
      ACTIVE_STATUSES (a paused prefill keeps protecting its shared
      depots), stop_request as a DB column cleared at every
      terminal/parking transition, pause RELEASES the worker slot with
      resume priority via the original job id (documented mockup
      divergence — UI follows backend), cooperative GC cancellation,
      VAULT_AUTO_GC hook, strict numeric env grammar (6 int + 2 float
      settings, nan/inf rejected); schema v8; suite 895 green; Opus
      PASS + Fable PASS)*
- [x] Manifest comparison (stale detection)
      *(ADR-0006: staleness via non-forced prefill (Tier 1), depot_manifests
      latest-per-(app,depot) from WP 3.2 ingestion, needs_force lifecycle
      with CAS-protected clear from WP 3.4)*
- [x] Configurable cron window (e.g. 09:00–17:00, every 3 h)
      *(WP 3.5: schedule_window parsing incl. overnight windows + 24:00 end,
      OnCalendar-style sweeps from agent reports)*
- [x] Manifest-based garbage collection (`/v1/cache/{appid}/gc`, optional
      auto-GC after successful update prefill)
      *(auto-GC half CLOSED — the earlier "still open" note below is
      superseded: WP 3.12 shipped `VAULT_AUTO_GC=off|dry-run|execute`
      (`worker.py::_maybe_queue_auto_gc`, re-read per job so a
      `PATCH /v1/settings` applies without restart, key exposed by
      ADR-0009 and wired through `deploy/compose.yaml` in WP D1). This
      is NOT a Phase-4d blocker any more)*
      *(WP 3.7 done: read-only GC core — `plan_gc()` with exact on-disk
      orphan bytes, shared-depot UNION keep sets, ADR-0007 readiness gate,
      uncached-app exclusion per ADR-0007 addendum, stored-manifest dedupe
      candidates; 86 tests, mutation-tested fail-closed directions,
      verified against the real PoC cache (0 orphans, research-doc parity).
      Endpoint + execution = WP 3.8, Opus + Fable mandatory)*
      *(WP 3.8 done: POST /v1/cache/{appid}/gc as queued job, dry-run by
      default at every layer (StrictBool opt-in, NULL job rows read as
      dry-run), execute-time re-plan by construction (run_gc takes no plan),
      WP 1.6 guard path reused (safe_child_path, remove_file_settling),
      keep-newest dedupe with byte-identity check, needs_force for touched
      depots' owners, partial-failure honesty; schema v7; 687 tests green;
      Opus PASS + Fable PASS. Optional auto-GC after update prefills still
      open)*
- [x] Beta-branch protection for GC (user decision 2026-08-09, ADR-0007
      addendum): (A) recently-stored grace window via st_ctime as a
      ChunkExclusion predicate; (B) open-beta branch manifests join the
      keep set when the oracle (WP 3.9) is enabled
      *(A: WP 3.8b merged. B: WP 3.9 done — opt-in manifest oracle
      (`VAULT_MANIFEST_ORACLE=steamcmd_api`, default OFF, mutation-pinned),
      own tables `oracle_app_state`/`oracle_branch_manifests` (schema v10,
      `depot_manifests` never written — test-pinned), passworded/unknown
      branches never stored, `public` excluded from the keep set, additive
      post-gate union in `gc.py` via generic `extra_manifest_ids` (no oracle
      import; orphans(on) ⊆ orphans(off)), fail-soft everywhere incl. broad
      catch so an oracle error can never fail a GC job; stdlib urllib with
      redirects refused + bounded read; three authed `/v1/oracle/*` routes.
      Renumbered twice by rebases: WP 3.12 took v8, WP 3.11 took v9, so the
      oracle tables are v10 — harmless, they add no column to either. 1193
      tests green, 17 mutations killed by named tests, reviewer re-verified
      with its own mutation set; Opus PASS ×3. Note: response shape modeled on
      api.steamcmd.net, not yet verified against the live service — mismatch
      degrades to oracle-off, documented in api/README)*
      *(WP 4a.6 done 2026-08-11: web settings view over GET/PATCH
      /v1/settings — per-key source/applies captions, only-changed-keys
      PATCH bodies (mutation-pinned), per-field reset, readonly banner;
      Steam identity per A+C — key entry/removal with masked last4,
      typed key cleared from DOM on every path (pinned), SteamID64
      validation with literal boundary fixtures incl. the BigInt
      0x-hex kill, library preview with 409 setup guidance, ADR privacy
      note; 3-step onboarding overlay with dialog semantics (focus trap
      deferred to 4a.8, recorded); serverUrl setting removed (same-origin
      by design); demo parity programmatically diffed against live
      responses; 242 headless tests; Opus PASS + should-fix round)*
- [x] Per-client hit statistics + bypass detection (`/v1/clients`)
      *(WP 3.11, ADR-0008: event sweep with a persisted byte-offset cursor,
      strict 9-field v1 parsing, miss-triggered prefill (cooldown + per-sweep
      cap + unmapped/shared/manifest never trigger), per-address hit stats,
      bypass detection failing toward NOT accusing; schema v9; new
      `GET /v1/stats`. Rotation is best-effort — in the shipped containers
      vault-api may read the log but not truncate it, which is handled,
      counted and surfaced rather than fatal)*
- [x] Optional generic webhook notifications (built as a generic webhook
      feature, not vendor-specific. NOTE: the original wording claimed
      "Discord/Slack/ntfy-compatible" — that overstates what shipped.
      Generic JSON receivers and n8n consume the envelope directly;
      Discord requires `{"content": …}`/`embeds` and needs a relay until
      the Phase 6 format adapters land)
      *(WP 3.13: five events — job.done/error/cancelled +
      client.bypass_suspected/resolved (transition-only, state tracked in
      both directions regardless of the delivery filter) — one generic
      JSON schema, single delivery daemon thread with bounded drop-oldest
      queue, measured zero worker latency against hanging receivers,
      userinfo→Authorization-header conversion with redacted logging;
      schema v10; suite 1102 green; Opus PASS + fix round)*

### Phase 4 — Frontends (user decision 2026-08-06: web first, then app)

#### Phase 4a — Web UI (served by vault-api as static files) — COMPLETE 2026-08-11

All packages merged: 4a.1 (shell/static serving), 4a.2 (client/store),
4a.3 (library), 4a.4 (detail sheet + delete/GC), 4a.5 (downloads),
4a.6 (settings/onboarding/Steam identity) + 4a.6r (relay, api/),
4a.7 (notifications/bypass), 4a.8 (e2e + a11y). Suite at close: 379
headless web tests + 1461 api tests green. Every package: Sonnet coder
→ Opus review with independent mutation batteries → PASS. Recorded
mockup divergences live in WORKPACKAGES.md (user veto welcome).
Remaining for real-world validation: the honest still-open list in
web/tests/README.md (real screen reader, phone browser cover art,
Zeus-scale library) — the Zeus rollout session covers it.

- [x] SPA sharing the mockup's design language; zero extra deploy
      complexity (vault-api serves it; works over Tailscale/LAN and on
      phone browsers immediately)
      *(WP 4a.1 done 2026-08-10: vault-api serves `web/` via exact GET+HEAD
      routes + /css,/js StaticFiles mounts — /v1 routing parity pinned
      empirically against a pre-change baseline (trailing-slash 307s, 405s);
      strict CSP with zero inline script/style in web/; app shell with
      3-view nav per frozen mockup, status-icon component incl.
      reduced-motion, toasts; traversal pinned incl. %2e%2e forms; suite
      1281 green; Opus FAIL→fix→PASS. Docker packaging of web/ is a
      documented known gap until the packaging WP)*
- [x] Same API surface as the app: library grid with badges + search,
      download-to-cache/delete flows, jobs view, settings incl. vault name
      *(WP 4a.2 done 2026-08-10: fetch wrapper with X-Api-Key + six-kind
      error taxonomy + demo mode (fixtures 1:1 with the real Pydantic
      shapes incl. ADR-0003 shared-depot semantics in demo deletes);
      polling store with jobs-fast/games-clients-slow cadence, backoff
      with load-bearing jitter floor, hidden-tab full park, nudge
      coalescing + generation token (fork-free under nudge storms, pinned
      headless); pure notification differ per mockup NOTES (first poll
      silent, cancelled silent, update_ready gated on cached bytes,
      bypass both directions); 60 headless Node tests; Opus
      FAIL→fix→PASS with 10-mutation battery)*
      *(WP 4a.3 done 2026-08-10: library view — grid 2/3/list, ANDed
      search+chips (Failed replaces Update ready until the stale field
      exists — recorded divergence in WORKPACKAGES.md), capsule pills,
      multi-select with mockup-faithful bulk-download split and set-aware
      multiPlan bulk delete (fail-closed on unresolvable owners), real
      Steam cover art behind an exact-value CSP img-src pin with offline
      fallback tiles, and round-7 patch-in-place for games ticks
      (render-plan.js; node-identity verified live, icon subtree
      untouched by instrumented proof); 135 headless Node tests; Opus
      FAIL→fix→PASS, 7/7 render-plan + 16-mutation battery killed)*
      *(WP 4a.5 done 2026-08-10: downloads view — Active/Paused as
      independent sections per the recorded slot-release divergence
      (paused holds no slot, queue hint says so), FIFO queue with
      positions, history newest-first with lazy-fetched cached log
      excerpts, pause/resume/cancel non-optimistic with error-taxonomy
      toasts, nav queue-pip with aria-label, new neutral 'cancelled'
      icon kind (recorded divergence), jobs-tick patch-in-place
      (stop_request drift patches, status change rebuilds); 173 headless
      Node tests; Opus PASS, 12/12 mutation battery killed)*
      *(WP 4a.7 done 2026-08-11: bell + panel consuming the 4a.2 differ
      (no re-derivation — first-poll-silent inherited), unread badge
      clears on panel open per NOTES round 6, literal-pinned
      navigate-to-target via the router incl. downloads job highlight;
      app-shell bypass banner with WP 3.11's not-accusing wording;
      clients sheet on real /v1/clients with patch-in-place both
      directions pinned and the 4a.6 dialog semantics; bell-in-topbar +
      session-only log recorded as divergences; original coder hung in
      live verification and was replaced by a finisher who fixed two
      real gaps; 286 headless tests; Opus PASS, 13/13 mutations)*
      *(WP 4a.4 done 2026-08-11: game detail sheet on the 4a.7
      sheet-dialog — four-state depot sharing (adopts the recorded
      ORPHANED divergence), survives-deletion wording branch, job
      control incl. queued, single-game delete via multiplan with
      server-reported bytes, GC dry-run→plan→confirm→execute porting
      the reviewed Android reducer (execute only via plan+confirm,
      job-id-bound generation-guarded polling — fork-free), GC-totals
      scrape with load-bearing after-header scoping and \b insurance
      verified against gc_execute.py; update_ready notifications now
      target the detail sheet (4a.7 TODO closed); includes the tracked
      library.js mounted() fix (empty-grid-on-renavigation regression);
      demo games routes gained the missing last_manifest_check; 351
      headless tests; Opus PASS, 15/15 mutations)*
- [x] Steam identity via Sign in with Steam (ADR-0004); library fetch per
      the 2026-08-09 addendum (user decision A+C): opt-in server-side relay
      because the Steam Web API sends no CORS headers — Android keeps the
      device-local path
      *(WP 4a.6r done 2026-08-10: opt-in relay — GET/PUT/DELETE
      /v1/steam/key (32-hex validated, masked key_last4, never echoed) +
      /v1/steam/owned-games + /v1/steam/player-summaries, all authed;
      schema v12 single-row key table; oracle-style outbound HTTP with
      host/scheme/path pinned by string-literal tests against the captured
      Request; strict steamid64 + upstream-shape validation; 256-entry TTL
      cache keyed per (endpoint, steamid), cleared on key change; 92 relay
      tests, 13-mutation review set killed; suite 1375 green; Opus
      FAIL→fix→PASS. Web-UI consumption follows in WP 4a.6)*

#### Phase 4b — Android App — 4b.1–4b.8 MERGED 2026-08-11

Open: WP 4b.9 (release build/signing/APK docs + the review carry-over list
in `docs/WORKPACKAGES.md`), tsnet (post-v1 by decision), a clients/bypass
detail surface (4b.8 routes bypass notifications to Settings for now), and
the honest on-device list in `app/README.md` — no emulator or phone was
available in any implementation session. Suite at close: 534 JVM tests,
lint clean, `assembleDebug` green.

- [x] Kotlin/Compose project, Steam Web API integration (library + covers)
      *(WP 4b.1 done 2026-08-10: self-contained app/ Gradle project —
      pinned catalog (AGP 8.7.3, Kotlin 2.0.21, Compose BOM 2024.10.01,
      SDK 35/min 26), checksum-pinned wrapper; dark theme byte-for-byte
      from the mockup tokens; status-icon composable with all 10 kinds
      incl. cancelled, reduced-motion via areAnimatorsEnabled +
      ContentObserver; allowBackup=false + dataExtractionRules (future
      API-key storage); 30 JVM tests incl. literal cross-frontend
      wire-name contract; assembleDebug/test/lint green from cold build;
      Opus PASS + should-fix round)*
- [x] Connectivity-profile abstraction (one API-client interface, three
      implementations: tsnet / system VPN / public domain)
      *(two of three shipped — System-VPN and Public-domain; tsnet stays a
      documented seam, see the post-v1 item below)*
      *(WP 4b.2 done 2026-08-11: suspend OkHttp client for the full /v1
      surface the app needs (no /v1/steam/* — ADR-0004 device-local
      path), DTOs field-exact vs HEAD Pydantic incl. strict-Json fixture
      pass + verbatim api-test anchor; six-kind error taxonomy with
      literal cross-frontend pin; SystemVpn + PublicDomain profiles with
      defence-in-depth against redirect key leaks — followRedirects AND
      followSslRedirects false plus CleartextPolicyInterceptor at
      application AND network level, each layer standalone-pinned after
      the reviewer measured an https→http redirect forwarding X-Api-Key;
      EncryptedSharedPreferences key storage; polling/backoff pure
      functions in parity with the web store incl. the load-bearing
      jitter floor; 124 JVM tests; Opus FAIL→fix→PASS→should-fix round;
      tsnet stays a documented seam, post-v1)*
      *(WP 4b.3 done 2026-08-11: Steam OpenID login — checkid_setup via
      Custom Tab with steamvault:// return scheme, assertions
      re-verified via check_authentication against pinned
      steamcommunity.com with redirects refused and strict is_valid
      parsing, signed-fields gate before trust; SteamID64 validator with
      in-range Arabic-Indic mutation pin; on-device GetOwnedGames/
      GetPlayerSummaries with the user's own key (never sent to
      vault-api — allowlist-pinned), key-redacted error paths;
      documented replay residual (no state binding — candidate 4b.7/
      4b.9) and honest device-test list incl. 4b.7-blocked items; 219
      JVM tests; Opus PASS, 12/12 security mutations dead after fix
      round)*
- [ ] tsnet Go module + gomobile build (`.aar`), auth-key handling
      *(deliberately POST-V1: the profile abstraction from WP 4b.2 makes
      this additive, and the System-VPN profile covers Tailscale via the
      regular Tailscale app on day one)*
- [x] Grid + badges + multi-select + trigger + polling
      *(WP 4b.4 done 2026-08-11: library screen with grid 2/3/list +
      persisted layout, ANDed search+chips (recorded chip set),
      multi-select with bulk-download split and set-aware multiPlan bulk
      delete — all four web logic modules ported semantics-exact
      (reviewer: 12/12 mutations killed, zero cross-frontend drift);
      3-item bottom nav per frozen mockup; Coil covers on a separate
      OkHttp stack (API key cannot ride cover requests — by
      construction, dependency noted); vault ⊎ Steam-owned merge with
      honest synthesized rows (needs_force=false pinned); animation
      preservation via stable GameCardModel equality + lazy keys;
      "not connected" placeholder until 4b.7 lands the connection UI;
      314 JVM tests; Opus PASS + should-fix round (string-resource
      rule recorded in app/README))*
      *(WP 4b.5 done 2026-08-11: downloads screen — Active/Paused as
      independent sections with the web's verbatim slot-release wording,
      FIFO queue with positions, history newest-first with session-
      cached lazy log excerpts (truncation marker pinned to position 0),
      non-optimistic pause/resume/cancel with prefill-only pause gating,
      nav pip (queued|running|paused, foreground-only — recorded in the
      device-test list); unknown job statuses route to History instead
      of vanishing (recorded cross-frontend divergence, web backport in
      4a.8); JobCardModel stability with the strongest pin yet
      (stop_request drift changes ONLY the action field); 362 JVM
      tests; Opus PASS, 12/12 parity mutations killed)*
- [x] Delete flow with size display and confirmation; GC action per game
      *(WP 4b.6 done 2026-08-11: detail sheet with four-state depot
      sharing wording (ORPHANED added for the ADR-0003 last-remnant
      case — recorded divergence), honest last_manifest_check wording
      incl. the survives-deletion branch ("before the cache was
      cleared", pinned); delete confirm reuses buildMultiPlan verbatim
      (single-id ADR-0003 pins); GC flow as a pure reducer — dry-run →
      plan → confirm → execute, execute reachable ONLY via
      DryRunPlan→ConfirmExecute (parametrised no-op pins over all
      states), job-id-bound polling both branches, log-scrape verified
      against the real gc_execute.py emitter with after-header scoping;
      fix round also repaired a dead "Check again" button via a
      controller reset path; 407 JVM tests; Opus PASS + should-fix
      round)*
      *(WP 4b.7 done 2026-08-11: 3-step onboarding (profile choice,
      base-URL+key with a REAL two-step connection check — health for
      reachability, authed /v1/settings for key validity; optional
      Steam step closes the setWebApiKey UI gap); settings screen over
      GET/PATCH /v1/settings with the web's diff/presentation semantics
      (only-changed-keys pinned, honest applies wording, readonly
      banner with device-local Steam section correctly ungated);
      disconnect wipes store + reopens first-run with no stale polls;
      AND the recorded 4b.3 replay residual is CLOSED — per-login
      192-bit CSPRNG state in return_to, single-use consume before any
      network call, mutation-pinned in all directions; 492 JVM tests;
      Opus PASS 8/8 mutations + nit round)*
- [ ] Bypass warnings surfaced in the UI — PARTIAL: WP 4b.8 ships the
      bypass_suspected/resolved notifications (both directions), but the app
      has no clients/bypass detail surface yet (the web UI's clients sheet
      from WP 4a.7 has no Android twin), so tapping the notification routes
      to Settings. Closing this needs a small `GET /v1/clients` screen —
      carry-over, candidate for 4b.9's window
      *(WP 4b.8 done 2026-08-11: background notifications via
      WorkManager — 15-min constrained PeriodicWorkRequest with UPDATE
      policy, Doze respected by design (no exact alarms/foreground
      service); pure differ port of the web notifications semantics
      (first-poll silent, cancelled silent, update_ready gated on
      cached bytes, bypass both directions, the 4b.5 unknown-status
      improvement); notify-then-persist idempotency with stable
      per-event IDs + setOnlyAlertOnce; compact non-secret snapshot in
      plain prefs with shared decodeSnapshotOrNull fail-soft (pinned on
      the PRODUCTION path); foreground suppression gates posting only;
      catch-all worker with CancellationException rethrow; recorded
      routing gap bypass→Settings until a clients surface exists; 534
      JVM tests; Opus PASS 8/9 mutations + fix round)*
- [ ] Document APK build (no Play Store requirement; F-Droid as a long-term goal)

#### Phase 4c — Manual update check (both frontends, user decision 2026-08-10)

A user-triggered "check my cached games for updates now", so the vault can be
told from outside the LAN (from work, over Tailscale) to pull whatever is new
— the point is arriving home to a game that is already playable, not merely
knowing that an update exists.

**The check IS the fill.** SteamPrefill v3.7.1 has no `--dry-run` (verified
against `prefill --help`), and it does not need one: a non-forced run costs
~3 s and zero bytes for an up-to-date app, and downloads only the changed
chunks when stale (`docs/research/phase3-manifests.md`). So one action
answers the question and resolves it. Consequence for the UI: the affordance
must be worded honestly (`Check & update`, not `Check`) — pressing it can
consume real bandwidth. A check that only reports without filling is not
available at any reasonable cost, and would be the less useful half anyway.

- [x] Backend gap: `GET /v1/games` exposes `last_prefill_at` but NOT
      `apps.last_manifest_check`. Surface it in `GameSummary`/`GameDetail`
      — ADR-0006 tier 1 semantics are "current as of <timestamp>", which is
      only honest if that timestamp is visible. NOTE the shipped write rule
      (WP 3.3, verified in this WP): the column is stamped ONLY by a run
      that CONFIRMED the app already current (done + Updated==0 +
      UpToDate>0) — not on every run, not on done-with-updates. The UI must
      label it "confirmed current at X", never "checked at X", and the
      value survives a cache deletion (unlike `last_prefill_at`)
      *(mini-WP done 2026-08-10: field in both models, verbatim/null
      semantics with byte-for-byte round-trip pin, README honesty-table
      pointer; suite 1379 green; Opus PASS, 6/6 reviewer mutations killed)*
- [x] Enqueue endpoint question, decided (2026-08-17, WP 4c-api): `POST
      /v1/prefill` already takes a LIST of app ids and dedupes against
      `queued`/`running`/`paused` jobs, so an impatient double-tap converges
      on one job — but the open question was whether a server-side "select
      all cached apps" convenience route is worth it anyway. **Decision:
      ship it.** Robust for very large libraries (no frontend needs to
      enumerate `GET /v1/games` and filter by size first) and directly
      callable by external automation (n8n, a shell script over the
      tailnet) without already knowing the app id list. Cost accepted along
      with it: new write-capable API surface Phase 6's scoped keys must
      cover explicitly (recorded in `api/README.md`'s Auth section)
      *(WP 4c-api done: `POST /v1/prefill/cached`, no body — selects every
      app with cache content (disk-and-mapping truth, one bulk query, no
      per-app N+1) and enqueues through the SAME `jobs.enqueue_prefill`
      `POST /v1/prefill` uses, identical dedupe and response shape; "non-
      forced" is `apps.needs_force`'s existing per-app decision, not a new
      flag, and its lifecycle table in api/README.md was corrected in the
      fix round to also name GC execute (not only deletion) as a writer.
      Fix round (Opus FAIL→fix→PASS): added the primary-guarantee test the
      first pass shipped without — a multi-depot app plus an ADR-0003
      shared depot, asserting exactly one response entry per app — after
      the reviewer's mutation (per-depot instead of per-app selection) went
      undetected by the original 12 tests; that mutation is now killed by
      name (`test_multi_depot_apps_and_an_adr_0003_shared_depot_yield_
      exactly_one_entry_per_app`, verified by re-applying it and watching
      it fail, then reverting). Also added a route-level paused-dedupe
      test, fixed two dangling docstring test-file references, renamed a
      misleadingly-named test, and documented the cold-size-cache latency,
      the silently-ignored request body, and the mid-loop 5xx case. Suite
      1475 green (14 new tests). See api/README.md "Check & update all
      cached games" for the full, corrected contract. Frontend trigger UI
      and the pull-to-refresh divergence below are separate, unticked,
      packages that consume this route)*
- [ ] Trigger in both frontends: a library-header action over all cached
      games, plus the existing per-game and multi-select paths
- [ ] Mockup divergence to resolve in design: `doRefresh()`
      (`vault-app-mockup-NOTES.md`) only reloads what vault-api already
      knows. The update check asks Steam and must NOT be silently folded into
      pull-to-refresh — a gesture that can start downloads is a trap
- [x] Guardrails (backend half, WP 4c-api): a user-initiated check
      deliberately bypasses the WP 3.11 miss-trigger cooldown (the user
      pressed the button) — structural, not conditional: `POST
      /v1/prefill/cached` never imports `event_sweep`/consults
      `miss_trigger_state` at all, mutation-tested (adding a cooldown check
      fails the pinning test by name) — but stays bounded by worker slots
      (one worker, plan §3) and job dedupe (the same rule every prefill
      request follows). A 50-game library is ~2.5 min of serial Steam
      logins, so progress belongs in the Jobs view, never behind a spinner
      — the endpoint returns `202` immediately by construction. The
      frontend half (routing an actual button press to this endpoint) is
      the unticked trigger item above
- Remote use ("check from work") needs no extra work: 4a is served over
  Tailscale/LAN by design, 4b has the connectivity-profile abstraction
- Complements Phase 6's `app.updated` webhook: that is the passive/push
  half (get pinged when the nightly sweep finds something), this is the
  active/pull half (ask right now)

#### Phase 4d — Persisted settings + "keep the cache current" sweep mode

Two coupled items (user decision 2026-08-10). The sweep mode is the feature;
persisted settings are what makes it a switch in the UI rather than a
Compose edit plus restart.

**Settings persistence — Plan B (chosen over env-only).** Today EVERY
setting is env-only (`VAULT_NAME`, `VAULT_SCHEDULE_*`, `VAULT_WEBHOOK_*`)
and `GET /v1/schedule` is read-only; there is no settings write endpoint at
all. A settings screen that can toggle anything therefore needs a new layer.

- [x] Settings table whose values override the env defaults, plus
      `GET`/`PATCH /v1/settings`. Needs an ADR: precedence rules (env vs DB
      — which wins, and how an operator forces a value back), validation
      reusing the same strict grammars `config.py` applies at startup
      (a bad value must fail at PATCH time, not hours later in the
      scheduler thread), and which settings stay deliberately env-only
      *(settings-WP done 2026-08-10, ADR-0009: schema v13 settings table;
      db>env>default via one accessor; PATCH all-or-nothing transaction,
      null clears, startup grammars reused (webhook-URL scheme check is
      the documented API-only exception), env-only allowlist,
      VAULT_SETTINGS_READONLY 403 lock, webhook-URL userinfo redacted;
      scheduler thread now starts unconditionally so schedule keys are
      genuinely next_sweep (B1 pinned end-to-end through the real
      lifespan); webhook keys honestly restart-required; suite 1461
      green; Opus FAIL→fix→PASS, 13-mutation battery + A/B thread-cost
      measurement)*
- [ ] Phase 4a's settings screen builds on this rather than displaying
      read-only values with "set this env var" hints

**Sweep target set — installed PLUS cached (opt-in).** Today the nightly
sweep targets only the union of *installed* lists from fresh agent reports
(`scheduler.compute_targets`: "Intersected with nothing else", plan A8). A
game that sits in the cache but is currently installed nowhere — or whose
PC has been quiet longer than `VAULT_SCHEDULE_CLIENT_STALE_DAYS` — is never
refreshed and silently rots.

- [ ] New target-set mode adding every cached app to the sweep. Cheap by
      construction: ~3 s and zero bytes per app that is already current
      (see 4c), real traffic only for actual deltas
- [ ] Opt-in, and off by default — it spends bandwidth on games nobody has
      asked for, which must be the operator's explicit choice
- [ ] The backend half (an env var + the widened target set) can land
      independently of the settings layer; only the UI switch depends on it
- [ ] **Pair with auto-GC** (still open in Phase 3): every kept-current game
      adds fresh chunks while the old manifest's chunks become orphans. A
      vault that keeps itself current without collecting garbage keeps
      itself current straight into a full disk. These two ship together or
      the growth must at least be surfaced

### Phase 5 — Community Release
- [x] README with architecture diagram, quickstart (compose up in 5 minutes),
      and an explicit "works for guests" FAQ note: any Steam client behind
      the LAN DNS is served with zero setup — no account, agent, or app;
      store-on-miss means the first guest download fills the cache for
      everyone (ADR-0001)
      *(WP 5.2: root README with Mermaid diagram, quickstart verified
      command-by-command against deploy/, works-for-guests FAQ plus four
      more ADR-backed entries; feature status stated against shipped code,
      not ADR designs — license section says "planned Apache-2.0" until
      the LICENSE file lands with the release)*
- [x] License: Apache-2.0 (permissive for maximum adoption, includes patent
      grant; AGPL deliberately rejected as it deters contributors and
      companies in the early phase)
      *(WP 5.4: root `LICENSE`, canonical text, copyright line "Copyright
      2026 SteamVault contributors")*
- [ ] **Packaging gap — release blocker, recorded 2026-08-17.** The
      finished web UI is NOT in the shipped image: `api/Dockerfile` only
      `COPY`s `vault_api/`, and `web/` sits outside the `context: ../api`
      build context entirely, so `config._default_web_dir()` resolves to a
      non-existent path in the container and `webui.mount_web_ui` skips the
      mount by design (documented as a known gap since WP 4a.1). Fixing it
      means moving the build context to the repo root (plus a
      `.dockerignore`) or copying `web/` in via a second stage — a
      deliberate deploy decision, hence its own package. The same package
      should close the env-forwarding gaps in `deploy/compose.yaml`:
      `VAULT_EVENT_LOG_PATH` is never passed to vault-api, so WP 3.11's
      miss-trigger, per-client stats and bypass detection are unreachable
      in the shipped stack; `VAULT_MANIFEST_ORACLE` likewise. (Schedule and
      webhook keys are covered — they became DB-settable with ADR-0009 —
      but the event-log path is env-only and cannot be set from the UI.)
- [ ] CI: GitHub Actions — lint, tests, multi-arch image build (amd64/arm64),
      publish to ghcr.io with pinned version tags
      *(WP 5.1 done 2026-08-09 — the test/lint half: api pytest (Linux),
      agent go build/vet/test (Linux+Windows matrix), core `nginx -t`
      through the image's REAL entrypoint render path (pinned upstream
      image derived from core/Dockerfile) + config-drift check +
      shellcheck, PS 5.1 parser/pure-ASCII gate + PSScriptAnalyzer
      PSUseCompatibleSyntax(5.1); actions SHA-pinned, contents:read,
      NO publishing. Manual/network harnesses documented as CI-excluded.
      First gate exposed a real WP 3.10 bug — 25-vault-eventlog.sh's
      survivor check made vault-core unable to start in its default
      config; fixed in a separate fix(core) commit. Image build/publish
      remains WP 5.5, user-gated. Opus PASS + delta-confirmed. CI run #1
      on real runners: 4/5 jobs green incl. all three locally-unverifiable
      ones; the api/pytest Linux job found a second real bug — a
      Windows-only-measured path guard accepting `a\b`/`C:x` on POSIX —
      fixed as fix(api), run #2 green expected.)*
- [x] CONTRIBUTING.md, issue templates, example configs
      *(WP 5.4: root `CONTRIBUTING.md` with verified per-component test
      commands (api pytest: 704 passed/1 skipped; agent go build/vet/test:
      all packages green, incl. a documented CRLF/`gofmt -l` checkout
      caveat) and an honest local-only-vs-CI test matrix; GitHub issue-forms
      `.github/ISSUE_TEMPLATE/{bug_report,feature_request,config}.yml`
      (YAML-validated) + `.github/PULL_REQUEST_TEMPLATE.md`;
      `deploy/examples/{minimal-lan,tuned-setup}.md` plus an examples
      `README.md` index — `tuned-setup.md` documents a real gap (scheduler/
      GC env vars are read by `vault_api/config.py` but not yet forwarded
      by `deploy/compose.yaml`) with a working Compose-override recipe
      rather than a silently-broken `.env` example)*
- [ ] Announcement: r/selfhosted, r/homelab, LanCache Discord (stay fair:
      frame as a complement/alternative, not a "LanCache killer")

### Phase 6 — External Integrations (post-release, user decision 2026-08-10)

Deliberately NOT a release blocker: WP 3.13 already ships working generic
webhooks, and everything below is additive on top of them. Scheduled after
the community release so no integration work delays it.

Motivation: the WP 3.13 envelope is consumed happily by n8n and any generic
JSON receiver, but the Phase 3 checkbox claiming "Discord/Slack/ntfy-
compatible" overstates it — a Discord webhook accepts only `{"content": …}`
or `embeds` and rejects our envelope with 400. Today Discord needs a relay
in between. Phase 6 closes that gap and adds the events an automation
platform actually wants to react to.

- [ ] Multi-target delivery: `VAULT_WEBHOOK_TARGETS` — N receivers, each
      with its own URL, event filter, format and headers, so n8n and Discord
      can be fed at the same time (today: exactly one URL)
- [ ] Vendor format adapters `generic|discord|slack` (user decision
      2026-08-10: adapters in the backend, NOT a free-form body template —
      Discord must work without a relay; the cost is that we maintain two
      foreign formats). Includes honouring `Retry-After` on 429, which
      Discord does send and the current 3-attempt/0.2-0.5 s backoff ignores
- [ ] `app.updated` — the update notification. **No oracle dependency**
      (user decision 2026-08-10, superseding the earlier "either source"
      answer): the trigger is the existing non-forced scheduler sweep, which
      per `docs/research/phase3-manifests.md` costs ~3 s and zero bytes for
      an up-to-date app and is therefore already the manifest check. The
      changed manifest ids come from WP 3.2 ingestion, the byte/`Updated`
      counters from the WP 3.3 summary parser — so this is a notification
      hook on machinery that already runs, not new detection.
      The honest semantics are "the vault HAS this update" rather than
      "an update is available"; for a homelab that is the more actionable
      message. `VAULT_MANIFEST_ORACLE` (ADR-0006 tier 3) stays exactly what
      it is — an opt-in pre-emptive badge — and never becomes a
      precondition for notifications
- [ ] Outbound auth beyond Basic-in-URL: custom headers (n8n commonly wants
      a header token) + optional HMAC-SHA256 signature, plus a per-event
      `event_id` so a receiver can deduplicate across retries
- [ ] `POST /v1/webhooks/test` — fire a synthetic event at a target. Needed
      by any settings UI (Phase 4) and turns webhook setup from guesswork
      into one click
- [ ] Integration docs: n8n in BOTH directions (receiving events; calling
      `/v1/jobs` to trigger a prefill), Discord, ntfy
- [ ] Structured log export — opt-in `VAULT_EVENT_LOG_STDOUT` mirroring the
      cache-event TSV (WP 3.10) to vault-core's stdout, so Loki/Promtail/
      Vector-style collectors can ingest the machine-readable events without
      mounting the volume (user request 2026-08-17: keep it open for the
      community even though the requester uses Dozzle). Deliberately NOT a
      Dozzle gap: Dozzle reads container stdout, where vault-core's
      human-readable per-request log with HIT/MISS/BYPASS already is — a
      file-tailing log viewer is what the TSV would need, and no popular
      Docker log viewer does that. Cost side: doubles the line volume inside
      the json-file ring buffer, and the config-drift check's pinned
      `access_log` counts must be updated in the same change. Off by default
- [ ] Named, scoped API keys — the direct consequence of inviting external
      systems in (user question 2026-08-10). Today there is exactly ONE
      key: `auth.require_api_key` compares against `VAULT_API_KEY` for every
      router, so the key an n8n flow needs in order to enqueue a prefill is
      the same key that may `DELETE /v1/cache/{appid}`. Wanted: several
      named keys (web UI, agent, Android app, n8n) with a coarse scope —
      read / enqueue / destructive — each revocable on its own, so a leaked
      automation token does not mean rotating every agent in the house.
      Note this is about BLAST RADIUS, not about identity: it does not make
      the vault multi-user
- [ ] Payload scoping per target: a Discord channel is a room full of
      people, and `client.bypass_*` payloads carry `client_id` plus every
      IP address a device reported from, while job/update events reveal
      which games the household owns. A `full|minimal` payload mode per
      target (minimal drops device and address fields) is the cheap,
      correct fix — authentication cannot solve a receiver-side visibility
      problem
- Open, deliberately deferred: PER-USER webhooks ("tell ME when MY games
  update") require a real user identity, which only arrives with Phase 4a's
  Sign in with Steam (ADR-0004). Until then a vault has one operator's view
  and the webhook config belongs to that operator. Revisit after Phase 4a
- Explicitly rejected: persisting the delivery queue across restarts.
      At-most-once is the right hardness for homelab notifications
      (`webhooks.py` module docstring)

---

## 8. Repository Structure (Monorepo)

```
steamvault/
├── core/            # nginx config, Dockerfile
├── dns/             # optional dnsmasq container (Compose profile)
├── api/             # FastAPI, SQLite schema, scheduler
├── agent/           # PC listener (Windows)
├── app/             # Android (Kotlin + Go tsnet module)
├── deploy/          # compose.yaml, example .env, DNS mode docs
├── docs/            # architecture, ADRs, setup guides
└── .github/         # CI, templates
```

---

## 9. Risks & Open Questions

| Risk | Severity | Mitigation |
|---|---|---|
| `proxy_store` incompatible with range requests | HIGH | Phase 0 resolves this; Plan A fallback defined |
| Steam changes its CDN URL scheme | MEDIUM | Log schema anomalies with alerts; abstract the mapping layer |
| Public-domain profile exposes the API to the internet | MEDIUM | TLS mandatory, strong bearer token, docs strongly recommend forward-auth/OIDC + rate limiting in the reverse proxy; API designed with no unauthenticated endpoints |
| Shared depots → incomplete deletion | LOW | Shared detection + transparent reporting |
| tsnet gomobile build complexity | MEDIUM | Profile abstraction means tsnet can ship later; system-VPN profile works day one |
| Single-maintainer risk | MEDIUM | Small scope (Steam only), good docs, permissive license lower the contribution barrier |
| SteamPrefill upstream dependency | LOW | Used only as a CLI subprocess, replaceable |

**Deliberately OUT of scope (v1):** multi-service (Epic etc.), multi-tenant
setups. *(The web UI moved INTO scope as Phase 4a by user decision on
2026-08-06 — it shares the app's design language and API surface and is
served by vault-api itself.)*

**Post-v1 roadmap:** an iOS app. The groundwork is deliberately kept
compatible: the app talks pure REST to vault-api, the connectivity-profile
abstraction is UI-framework-agnostic, and tsnet builds for iOS via the same
gomobile toolchain as the Android `.aar`.

---

## 10. Deployment Notes (generic)

- vault-core needs to answer on **port 80** (Steam CDN traffic is plain HTTP).
  If another service occupies port 80 on the host, use a dedicated IP
  (IP alias, macvlan, or a dedicated VLAN interface).
- **DNS redirection — three modes, pick one:**
  1. **Existing local DNS** (recommended for homelabs): rewrite
     `*.steamcontent.com` → cache server IP in AdGuard Home, Pi-hole,
     dnsmasq or Unbound. Block/rewrite AAAA records too — IPv6 fallback
     silently bypasses the cache.
  2. **Bundled vault-dns** (no local DNS server required): enable the
     optional dnsmasq container and point your router's DHCP DNS at it.
     AAAA handling is covered by vault-dns's config (`address=` +
     `local=` pairing — see the vault-dns component note in §3).
  3. **DNS-free hosts mode** (single Windows gaming PC, simplest setup):
     a `lancache.steamcontent.com` hosts entry — manually or automated by
     vault-agent (opt-in). Windows Steam client only.
- **Remote access (vault-api only — never expose vault-core/port 80):**
  - Tailscale: reusable auth key for the app's embedded tsnet node, or the
    regular Tailscale client app
  - Twingate: define vault-api as a Twingate resource; the app uses the
    system-VPN profile
  - Public domain: front vault-api with your reverse proxy (Traefik, Caddy,
    NPM, Cloudflare Tunnel); TLS required, forward-auth/OIDC strongly
    recommended on top of the API key
- `/v1/health` is designed to be polled by any external monitoring system.

---

## 11. Next Steps

The original three steps here (build the Phase-0 PoC, create the public
repository, then start Phase 1) are all DONE — Phase 0 answered the
`proxy_store` question in favour of Plan B (ADR-0001), the repo exists, and
Phases 1–3 plus 4a shipped. Rewritten 2026-08-17 to reflect the real state.

1. [ ] **Packaging package** (§7 Phase 5, first bullet): `web/` into the
   vault-api image, `VAULT_EVENT_LOG_PATH` + `VAULT_MANIFEST_ORACLE` through
   Compose, `deploy/tests/verify-stack.sh` extended. Small, and nothing else
   makes Phase 4a's work reachable in a real deployment.
2. [ ] **Zeus rollout** — joint interactive session (see the Deployment
   section of `docs/WORKPACKAGES.md`). Two user-side blockers first: the
   stale HADES Tailscale subnet route, and the Fritz!Box announcing itself
   as an IPv6 DNS server via RA (a live cache bypass). This session is also
   where the honest still-open lists in `web/tests/README.md` and
   `app/README.md` get verified — real screen reader, phone browser cover
   art, GC against real on-disk chunks, real multi-client bypass detection.
3. [ ] **WP 4b.9** Android release build + signing docs + APK distribution
   (keystore creation is a user action), plus the review carry-over list in
   `docs/WORKPACKAGES.md`.
4. [ ] **Phase 4c / 4d** frontend halves: the manual "check & update" trigger
   in both UIs, and the opt-in "keep the cache current" sweep mode (its
   auto-GC prerequisite is shipped — see §7 Phase 3).
5. [ ] **WP 5.3** SECURITY.md + threat model + pre-release security review
   (Fable mandatory) — after an api/core code freeze.
6. [ ] **User-gated:** WP 5.5 (GitHub org + `ghcr.io/steamvault/*` publish)
   and WP 5.6 (announcement, only after the user's own end-to-end test with
   the Android app). Phase 6 integrations are deliberately post-release.
