# SteamVault — Project Plan

A Steam game cache with true per-game management — self-hosted, Docker-first.

Status: PLANNING · License: Apache-2.0

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
| GET | /v1/clients | Per-client hit stats incl. bypass warnings |
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

### Phase 3 — Scheduler & Update Logic

Closing plan (2026-08-09, user decision "implement everything"): remaining
packages are 3.8b grace window (merged) · 3.9 oracle incl. open-beta
branch manifests (ADR-0007 addendum B) · 3.10 vault-core cache-event log
(ADR-0008) · 3.11 event sweep = miss-trigger + client hit stats/bypass ·
3.12 job control + optional auto-GC · 3.13 webhooks. api/-packages run
strictly serially; 3.10 (core/) runs in parallel.

Branch-dispatch structure for ALL remaining phases (package briefs,
parallel/serial decisions, merge discipline, open user decisions):
`docs/WORKPACKAGES.md` (2026-08-09).

- [ ] Miss-triggered prefill completion: a cache miss on an unknown/partial
      app queues a prefill job for that app (hybrid decision, ADR-0001;
      feed design decided in ADR-0008 → WP 3.10 core log + WP 3.11 sweep)
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
- [ ] Manifest-based garbage collection (`/v1/cache/{appid}/gc`, optional
      auto-GC after successful update prefill)
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
- [ ] Beta-branch protection for GC (user decision 2026-08-09, ADR-0007
      addendum): (A) recently-stored grace window via st_ctime as a
      ChunkExclusion predicate; (B) open-beta branch manifests join the
      keep set when the oracle (WP 3.9) is enabled
- [ ] Per-client hit statistics + bypass detection (`/v1/clients`)
- [ ] Optional generic webhook notifications (Discord/Slack/ntfy-compatible;
      built as a generic webhook feature, not vendor-specific)

### Phase 4 — Frontends (user decision 2026-08-06: web first, then app)

#### Phase 4a — Web UI (served by vault-api as static files)
- [ ] SPA sharing the mockup's design language; zero extra deploy
      complexity (vault-api serves it; works over Tailscale/LAN and on
      phone browsers immediately)
- [ ] Same API surface as the app: library grid with badges + search,
      download-to-cache/delete flows, jobs view, settings incl. vault name
- [ ] Steam identity via Sign in with Steam (ADR-0004); library fetched
      browser-side, never proxied through vault-api

#### Phase 4b — Android App
- [ ] Kotlin/Compose project, Steam Web API integration (library + covers)
- [ ] Connectivity-profile abstraction (one API-client interface, three
      implementations: tsnet / system VPN / public domain)
- [ ] tsnet Go module + gomobile build (`.aar`), auth-key handling
- [ ] Grid + badges + multi-select + trigger + polling
- [ ] Delete flow with size display and confirmation; GC action per game
- [ ] Bypass warnings surfaced in the UI
- [ ] Document APK build (no Play Store requirement; F-Droid as a long-term goal)

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
      remains WP 5.5, user-gated. Opus PASS + delta-confirmed.)*
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

1. [ ] **Build the Phase 0 PoC**: test nginx + DNS rewrite + one real Steam
   download. The result decides Plan A vs. Plan B.
2. [ ] Create the public repository.
3. [ ] Only then: start implementation of Phase 1.
