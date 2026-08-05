# Engineering learnings (living document)

Standing, project-proven findings. **Every coder and reviewer reads this
before their first task of a session.** The orchestrator appends distilled
findings after each review cycle — one line each, with the WP that proved it.
These are not style preferences; each entry cost a review round to learn.

## nginx / vault-core
- `$upstream_status` becomes a comma list ("502, 200") when
  `proxy_next_upstream` retries — any map/guard keyed on it must handle the
  list form (WP 1.1 blocker).
- Unguarded `proxy_store` stores ONLY status-200 bodies on this nginx line —
  404/206/301/302 are not stored; don't claim otherwise, and keep explicit
  guards anyway for retry-list handling (WP 1.1, reviewer claim corrected by
  empirical rig).
- `proxy_pass` with a variable resolves at request time via the configured
  `resolver`; without a variable it resolves once at startup via the OS
  (hosts-file-poisonable) path (WP 0.1).
- Strip `Range`, `Accept-Encoding`, `If-Range` upstream on the store path —
  gzip bodies get stored raw and served corrupt otherwise (WP 1.1).
- nginx official image envsubst: set `NGINX_ENVSUBST_FILTER` or it blanks
  every `$uri`/`$host` in templates (WP 1.9).
- Temp paths must live OUTSIDE the document root but on the SAME filesystem
  as the cache (atomic rename) (WP 1.1/1.9).

## SQLite / FastAPI / vault-api
- Connections must be created, used, and closed in ONE thread. A generator
  dependency holding a connection segfaults (native access violation) when
  request cancellation unwinds AsyncExitStack on another thread —
  `check_same_thread=False` converts the loud error into a use-after-free
  (WP 1.4 fixing WP 1.3's review advice).
- Every check-then-act needs `BEGIN IMMEDIATE` (job claim/dedupe, report
  chains) (WP 1.4/2.4).
- Order report/diff chains by rowid, not second-precision timestamps
  (WP 2.4). WAL + busy_timeout on every connection (WP 1.2).
- FastAPI ships /docs, /redoc, /openapi.json world-readable — disable via
  `openapi_url=None`; auth belongs on the APIRouter, not per route; compare
  API keys with `hmac.compare_digest` on BYTES (non-ASCII str raises)
  (WP 1.2/1.3).

## Windows filesystem semantics
- `shutil.rmtree` deletes part of the tree BEFORE raising — "every attempt
  failed" never implies "nothing changed" (WP 1.6 blocker).
- Deleted-while-handle-open files report PermissionError (delete pending),
  not FileNotFoundError; racing removals need settle-and-recheck with
  `lexists` as the last word (WP 1.6 Fable blocker).
- Junction detection: `os.path.islink()` is False for junctions,
  `DirEntry.is_dir(follow_symlinks=False)` is True — use an
  islink-OR-isjunction helper; rmtree refuses links (target survives) but
  removal code must unlink links itself, never traverse (WP 1.6).

## PowerShell 5.1 (dev/test harnesses)
- `2>&1` on native commands wraps stderr lines in NativeCommandError and
  kills the script under `$ErrorActionPreference=Stop` (WP 1.8/0.6).
- `"\n"` in double quotes is a literal backslash-n; locale decimal commas
  corrupt reports — force InvariantCulture (WP 0.3).
- `$null` numeric comparisons pass silently (`$x -ge $null` is true) — gate
  assertions on preconditions or they become false positives (WP 1.7).

## dnsmasq / vault-dns
- `address=/zone/ip` alone FORWARDS AAAA upstream on modern dnsmasq —
  pair with `local=/zone/` or IPv6 silently bypasses the cache (WP 0.6).
- Pi-hole v6 ignores /etc/dnsmasq.d by default — instructions must target
  `misc.dnsmasq_lines` (WP 1.8).

## Steam ecosystem facts
- SteamPrefill v3.7.1 has no app-id CLI — selection via
  Config/selectedAppsToPrefill.json; exits 0 with "Prefilled 0 apps" for
  unowned apps (job-outcome trap, Phase 3 item); requires the
  /lancache-heartbeat + X-LanCache-Processed-By contract; sends
  ?nocache=1 + Range bytes=0-0 speed probes (WP 0.4/1.4/1.7).
- Real Steam clients (Windows AND current Linux) send ZERO Range headers
  and use lancache discovery; manifest URLs carry per-request codes (no URL
  dedupe); Steam LAN P2P transfers can legitimately replace cache traffic
  (WP 0.3/0.6).

## Testing discipline
- Flake-hunt concurrency tests: run the module isolated in a 20-40x loop —
  full-suite green means nothing for timing bugs (WP 1.6 Fable).
- Mutation-test regression tests (revert the fix, watch the test fail)
  before trusting them (multiple WPs).
- Fixtures: synthetic only, modeled on real structure — never personal data
  (WP 2.1).
- Verify empirically over believing docs or reviewers — several review
  claims were corrected by rigs (WP 1.1 301/302, WP 1.6 rmtree).
