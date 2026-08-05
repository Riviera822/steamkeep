# vault-agent

The PC listener for SteamVault. Deliberately dumb by design (see
`docs/PROJECT_PLAN.md` section 3): read the local Steam library, report
what's installed, no control logic on the device. All prefill/scheduling
decisions live in vault-api.

Status: work in progress. The ACF/VDF parser has been ported to Go
(WP 2.1b, `go/`) and is judged against this Python package's test corpus —
see "Go port (production)" below. The HTTP reporter and the `vault-agent`
CLI now exist (WP 2.2, `go/report`, `go/client`, `go/agentconfig`,
`go/cmd/vault-agent`) — see "vault-agent CLI (WP 2.2)" below. No
hosts-file mode yet (WP 2.3), no Linux/SteamOS discovery variant (WP 2.5,
see ADR-0002; the binary already cross-compiles for linux/amd64 and
linux/arm64 and runs, but library-discovery paths beyond the OS-generic
default are WP 2.5's job).

**Executable specification (ADR-0005):** vault-agent ships as a Go binary
in production; this Python package and its test suite are the pinned
reference the Go port (`go/acf`, WP 2.1b) was built and tested against.
Every parsing decision below — especially the ones that are conventions
rather than the one obviously correct answer (escape handling, nesting
limits, conditional tags) — is deliberately spelled out and tested so the
Go port matches it (with a small number of named exceptions — see "Known
divergences from the Python spec" below) rather than re-deriving it. Per
ADR-0005, this Python package is retained as the frozen spec baseline —
it is no longer the code that ships, but it stays in the repo, unremoved,
through the rest of Phase 2; removal happens at the **Phase-2 close-out
package**, not automatically the moment WP 2.2 (reporter) lands.
`tests/fixtures/` is permanent either way: it's the shared fixture corpus
both this Python suite and the Go suite (`go/acf/*_test.go`) reference,
and stays regardless of which implementation is currently shipping.

## Go port (production)

`go/acf` is the Go package vault-agent actually ships (ADR-0005): a
straight port of this Python package's parsing behavior, tested against
the SAME fixture corpus under `tests/fixtures/` (referenced relatively,
never copied).

**Layout:**

```
agent/go/
├── go.mod                    # module github.com/Riviera822/steamvault/agent
├── acf/
│   ├── acf.go                 # KeyValues (ordered map), ParseError, getCI
│   ├── tokenizer.go            # rune-based tokenizer
│   ├── parser.go                # recursive-descent parser, depth cap, conditionals, BOM
│   ├── strictuint.go             # parseStrictUint (ASCII-digit-only grammar)
│   ├── appmanifest.go              # InstalledApp, ParseAppManifest(File)
│   ├── libraryfolders.go            # ParseLibraryFolders(File), modern + old flat
│   ├── discover.go                   # DiscoverInstalled, Warning
│   ├── testhelpers_test.go            # fixture path resolution, KeyValues-vs-map equality helper
│   ├── acf_test.go                     # tokenizer/parser tests (ported from test_tokenizer.py)
│   ├── appmanifest_test.go              # ported from test_appmanifest.py
│   ├── libraryfolders_test.go            # ported from test_libraryfolders.py
│   └── discover_test.go                   # ported from test_discover.py
├── report/
│   ├── report.go               # BuildReport, ValidateClientID (mirrors the server's rules)
│   └── report_test.go
├── client/
│   ├── client.go                # HTTP POST /v1/agent/installed, retry + backoff + jitter
│   └── client_test.go            # httptest.Server: success/401/500-then-ok/timeout/
│                                  # malformed-JSON/connection-refused/retry-cap cases
├── agentconfig/
│   ├── config.go                 # flags+env parsing, defaults, validation (WP 2.2)
│   └── config_test.go
├── cmd/
│   ├── probe/main.go          # throwaway CLI: DiscoverInstalled against a real
│   │                           # library_root, for manual real-machine validation —
│   │                           # NOT the production reporter
│   └── vault-agent/main.go     # THE production CLI (WP 2.2): `report` [--loop]
│       └── main_test.go         # exit codes, one-shot success/failure, API-key redaction
```

**Building and testing (WSL2 — no Windows Go toolchain in this repo's dev
environment; Go 1.26 in WSL):**

```bash
wsl bash -c "cd /mnt/c/claude-dev/SteamVault/agent/go && go build ./... && go vet ./... && gofmt -l . && go test ./..."
```

**Cross-compile matrix** (static binaries, no CGO, matching the three
ADR-0005 targets plus a Linux/arm64 SteamOS check). `CGO_ENABLED=0` is
REQUIRED on every line, not a cosmetic default: building `linux/amd64`
*from* the linux/amd64 WSL host without it silently produces a
dynamically-linked binary (Go enables cgo automatically whenever
GOOS/GOARCH match the build host and a C toolchain is present) - `file`
reported it as "dynamically linked" against `libc.so.6`, directly
contradicting ADR-0005's "no runtime dependencies" static-binary premise
(WP 2.2 review finding S3). The other two targets (`windows/amd64`,
`linux/arm64`) don't have a host C toolchain configured for them so they
came out static either way, but `CGO_ENABLED=0` is set explicitly on all
three so this doesn't depend on which machine happens to run the build:

```bash
CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -o /tmp/vault-agent-windows-amd64.exe ./cmd/vault-agent
CGO_ENABLED=0 GOOS=linux   GOARCH=amd64 go build -o /tmp/vault-agent-linux-amd64      ./cmd/vault-agent
CGO_ENABLED=0 GOOS=linux   GOARCH=arm64 go build -o /tmp/vault-agent-linux-arm64      ./cmd/vault-agent
```

All three build clean for both `cmd/probe` and `cmd/vault-agent` (verified
during WP 2.1b and WP 2.2). **Verified static, not just assumed** (WP
2.2): `file` reports the `linux/amd64` output as "statically linked" and
`ldd` refuses it ("not a dynamic executable") only with `CGO_ENABLED=0`
set - the same build without it links against `libc.so.6` per the
paragraph above. Build output is never committed —
`agent/go/*.exe`, `agent/go/probe`, `agent/go/vault-agent(.exe)` are
gitignored.

**Live integration check (WP 2.2):** the real vault-api (loopback,
throwaway SQLite DB) was started and the built Windows `vault-agent.exe`
was run against the real `C:\steam` library (read-only): first run —
`200`, `first_report=true`, `received=15` (matching the same 15 apps
`cmd/probe`/WP 2.1b found); second run — `added=[]`, `removed=[]`,
`first_report=false`; a run with a deliberately wrong `--api-key` returned
`401` in under 100ms (no retry storm — 4xx is never retried) with exit
code 1. Server-side logs confirmed the matching
`vault_api.agent_reports` audit lines. No real app names/sizes are
reproduced here (fixture/privacy policy, same as WP 2.1b).

**Key porting decisions:**

- **Ordered `KeyValues`, not a plain `map[string]any`.** Go map iteration
  order is randomized; Python dicts are insertion-ordered, and
  `ParseLibraryFolders`' returned path list is order-sensitive (the
  modern-format fixture asserts library 0 before library 1). `KeyValues`
  is a small struct (ordered key slice + lookup map) instead — a bare Go
  map here would make that test flaky rather than deterministically
  wrong, which is worse.
- **Runes, not bytes, in the tokenizer.** Python strings are indexed by
  code point; a byte-indexed Go tokenizer would split multi-byte UTF-8
  content (e.g. a non-ASCII game name in a quoted value) mid-character.
  `tokenize` converts to `[]rune` up front so offsets and slicing behave
  the same way.
- **`os.ReadDir` + manual `appmanifest_*.acf` prefix/suffix matching in
  `DiscoverInstalled`, not `filepath.Glob`.** Found during real-machine
  validation: Go's `filepath.Glob` runs `Match`-style pattern parsing
  over EVERY segment of the full joined path — on non-Windows GOOS it
  treats `\` as an escape metacharacter (a library path containing a
  literal backslash, e.g. a Windows-native `libraryfolders.vdf` path fed
  through a non-Windows-built `acf` binary while cross-testing under
  WSL, makes `Glob` silently match nothing), and `[`/`]` are ALWAYS
  character-class syntax on every platform (a library folder legitimately
  named e.g. `lib [beta] one` hits the same silent-nothing-matched trap,
  no OS-specific quirk needed). Python's `pathlib.Path.glob` has no
  equivalent trap — it only pattern-matches the final path component,
  never earlier directory segments. Plain directory listing sidesteps the
  whole class of bug and doubles as the "is this a directory" check.
  Regression-tested for both trigger cases (`go/acf/discover_test.go`);
  mutation-tested once during WP 2.1b's review round by reverting to
  `filepath.Glob` and confirming both new tests fail, then restoring the
  fix and confirming green again.
- **Manifest filename matching is case-insensitive.** `strings.ToLower`
  before the prefix/suffix check, so e.g. `AppManifest_100.ACF` is still
  found. Windows is the primary ADR-0005 target and Windows filesystems
  are case-insensitive; Python's `Path.glob` gets this for free there,
  a Go string comparison does not without the explicit lowering.
- **`Warning` slice instead of `logging.warning`.** Go has no ambient
  logging convention; `DiscoverInstalled` returns `(apps []InstalledApp,
  warnings []Warning)` and leaves it to the caller (WP 2.2's reporter) to
  decide how to surface them. The warning text distinguishes "no
  steamapps directory" (missing/not-a-directory) from "permission
  denied" — different operator actions, so collapsing them into one
  message would lose actionable information the Python spec's per-
  exception-type warning also preserves implicitly (a `PermissionError`
  vs `FileNotFoundError` read differently in a log).
- **Plain `string` for library/manifest paths, not a `Path` type.** Go
  has no cross-platform path-object abstraction equivalent to
  `pathlib.Path`; paths are plain strings throughout, joined with
  `path/filepath` where the code itself constructs a path (e.g.
  `steamapps` under a library root).
- **Invalid UTF-8 in a file is rejected with a `*ParseError`, not
  silently decoded.** Python's `read_text(encoding="utf-8-sig",
  errors="strict")` raises on a byte sequence that isn't valid UTF-8
  (e.g. a stray Latin-1/cp1252 byte like `0xE9` for an accented
  character in a manifest name), which the spec wraps into
  `VdfParseError` — corrupt file, skip it, warn. Go's `string(data)` has
  no equivalent guard: it silently substitutes the U+FFFD replacement
  character for every invalid byte, which would let a mojibake-mangled
  name flow all the way into `InstalledApp` (and from there, WP 2.2's
  reporter → vault-api) with no error at all. `readFileStripBOM` now
  calls `utf8.Valid` explicitly before returning.
- **`ParseError` carries a `Cause error` field and implements
  `Unwrap()`.** So callers can use `errors.Is`/`errors.As` — e.g.
  `errors.Is(err, fs.ErrNotExist)` in WP 2.2's reporter to tell "the
  manifest file vanished between listing and reading it" apart from "the
  manifest is corrupt", without string-matching the error message.

**Real-machine validation (WP 2.1b):** `DiscoverInstalled` was run from
WSL against the real `/mnt/c/steam` install (read-only — the install
itself was never modified) and found the same 15 installed apps with
zero warnings that the Python spec's empirical verification documents
(see "StateFlags" section below). The real `libraryfolders.vdf` on that
machine lists a Windows-native path (`C:\Steam`); resolving it from a
Linux-built binary needed a throwaway symlink in a scratch `/tmp`
directory (`ln -s /mnt/c/steam 'C:\Steam'`, deleted afterward) — a WSL-
vs-Windows path-syntax bridge for this one manual validation run, not
anything `go/acf` itself does or needs to do in real (single-OS)
production use. Real app names/sizes from that run are not reproduced
here or anywhere in the repo (fixture policy below).

**Parity with the Python spec:** every Python test in `tests/test_*.py`
(69 cases across 4 files, `pytest`-parametrized cases counted
individually) has a Go counterpart in `go/acf/*_test.go` using
`t.Run` subtests where Python used `@pytest.mark.parametrize`, so the
per-case count matches 1:1 (`go test ./... -v` reports 81 leaf test
cases total: the 69 ported plus 12 Go-only). The 12 Go-only tests pin
behavior the Python corpus implies but doesn't separately exercise:
- 3 depth-cap boundary tests (nesting depth exactly at the 100-level
  cap, one over it, and 1500);
- 2 `filepath.Glob`-vs-`os.ReadDir` regression tests (a library path
  containing `[` `]` glob-metacharacters, and — non-Windows only — a
  literal backslash), mutation-tested once by reverting to `filepath.Glob`
  and confirming both fail;
- 1 case-insensitive manifest filename match test;
- 2 invalid-UTF-8-rejection tests (one per `readFileStripBOM` caller)
  plus 1 discover-level test proving the invalid-UTF-8 file is skipped
  with a warning, not silently mangled into the result;
- 3 integer-overflow divergence tests (one per affected field — see
  below).

### Known divergences from the Python spec

The Go port is judged against the Python spec's test corpus and matches
it for every case that corpus (or any real Steam-written file) exercises.
Two divergences exist by deliberate choice rather than oversight —
named here instead of buried in a "matches bit-for-bit" claim that
wouldn't survive contact with either of them:

1. **Integer overflow** (`parseStrictUint`, `go/acf/strictuint.go`): Go's
   `int` is a 64-bit machine integer on every ADR-0005 build target;
   Python's `int` is arbitrary-precision. A digit string too large to fit
   (~19+ digits) is grammatically valid in both, but:
   - `SizeOnDisk` silently becomes `nil` in Go where Python would return
     the actual (huge) value — shaped like the existing "tolerated
     field" contract, but the number is lost, not just defaulted.
   - `StateFlags` makes Go reject the WHOLE appmanifest as corrupt
     (`*ParseError`) where Python would accept it with an enormous
     `StateFlags` value — a file Python treats as fine, Go skips.
   - `appid` is rejected the same way, even though appid is stored as a
     plain string and never used arithmetically anywhere in this
     package — kept anyway, for consistency with the ONE house rule
     this grammar check shares with `api/vault_api/deletion.py`'s
     `coerce_positive_id` (see "Integer field grammar" below), not
     because appid needs the numeric bound.

   No real Steam-written file has ever been observed anywhere near this
   range (StateFlags is a small bitmask, appid and SizeOnDisk are
   ordinary Steam-scale numbers) — this is a corruption/hostile-input
   edge case, not a real-world compatibility gap. Pinned by three tests
   in `go/acf/appmanifest_test.go`.

2. **`isAllASCIIDigits`** (`go/acf/libraryfolders.go`), used to recognize
   numbered library keys in `libraryfolders.vdf`: stricter than Python's
   `str.isdigit()`, which also accepts some non-ASCII Unicode digit-like
   characters that this ASCII-only check rejects. Every real
   libraryfolders.vdf uses plain ASCII numeric keys, so this has no
   observed real-world impact; documented so the divergence is a named,
   deliberate fact rather than an implicit (and wrong) "identical
   behavior" assumption.

## vault-agent CLI (WP 2.2)

The production entrypoint: `go/cmd/vault-agent` discovers the local Steam
library (`go/acf`) and reports the FULL installed-app-id list to vault-api
(`go/report` builds and locally validates the payload, `go/client` sends
it with retry). Deliberately dumb (plan §3): no control logic runs here —
scheduling, prefill decisions, and removal handling all live in vault-api.

Out of scope for this package (see the Status line above and the WP 2.2
brief): hosts-file mode (WP 2.3), a Windows Scheduled Task installer script
(WP 2.6), a Linux/SteamOS-specific library discovery variant and systemd
packaging (WP 2.5 — the binary runs on linux/amd64 and linux/arm64 today
with the OS-generic `~/.local/share/Steam` default, but that's this
package's default, not WP 2.5's dedicated variant).

### Usage

```
vault-agent report                one-shot: discover -> report -> print result -> exit
vault-agent report --loop         keep running, reporting every --interval
                                    (jittered ±10%) until SIGTERM/CTRL-C
```

One-shot is the PRIMARY mode (plan §7: a Windows Scheduled Task provides
the timing — see WP 2.6 for the installer). `--loop` exists for a systemd
user service (Phase 2.5's Linux/SteamOS packaging), where the service
itself needs to stay resident and do its own timing.

### Configuration: flags with an environment-variable fallback

No config FILE format (no TOML, no hand-rolled `KEY=VALUE` parser) — the
brief asked for "the simplest option that satisfies: one URL + API key",
and flags-with-env-fallback needs no parser at all. A flag, if given, wins
over its env var equivalent.

| Flag             | Env var                     | Required | Default                                              |
|------------------|------------------------------|----------|-------------------------------------------------------|
| `--server-url`   | `VAULT_AGENT_SERVER_URL`     | yes      | —                                                       |
| `--api-key`      | `VAULT_AGENT_API_KEY`        | yes      | — (prefer the env var: a flag value is visible in process listings / Task Manager) |
| `--client-id`    | `VAULT_AGENT_CLIENT_ID`      | no       | sanitized local hostname (see below)                    |
| `--library-root` | `VAULT_AGENT_LIBRARY_ROOT`   | no       | `C:\Program Files (x86)\Steam` (Windows) / `~/.local/share/Steam` (else) |
| `--interval`     | `VAULT_AGENT_REPORT_INTERVAL`| no       | `30m` (only consulted with `--loop`)                    |
| `--loop`         | —                            | no       | off (one-shot)                                          |

Why no config file: a file holding `VAULT_AGENT_API_KEY` needs its own
permission story and its own "never commit this" warning that this project
would then have to invent and document — whatever LAUNCHES the agent
(a Windows Scheduled Task's own environment, a systemd unit's
`Environment=`/`EnvironmentFile=`) already has that story solved. An env
var also never appears in a process listing the way a CLI flag would.

**`--server-url` validation:** must parse as an absolute `http://` or
`https://` URL with a host; a trailing slash is stripped. **`--client-id`**
is validated with the exact same rules vault-api enforces server-side
(`go/report.ValidateClientID`, mirroring
`vault_api.routers.agent.InstalledReportRequest`'s validator) — 1-64
characters, no surrounding whitespace, no control characters, not `.` or
`..` — so a bad value is rejected locally with a clear message instead of
spending a round trip on the 422 the server would return anyway.
**`--interval`** must be a positive Go duration (`"30m"`, `"1h"`, ...).

**Default `--client-id`:** the local hostname (`os.Hostname()`), trimmed,
with any control character replaced by `-`, truncated to 64 characters. If
`os.Hostname()` fails, or the sanitized result is empty / `.` / `..`,
config parsing fails loudly with an actionable message rather than
silently falling back to some placeholder value — set `--client-id`
explicitly in that case.

**Missing/invalid configuration fails loudly** (`go/agentconfig`): every
problem found (not just the first one) is collected into one error report,
printed to stderr, and the process exits **2** without attempting any
discovery or network call.

### Exit codes

| Code | Meaning |
|------|---------|
| `0`  | the report was sent and accepted (one-shot); or `--loop` exited cleanly on SIGTERM/CTRL-C; or `-h`/`--help` was requested |
| `1`  | a runtime failure: local report validation failed (should be unreachable in practice — acf's own parser already enforces the appid grammar — but checked, not assumed), or the HTTP client gave up (network error after retries, `401`, `422`, malformed response, ...) |
| `2`  | a configuration/usage error (missing/invalid flag or env var, no subcommand given) |

In `--loop` mode, a failed report is logged and the loop keeps going —
exit codes 0/1 only describe one-shot runs and how `--loop` itself
terminates, never an individual iteration inside the loop (plan §7:
"tolerate VPN/network outages" means staying up through a bad interval,
not exiting on one).

### What gets sent (ADR-0002) and the privacy boundary

Every `report` run sends exactly two things to **your own vault-api, over
your own network** (Tailscale/VPN/your reverse proxy — see plan §10; never
to any third party, never to Valve): the **complete** list of currently
installed Steam app ids (`go/acf.DiscoverInstalled` → `go/report.BuildReport`
— filtered to `StateFlags & 4`, de-duplicated, sorted), and the configured
`client_id`. Nothing else about the machine — no game names, no file
paths, no usernames, no Steam account/session identifiers — leaves it;
`InstalledApp.Name` is used only for local log/error messages
(`go/report`'s validation errors), never included in the wire payload
(`report.Payload` has exactly two JSON fields: `client_id`, `appids`).

The agent is **stateless and dumb by design**: it does not remember what
it reported last time, does not compute what changed, and does not decide
anything. vault-api stores each report as a snapshot and diffs it against
that client's previous one to derive `added`/`removed` — see
`api/README.md`'s "Agent reports" section for the full server-side
contract, and ADR-0002 for why removals are surfaced there but never acted
on automatically.

### Retry behavior (`go/client`)

Per-attempt timeout defaults to 15s; connection errors (refused, reset,
DNS failure, timeout), `5xx` responses, **and `429` (Too Many Requests)**
are retried with capped exponential backoff + jitter (default: up to 5
retries, 500ms base, 30s cap). `429` is the one `4xx` that heals by
waiting and resending — plan §9 recommends operators put rate limiting in
front of vault-api via their reverse proxy, so a real deployment can
plausibly return this. Any `Retry-After` header on a `429` is **deliberately
ignored** rather than parsed and honored precisely (plan §9's simplicity
principle: the backoff already waits between attempts, and this is a small
periodic status report, not high-volume traffic a precise wait would
meaningfully protect a server from) — pinned by
`TestReportInstalled_RetryAfterHeaderIsIgnored`, which sets a 3600s
`Retry-After` and asserts the retry still happens almost immediately.
Every OTHER `4xx` response (`401` bad key, `422` rejected body) is
**never** retried — resending the identical request cannot fix either, and
hammering the server on a genuine auth/validation failure would be
actively harmful. The backoff sleep itself is cancellable mid-wait (not
just checked before it starts) — canceling the context passed to
`ReportInstalled` (e.g. on SIGTERM in `--loop` mode) interrupts a pending
backoff sleep immediately rather than sitting through it.

**Worst-case retry wall time** with the defaults above: 6 total attempts
(1 initial + 5 retries), each up to the 15s per-attempt timeout, plus the
5 backoff sleeps between them at their upper bound
(500ms+1s+2s+4s+8s = 15.5s) ⇒ 6×15s + 15.5s ≈ **105.5s**. `cmd/vault-agent`
budgets **2 minutes** per report specifically to comfortably clear this
worst case without an operator needing to reason about the arithmetic
themselves.

TLS uses the OS/system root CA pool; `http_proxy`/`https_proxy`/`no_proxy`
environment variables are honored by default (`http.ProxyFromEnvironment`)
— set them in the environment vault-agent runs under if reaching the
server needs a proxy.

## What's here

`vault_agent/acf.py` — a small, dependency-free parser for Valve's
KeyValues text format (VDF/ACF), used for:

- `steamapps/appmanifest_<appid>.acf` — one installed app's metadata
  (appid, name, StateFlags, SizeOnDisk).
- `steamapps/libraryfolders.vdf` — the list of library folders (drives)
  Steam knows about.

and `discover_installed(library_root)`, which walks every library listed
in `libraryfolders.vdf` and returns the full list of installed apps.

The parser is a real tokenizer + recursive-descent parser (quoted keys/
values with escaped quotes and backslashes, nested `{ }` blocks, unquoted
bareword tokens, `//` line comments) — not regex line-picking. KeyValues
is a small format, so this stays one small module, stdlib only (no
third-party parser dependency — PROJECT_PLAN.md section 9).

## StateFlags: what "installed" means

`StateFlags` is a bitmask. Bit `4` means "fully installed". Other bits can
be set alongside it — e.g. an app mid-update still has bit 4 set (it's
still installed and playable, just stale) — so `InstalledApp.installed`
checks `state_flags & 4`, not equality against a specific value.

This was **empirically verified** against every real appmanifest file on
a real Steam install (`c:\steam` on the dev machine): all 15 currently
installed apps report `StateFlags == 4`. That machine has nothing
currently mid-update or partially downloaded, so the "update required"
(bit 2 alongside bit 4) and "partial/not yet installed" (bit 2 without
bit 4) cases are modeled from Valve's publicly documented StateFlags bit
combinations in synthetic test fixtures, not copied from a real file —
see Fixture Policy below.

The full documented bit table (SteamKit's `EAppState` enum, widely
mirrored across community Steam tooling — only bit 4 was independently
verified here) is reproduced next to `STATE_FLAG_FULLY_INSTALLED` in
`vault_agent/acf.py` so the Go port doesn't have to re-research it.

`appid` and `StateFlags` are validated against a strict ASCII-digit
grammar (see "Integer field grammar" below) — a value like `" 480 "` or
`"notanumber"` raises rather than being silently coerced.

## libraryfolders.vdf: two formats

- **Modern** (Steam client 2019+): numbered blocks, each with a `path`
  key (and an `apps` sub-block, not needed here). This is what the real
  file on the dev machine looks like — a single library, block `"0"`,
  with `path` pointing at the Steam install root itself.
- **Old flat format**: numbered keys mapping directly to a path string,
  alongside unrelated bookkeeping keys (`TimeNextStatsReport`,
  `ContentStatsID`, ...) that must be skipped. No file in this format
  exists on the dev machine (this is a pre-2019 Steam client format); the
  test fixture is synthetic, modeled on the documented format.

`discover_installed()` treats `library_root` (the argument you pass in —
the Steam install directory containing `steamapps/libraryfolders.vdf`)
as the source of truth for every library path, including the main one
itself, matching how the real file lists it.

## Windows library discovery (current scope)

On Windows, `library_root` is the Steam install directory (commonly
`C:\Program Files (x86)\Steam` or a custom path like `C:\Steam`). Nothing
in `vault_agent/acf.py` hardcodes a Windows path or a `\` separator —
every function takes `pathlib.Path` objects, so the same module will be
reused unchanged by the Linux/SteamOS agent variant (WP 2.5, ADR-0002),
which only needs to pass a different `library_root`
(`~/.local/share/Steam`) — no parser changes required.

## Integer field grammar

`appid`, `StateFlags`, and `SizeOnDisk` are validated with a strict
grammar (`_parse_strict_uint` in `vault_agent/acf.py`), deliberately
narrower than Python's `int()`:

**Accepted:** one or more ASCII digit characters (`0`-`9`), nothing else.
Leading zeros are tolerated (`"004"` -> `4`).

**Rejected** (all of these parse fine with plain `int()`, and are
therefore a real Python-vs-Go divergence if left unguarded): surrounding
whitespace (`" 4 "`), a leading `+`/`-` sign (`"+4"`), underscore
digit-group separators (`"1_0"` -> `10`), non-ASCII Unicode digit
characters (e.g. Arabic-Indic `"٤"` -> `4`). This mirrors Go's
`strconv.Atoi` (base 10), which accepts none of those either — since this
package is the executable specification for the Go port (ADR-0005), the
two are designed to agree on the same input, with one named exception:
digit strings too large for a 64-bit machine int, where Python's
arbitrary-precision `int` still accepts and Go's port does not — see the
Go port section's "Known divergences from the Python spec" above for the
three concrete consequences.

`appid` stays a `str` (it's an identifier, not a quantity), but is
validated with the same grammar; a value that fails it makes the whole
manifest raise `VdfParseError` (`discover_installed` then warns and skips
that file, per the resilience contract below). `StateFlags` is likewise
required and raises. `SizeOnDisk` is the one field that is tolerated —
missing or ungrammatical, it becomes `None` rather than failing the whole
record.

This grammar mirrors `api/vault_api/deletion.py`'s `coerce_positive_id`
(`value.isascii() and value.isdigit()`) — this project's one house rule
for "does this string look like an integer field", kept consistent
between `api/` and `agent/`.

## Other pinned parsing decisions

- **Unknown backslash escapes preserve the backslash.** `"a\qb"` parses
  to `a\qb` (backslash kept), not `aqb` (backslash dropped). A lone
  backslash before an unescaped character in a real-world file is far
  more likely to be an under-escaped Windows path
  (`"C:\Steam\common"` written without doubling the backslashes) than a
  deliberate unknown escape — dropping it would silently turn that into
  a *different, wrong* library path with no error raised. Recognized
  escapes remain `\"`, `\\`, `\n`, `\t`, `\r`.
- **Nesting depth is capped at 100 levels**, raising `VdfParseError`
  beyond that rather than recursing further. Real appmanifest/
  libraryfolders files nest 3 levels deep at most; the cap exists so a
  corrupt or hostile file with thousands of nested `{` cannot escape
  `discover_installed`'s single-exception-type contract with an uncaught
  `RecursionError`.
- **Platform conditional tags** (`[$WIN32]`, `[$LINUX]`, `[!$WIN32]`,
  ...) immediately after a key's value are tolerated and stripped, not
  treated as the start of the next pair — real Valve KeyValues files use
  this suffix (controller configs, localization files) to mark a pair as
  platform-specific, and failing the WHOLE file on encountering one would
  be needlessly fragile for a format-compliant construct. Deliberate
  simplification: the tag's *condition* is never evaluated — the pair is
  always kept regardless of which platform it names, since vault-agent
  only ever reads the manifest of the platform it runs on and has no
  reason to filter by platform. Only recognized directly after a value
  (the common, real-world placement); a conditional between a key and its
  value is not handled (not observed in practice in the two file types
  this module parses).
- **A leading UTF-8 BOM is stripped**, both when reading a file
  (`encoding="utf-8-sig"`) and defensively inside `parse_vdf` itself for
  a string handed in directly. Without this, the BOM character is
  neither whitespace, a brace, nor a quote, so the unquoted-bareword
  reader swallows it together with the following quoted key — corrupting
  the very first token of the file.

## Resilience contract

`discover_installed()` never crashes on bad local data. It logs a warning
(Python's standard `logging` module, logger name `vault_agent.acf`) and
degrades instead:

| Situation | Behavior |
|---|---|
| Missing/corrupt `libraryfolders.vdf` | Warn, fall back to treating `library_root` as the only library |
| Missing/corrupt `appmanifest_*.acf` (incl. non-grammatical `appid`/`StateFlags`) | Warn, skip that file |
| Library path listed but missing on disk | Warn, skip that library |
| Duplicate appid across libraries | Warn, first occurrence wins |
| Duplicate key at the same KeyValues nesting level (incl. a duplicated numbered library index) | Last occurrence silently wins (no warning — this is dict-level parsing, not file discovery) |
| Missing/non-grammatical `SizeOnDisk` | `size_on_disk` is `None`, record still returned |
| Nesting deeper than 100 levels | Whole file treated as corrupt (`VdfParseError` -> warn + skip) |
| Leading UTF-8 BOM | Stripped transparently, no warning |

## Fixture policy

Test fixtures under `agent/tests/fixtures/` are **synthetic** — modeled
on the structure of real files (verified against `c:\steam` on the dev
machine during development) but with fabricated app IDs, names, and
sizes. No real personal library data (owned game list, real appids/
sizes/build IDs) is committed to this repository.

## Running the tests

```powershell
cd agent
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
```

Runtime code (`vault_agent/`) has zero third-party dependencies; the venv
here is for running the test suite only.
