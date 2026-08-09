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
- nginx official image envsubst: always set `NGINX_ENVSUBST_FILTER` — but know
  why. It is a no-op today (the entrypoint's allowlist is built from env vars
  that EXIST, none named like an nginx variable, so filtered and unfiltered
  renders are byte-identical). It guards a future lowercase env var named
  `host`/`uri`, which envsubst would substitute INTO the config as its value —
  not blank it — leaving `nginx -t` green and the cache quietly wrong (WP 1.9).
- Temp paths must live OUTSIDE the document root but on the SAME filesystem
  as the cache (atomic rename) (WP 1.1/1.9).
- `access_log` inheritance is REPLACE, not merge: declaring a second log in
  a location silently kills the inherited one — restate every log per
  location (WP 3.10).
- `$upstream_cache_status` is literally `-` under proxy_store on both HIT
  and MISS (measured) — cache-status truth must come from structural
  location markers (`set` in the serving location), never from
  proxy_cache-only or `$upstream_status` variables (WP 3.10).
- PCRE `.` stops at newlines — regexes bounding DECODED URIs need `(?s)`
  or an embedded %0A silently truncates the match (WP 3.10).
- A guard grepping a log_format NAME also matches the `log_format`
  declaration and every `map` named after it — anchor config guards to the
  DIRECTIVE (`^\s*access_log\s.*name`). WP 3.10 shipped a fail-closed hook
  that failed 100% of the time in the default (event-log-off) deployment;
  container hooks without a container-level test stayed unexecuted until
  WP 5.1's first CI gate exposed it (WP 5.1 blocker).

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
- `os.path.lexists` swallows EVERY `OSError` into False ("gone") — a
  protective branch keyed on it collapses "unreadable" into "deletable".
  Decide on the error TYPE: only `FileNotFoundError` means gone
  (WP 3.8b).
- Attribute Windows errors per SYSCALL: a delete-pending name answers
  `lstat` with `FileNotFoundError [WinError 2]` but `unlink` with
  `PermissionError [WinError 5]` — WP 1.6's finding is about unlink only;
  measured with a FILE_SHARE_DELETE handle rig (WP 3.8b).
- A path-shape guard measured only on the dev OS is not a guard:
  `ntpath.basename` splits on `\` (and strips `X:` drives), `posixpath`
  does neither — the Windows measurement hid a POSIX hole (`a\b`, `C:x`
  accepted as direct children) for two work packages until CI ran pytest
  on the production OS for the first time. Reject separator-ish
  characters (`\`, `:`) by LITERAL check, never via the host's `os.path`;
  pin each rejection arm with its own mutation-tested case (WP 3.8 →
  caught by WP 5.1 CI run #1).

## PowerShell 5.1 (dev/test harnesses)
- `2>&1` on native commands wraps stderr lines in NativeCommandError and
  kills the script under `$ErrorActionPreference=Stop` (WP 1.8/0.6) — and
  so does `2>$null` (measured, WP 3.10): ANY stderr redirection triggers
  it; the only fix around stderr-writing natives like `nginx -t` is a
  locally restored `$ErrorActionPreference = "Continue"`.
- `return $arrayVar` re-flattens a single-element array even when the
  helper wrapped it — the CALL SITE needs `@(...)` too (WP 3.10).
- `"\n"` in double quotes is a literal backslash-n; locale decimal commas
  corrupt reports — force InvariantCulture (WP 0.3).
- `$null` numeric comparisons pass silently (`$x -ge $null` is true) — gate
  assertions on preconditions or they become false positives (WP 1.7).
- `$ErrorActionPreference = "Stop"` makes every later `Write-Error`
  terminating: `exit 2` lines after it are unreachable and the script
  reports exit 1. Set Stop only after input validation (WP 2.6, measured
  on all four usage paths).
- `Set-Acl` on an already-protected ACL fails with SeSecurityPrivilege for
  non-admin accounts on the SECOND call — use `icacls /inheritance:r
  /grant:r` (idempotent, measured 3x); Task Scheduler XML rejects
  `[TimeSpan]::MaxValue` repetition durations; em dashes in BOM-less UTF-8
  break the PS 5.1 parser under the system codepage — packaging scripts
  are pure ASCII (WP 2.6).

## CI / GitHub Actions
- The stock nginx image entrypoint soft-fails: `20-envsubst-on-templates.sh`
  returns 0 WITHOUT rendering when the template dir is missing or the output
  dir is unwritable, and the image ships its own nginx.conf — a gate driving
  the real entrypoint must assert the rendered config is actually ours
  (grep a repo-owned token like the log_format name) or it green-lights the
  stock config (WP 5.1 reviewer catch).
- Values hand-copied from a Dockerfile into CI (image ref, ENV wiring) are
  drift surfaces — derive them (`sed -n 's/^FROM[[:space:]]\{1,\}//p'`) or
  grep-assert they still match before use, and require `@sha256:` (WP 5.1).
- PSScriptAnalyzer's PSUseCompatibleSyntax checks the AST against stored
  version profiles — it does not need to run UNDER 5.1; only the raw
  `Parser::ParseFile` check does. Split: parser under `shell: powershell`,
  analyzer under `pwsh` (5.1 hosts may not see the runner's preinstalled
  module path) (WP 5.1).

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

## Parsers / input handling
- Recursive-descent parsers need an explicit depth limit raising the
  module's own error type — RecursionError escapes the documented catch
  contract and crashes the caller (WP 2.1 blocker).
- Python `int()` accepts " 4 ", "+4", "1_0" (=10) and non-ASCII digits —
  any value that later feeds Go, SQL, or a filesystem path needs
  strip + isascii + isdigit validation (WP 1.6/2.1).
- Pydantic lax mode coerces `true`→1 on int fields — reject bools
  explicitly on any id field (WP 2.4).
- `re`'s `\d` matches Unicode digits unless `re.ASCII` is passed — strict
  numeric env/config grammars are safer as `isascii()+isdigit()` on the
  partitioned string; and a long digit string is a VALID decimal literal
  that `float()` rounds to `inf`, so check `math.isfinite` after
  conversion (WP 3.12).
- One-shot request flags (stop/cancel columns) must be cleared at EVERY
  terminal or parking transition (finish, park, resume) — a stale flag
  re-fires on the next run of the same row (WP 3.12).
- Cursor-based tailers: "no newline in the batch" conflates a partial
  tail (wait) with an oversized line (skip past the next newline, loudly)
  — a full-sized batch can never become a valid line, and treating it as
  a tail stalls the sweep silently forever. Never advance a cursor past
  unterminated bytes (half-written lines would parse as fresh records),
  and never let a progress log line fire when nothing was consumed
  (WP 3.11).
- `urllib.request` does not handle userinfo in URLs (`https://u:p@host/`
  fails getaddrinfo) — strip it and send a real Basic Authorization
  header; redact userinfo in every log line, and test the redaction
  against `@` inside passwords and IPv6 hosts (WP 3.13).
- Transition detectors: persist state changes in BOTH directions
  regardless of which notifications are enabled — filtering belongs at
  delivery, or enabling an event later fires falsely on first sight
  (WP 3.13).
- envsubst on config templates: a colliding env var REPLACES runtime
  variables with its value (not blanks) and `nginx -t` still passes —
  always restrict with an allowlist filter (WP 1.9).
- A consumer that renames a producer's files breaks filename-contract
  parsers: manifest_archive stores `{depotid}_{manifestid}.bin` while the
  parser demanded SteamPrefill's 4-field name — keep payload parsing and
  filename-contract validation separable, with the caller supplying the
  expected ids so the corruption cross-check survives (WP 3.7).

- `ntpath.join` has two surprising drive cases: `join(parent, "C:x")`
  silently drops the drive (yields `parent\x`) and `join(parent, "a:b")`
  discards the parent entirely — strict-child path guards must compare
  `dirname`/`basename` equality on the joined result, never prefixes
  (WP 3.8, measured).
- CPython's `json` scanner recurses per nesting level — a bounded response
  body is NOT a bounded parse; catch `RecursionError` by name around
  `json.loads` on untrusted input and convert it to the module's own error
  type (WP 3.9, same failure class as the WP 2.1 blocker).
- `urllib` follows redirects by default — an operator-configured outbound
  URL needs an explicit no-redirect opener, or the host actually contacted
  is not the one configured (WP 3.9).

## Containers
- A non-root container user needs a real, writable HOME — tools that
  write caches (SteamPrefill: $HOME/.cache) crash on /nonexistent before
  printing anything, so error-pattern matching never fires. Set both the
  passwd home-dir AND ENV HOME; compose run vs docker exec derive HOME
  differently (WP 1.9 Fable blocker).
- "Verified by inspection" is not verified: every binary that ships in an
  image needs at least a credential-free smoke RUN in the verify suite
  (WP 1.9).
- A `.env` beside `compose.yaml` only feeds `${...}` interpolation inside
  that file — it is NOT a pass-through into container environments. Any
  setting config.py reads is dead unless the service's `environment:`
  block forwards it by name; WP 5.4 found four such dead vars
  (VAULT_SCHEDULE_WINDOW, VAULT_SCHEDULE_INTERVAL_MINUTES,
  VAULT_SCHEDULE_CLIENT_STALE_DAYS, VAULT_GC_GRACE_DAYS) and documented a
  compose.override.yaml recipe instead of a silently-broken .env example.

## Subprocess output handling
- SteamPrefill writes its summary table in the OS OEM codepage (cp850
  here), not UTF-8 — decode strict-UTF-8 first, OS-queried OEM second,
  lossy LAST (a decode path that can raise turns a successful download
  into a crashed job: terminate() leaves truncated multibyte tails as
  the NORMAL case) (WP 3.3 blocker).
- "Contains a digit" is a terrible row-detector: SGR remnants
  (\x1b[38;5;226m) and timestamps are digits too — require
  digits-and-separators-only (WP 3.3).

## systemd / packaging
- Persistent=true only works with OnCalendar= timers — on monotonic
  triggers (OnBootSec/OnUnitActiveSec) it is a silent no-op, so
  "catch up after suspend" claims need OnCalendar (WP 2.5).
- network-online.target does not exist in the systemd USER scope —
  Wants=/After= on it are no-ops there (WP 2.5).
- Secret env files: umask 077 BEFORE creating, not chmod 600 after
  (world-readable window) (WP 2.5).

## Go / CLI
- Never register a secret env value as a flag DEFAULT — Go's
  flag.PrintDefaults() prints non-empty defaults verbatim on -h and every
  parse error, leaking the secret to stderr on the recommended
  operational path. Register empty, apply env fallback after parsing
  (WP 2.2 blocker).
- Python str.isprintable() rejects Cf/Zs/Co/Cn — Go parity needs
  !unicode.IsPrint, not IsControl+Zl/Zp (WP 2.2).
- time.Sleep in retry loops is not ctx-cancellable — select on
  ctx.Done() vs time.After (WP 2.2). CGO_ENABLED=0 explicitly or
  "static binary" claims are false on the native target (WP 2.2).
- With core.autocrlf=true and no `*.go eol=lf` in .gitattributes,
  `gofmt -l .` lists EVERY Go file — a checkout property, not a style
  violation. Judge gofmt only from an LF checkout (measured, WP 5.4).
- On this dev host the Go toolchain lives only in WSL (no go.exe) —
  Go verification claims must state where they ran, and reviewers should
  check WSL before treating a Go claim as unverifiable (WP 5.4).

- Removal reviews audit CONTENT, not just references: a deleted reference
  implementation can be the only home of documentation living code still
  cites (the EAppState bit table existed nowhere else) — port embedded
  knowledge before deleting its host (WP 2.6 blocker).

## Testing discipline
- Fail-closed defaults need tests that pin the DEFAULT direction: flip each
  "unknown ⇒ protected" branch and watch a test die — two such flips passed
  the entire 434-test suite unnoticed in review (WP 3.6 Opus).
- A protection rule keyed on data that deletion deliberately preserves
  (mapping rows, ADR-0003) leaks resources forever unless it has an explicit
  last-remnant escape — audit every "skip if referenced" guard for the
  all-referrers-gone end state (WP 3.6).
- Flake-hunt concurrency tests: run the module isolated in a 20-40x loop —
  full-suite green means nothing for timing bugs (WP 1.6 Fable).
- Enumerate the package's GUARANTEES and mutation-test each one by name —
  six killed mutations mean nothing if the seventh (the primary
  fail-closed promise) was never targeted (WP 2.3).
- Mutation-test regression tests (revert the fix, watch the test fail)
  before trusting them (multiple WPs).
- Fixtures: synthetic only, modeled on real structure — never personal data
  (WP 2.1).
- Verify empirically over believing docs or reviewers — several review
  claims were corrected by rigs (WP 1.1 301/302, WP 1.6 rmtree).

## Docs / community release
- Entry-point docs describe SHIPPED behavior, not ADR designs: an ADR
  records a decision that may be unimplemented — grep the code for the
  mechanism (and check PLAN checkboxes) before claiming it works
  (WP 5.2 blocker: staleness FAQ described the unshipped manifest oracle
  as the live mechanism).
- Quote container-real paths in docs: the cache lives at
  `/vault/cache/depot/...` (nginx `-p /vault`, `VAULT_CACHE_ROOT`), not
  `/cache/...` (WP 5.2 blocker).
- Never present a planned license as granted — a `## License: Apache-2.0`
  heading without a LICENSE file reads as an effective grant that does not
  exist; word it "planned" until the file lands (WP 5.2 blocker).
