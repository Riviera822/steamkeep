# vault-agent

The PC listener for SteamVault. Deliberately dumb by design (see
`docs/PROJECT_PLAN.md` section 3): read the local Steam library, report
what's installed, no control logic on the device. All prefill/scheduling
decisions live in vault-api.

Status: work in progress. This package currently ships the ACF/VDF parser
(WP 2.1) only — no HTTP reporter yet (WP 2.2), no hosts-file mode (WP 2.3),
no Linux/SteamOS variant (WP 2.5, see ADR-0002).

**Executable specification (ADR-0005):** vault-agent ships as a Go binary
in production; this Python package and its test suite are kept as the
pinned reference for that port (WP 2.1b). Every parsing decision below —
especially the ones that are conventions rather than the one obviously
correct answer (escape handling, nesting limits, conditional tags) — is
deliberately spelled out and tested so the Go port can match it exactly
rather than re-deriving it.

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
two must agree on the same input byte-for-byte.

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
