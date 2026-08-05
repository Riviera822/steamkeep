# vault-api

FastAPI + SQLite backend for SteamVault. Plain `sqlite3` with small helper
functions — no ORM. WP 1.2 shipped the project skeleton (config, DB schema,
auth dependency, `GET /v1/health`); WP 1.3 added depot→app mapping storage and
the games endpoints; WP 1.4 adds **prefill orchestration** — a job queue, the
SteamPrefill subprocess runner, and the prefill-driven mapping import
(`docs/PROJECT_PLAN.md` §3, §4, §6). Size calculation, deletion, the scheduler
and the miss trigger remain scope for later work packages.

## Layout

```
api/
├── vault_api/
│   ├── main.py           # FastAPI app factory + lifespan (starts the worker)
│   ├── config.py         # Settings, read once from env vars
│   ├── db.py             # SQLite schema v2, idempotent init
│   ├── auth.py           # X-Api-Key dependency (constant-time compare)
│   ├── deps.py           # Shared FastAPI dependencies (db_opener)
│   ├── mapping.py        # upsert_mapping() — the depot->app write path
│   ├── jobs.py           # job queue + apps.status transitions
│   ├── prefill.py        # SteamPrefill runner + depot attribution
│   ├── worker.py         # the single background job worker thread
│   └── routers/
│       ├── health.py     # GET /v1/health — the ONE public router
│       ├── games.py      # GET /v1/games, GET /v1/games/{appid}
│       ├── mapping.py    # PUT /v1/mapping/{depotid}, GET /v1/mapping
│       └── jobs.py       # POST /v1/prefill, GET /v1/jobs[/{id}]
├── tests/                # pytest (incl. tests/stub_prefill.py — fake CLI)
├── requirements.txt      # pinned, runtime only
├── requirements-dev.txt  # pinned, adds test-only deps (pytest, httpx)
├── .env.example          # committed template — never commit a real .env
└── pytest.ini
```

## Configuration

Read once at startup from environment variables (`vault_api/config.py`).
Copy `.env.example` to `.env` and adjust:

| Variable                        | Required | Default      | Purpose                                                            |
|---------------------------------|----------|--------------|--------------------------------------------------------------------|
| `VAULT_API_KEY`                 | yes      | *(none)*     | Shared secret for the `X-Api-Key` header                           |
| `VAULT_DB_PATH`                 | no       | `./vault.db` | SQLite database file                                               |
| `VAULT_CACHE_ROOT`              | no       | `./cache`    | Depot cache root — diffed before/after a prefill (see below)        |
| `VAULT_LOG_LEVEL`               | no       | `INFO`       | Log level                                                          |
| `VAULT_STEAMPREFILL_PATH`       | no*      | *(empty)*    | Path to the SteamPrefill executable; *required to run prefill jobs* |
| `VAULT_PREFILL_TIMEOUT_SECONDS` | no       | `14400`      | Hard time budget for one SteamPrefill run (hang backstop)           |
| `VAULT_WORKER_POLL_SECONDS`     | no       | `1.0`        | Worker sleep between polls of an empty queue                        |

`VAULT_API_KEY` has no default. Starting the app without it raises
`RuntimeError` immediately (`Settings.from_env`) — this is the "fail loudly"
behavior required by the work package, verified in `tests/test_config.py`.

`VAULT_STEAMPREFILL_PATH` is deliberately **not** validated at startup (the
`no*` above): vault-api must still serve `/v1/games`, `/v1/mapping` and
`/v1/health` on a host where SteamPrefill hasn't been set up yet. A missing or
wrong path fails the individual *job* with an actionable message
(`tests/test_worker.py::test_missing_steamprefill_path_fails_the_job_but_not_the_app`).
The two numeric settings reject non-numeric and non-positive values loudly at
startup.

## Database schema (v2)

Created idempotently at startup by `vault_api/db.py::init_db` (safe to call
on every process start — uses `CREATE TABLE IF NOT EXISTS` and only seeds
`schema_version` once).

| Table            | Columns                                                                                   | Purpose |
|------------------|--------------------------------------------------------------------------------------------|---------|
| `schema_version` | `version`                                                                                   | Single-row marker for future migrations |
| `apps`           | `appid` (PK), `name`, `status`, `last_prefill_at`, `last_manifest_check`                    | One row per tracked Steam app |
| `depot_app_map`  | `depotid`, `appid`, PK `(depotid, appid)`                                                   | Depot→app mapping; a depot can map to multiple apps (shared depots, plan §4) |
| `jobs`           | `id` (PK autoincrement), `appid`, `type`, `status`, `created_at`, `started_at`, `finished_at`, `log_excerpt` | Prefill/GC job queue (plan §3, §6) |
| `agent_reports`  | `client_id`, `reported_at`, `appids` (JSON array of ints)                                    | One row per agent report — a full installed-app-ID snapshot at that timestamp (ADR-0002: the agent is stateless/dumb, always reports the complete list). vault-api derives additions/removals by diffing the two most recent rows per `client_id` |

Indexes beyond the primary keys: `idx_depot_app_map_appid` on
`depot_app_map(appid)` (plan §4's main lookup direction is appid → depots,
e.g. for delete/size-by-game), `idx_agent_reports_client_time` on
`agent_reports(client_id, reported_at DESC)` (fetch the latest report(s) per
client for the diff), and — new in **v2 (WP 1.4)** — `idx_jobs_status_id` on
`jobs(status, id)` plus `idx_jobs_appid_status` on `jobs(appid, status)`. The
worker asks "oldest queued job?" on every poll tick and `POST /v1/prefill` asks
"does this app already have a queued/running job?" on every request; without
those two indexes both scan the whole append-only `jobs` table.

**Migration story.** Every statement in `db.py`'s DDL is
`CREATE ... IF NOT EXISTS`, and v1 → v2 is purely additive (two indexes), so
`init_db` upgrades an existing v1 file by running the current DDL and then
recording the new `schema_version`. A stored version *higher* than
`SCHEMA_VERSION` raises `RuntimeError` instead of silently operating on a
schema this code doesn't know (downgrade guard). The first non-additive change
will need a real per-version step list — that's called out in `init_db`'s
docstring. Both paths are covered in `tests/test_db.py`.

**Retention (not yet implemented):** `agent_reports` grows one row per
client per report interval indefinitely. A simple policy — e.g. keep only
the last N reports per `client_id`, pruned on insert or via a periodic job —
is deferred to the work package that implements the diff logic (Phase 2).

No foreign keys enforced between `depot_app_map`/`jobs`/`agent_reports` and
`apps.appid` — depot mappings and agent reports may arrive before an
app row exists (e.g. first prefill of a new title). Revisit if this becomes
a data-integrity problem in practice.

**Connection pragmas** (`db.py::get_connection`): `journal_mode=WAL` and
`busy_timeout=5000` (ms) on every connection, so HTTP request handlers
reading the database don't immediately fail with "database is locked" while
the background job worker holds a write transaction.

### Connection handling — `deps.db_opener` (WP 1.4, replaces WP 1.3's `get_db`)

WP 1.3 injected a `sqlite3.Connection` via a sync **generator** dependency
(open → `yield` → close in a `finally`), with `check_same_thread=False` to
tolerate the connection crossing anyio worker threads. **That combination
segfaulted the interpreter under concurrent load** — reproduced repeatedly on
Windows/CPython 3.12 as `Windows fatal exception: access violation`, ~1 in 1–3
runs of the hammer test, once `POST /v1/prefill` (a *write* endpoint that takes
SQLite's write lock) joined the request mix.

Root cause, measured — not guessed. Wrapping `sqlite3.Connection` in a subclass
that took a per-connection lock around every `execute`/`commit`/`close` and
recorded contention produced this, five times per run:

```
close  owner (thread 11072, 'execute:BEGIN IMMEDIATE')  intruder thread 36880
  fastapi/routing.py:290  async with AsyncExitStack() as async_exit_stack:
  contextlib.py:737       cb_suppress = await cb(*exc_details)
  vault_api/deps.py:31    conn.close()
```

One thread is blocked *inside* `execute()` on the connection (waiting on the
write lock, GIL released) while FastAPI's `AsyncExitStack` unwinds the
dependency on another thread and calls `close()` on that same connection.
`sqlite3_close` concurrent with `sqlite3_step` is a use-after-free at the C
level: sqlite's serialized threading mode protects the *database*, not
CPython's per-connection objects. With the lock in place the crash disappeared
and only the (separate) mapping race surfaced — that is the confirmation.

The fix is structural rather than another flag: **a connection never leaves the
thread that created it and never outlives the endpoint body.**
`deps.db_opener` is a plain (non-generator) dependency that returns a
zero-argument *opener*; endpoints do

```python
def list_games(open_db: DbOpener = Depends(db_opener)) -> list[GameSummary]:
    with open_db() as conn:
        ...
```

so open, use and close all happen inside the single `run_in_threadpool` call
that runs the body. Nothing else holds the connection, so nothing else can
close it. `check_same_thread` is consequently back at its **safe default
(`True`)** — a future accidental hand-off is now a loud `ProgrammingError`
instead of a native crash, pinned by
`tests/test_db.py::test_connections_are_thread_confined_by_default`.

The job worker follows the same rule from the other side: it owns exactly one
connection, created inside its own thread, closed by that thread on exit.

Regression tests: `tests/test_concurrency.py` — the existing mixed-request
hammer (now 50 parallel requests incl. `POST /v1/prefill` and `GET /v1/jobs`)
plus `test_http_reads_survive_a_worker_writing_in_the_background`, which
hammers reads while the real worker thread writes jobs/apps/mapping rows.

**Also fixed (same class, found by the same hammer): `upsert_mapping`'s app-row
creation was `SELECT`-then-`INSERT`.** Two writers upserting the *same* new
appid both saw "no row" and both inserted; the loser got
`IntegrityError: UNIQUE constraint failed: apps.appid` and the request 500'd. It
is now `INSERT OR IGNORE` plus a conditional name `UPDATE`, pinned by
`tests/test_mapping.py::test_concurrent_upsert_of_the_same_new_appid_does_not_500`.

## Endpoints (WP 1.3 + 1.4)

All routes below require `X-Api-Key` (see "Auth"). Full API table:
`docs/PROJECT_PLAN.md` §6; the games, mapping, prefill and jobs rows are
implemented so far.

| Method | Endpoint                          | Purpose |
|--------|-------------------------------------|---------|
| GET    | `/v1/games`                        | All tracked apps: `appid`, `name`, `status`, `last_prefill_at`, `depot_count`, `size_bytes` (always `null` until WP 1.5 adds the per-app size calculation) |
| GET    | `/v1/games/{appid}`                | Detail for one app: same fields plus `depots` (list of `{depotid, shared}`); `404` for an unknown `appid` |
| PUT    | `/v1/mapping/{depotid}`            | Body `{"appid": int, "app_name": str \| null}` — **additively** upsert one depot→app mapping fact (manual fallback, see below); `422` for `depotid <= 0`, `appid <= 0`, or an unrecognized body field |
| GET    | `/v1/mapping`                      | Full depot→app mapping table: list of `{depotid, appid}` |
| DELETE | `/v1/mapping/{depotid}/{appid}`    | Remove one mapping pair (correction path for the additive `PUT`, see below); `204` on success, `404` if the pair doesn't exist, `422` for non-positive ids |
| POST   | `/v1/prefill`                      | Body `{"appids": [int, ...]}` — queue one prefill job per app id. `202` with a list of `{appid, job_id, status, deduplicated}`. `422` for an empty list, an appid `< 1`, a non-list, or an unrecognized body field |
| GET    | `/v1/jobs`                         | Recent jobs, newest first. `?limit=` 1–200, default 20 (`422` outside that range). Omits `log_excerpt` on purpose — this is the polling list |
| GET    | `/v1/jobs/{id}`                    | One job incl. `log_excerpt`; `404` for an unknown id |

## Prefill orchestration (WP 1.4)

### Queue semantics

- `POST /v1/prefill` only *enqueues* and returns `202` immediately — the app
  polls `GET /v1/jobs/{id}` for the outcome.
- **Exactly one job runs at a time** (plan §3), FIFO by job id.
- **Dedupe:** if an app already has a `queued` or `running` job, that job is
  returned with `"deduplicated": true` instead of a second one being stacked.
  Duplicate app ids *within one body* fall out of the same rule — the response
  keeps one entry per requested id, in request order, so the repeat points at
  the same `job_id`. Rationale: the app fires this on a button press and Phase
  3's miss trigger (ADR-0001) will fire it on cache misses, so repeats are the
  normal case. A *finished* job never blocks a new one.
- Enqueueing an app that has never been seen creates its `apps` row (status
  `idle`) so `GET /v1/games/{appid}` answers 200 while the job is still queued.
- Statuses: jobs are `queued` → `running` → `done` | `error`; `apps.status`
  follows `idle` → `running` → `done` | `error` (plan §3). `last_prefill_at` is
  written **only** on success.
- `log_excerpt` is the ANSI-stripped **tail** of SteamPrefill's combined
  stdout/stderr, capped at 4 KiB and prefixed with `[...truncated...]` when it
  was cut, plus vault-api's own `[vault-api] …` diagnostic lines.

### Worker lifecycle

One background thread, started by the FastAPI lifespan
(`vault_api/worker.py`) — "one job queue, not Celery".

1. **Startup, before the worker starts:** `jobs.recover_stale_jobs` fails every
   job still marked `running`. The rule: vault-api runs exactly one worker in
   exactly one process, and at startup that worker has claimed nothing, so a
   `running` row can only be an orphan from a process that died mid-job (crash,
   container kill, reboot). Leaving it would also wedge the dedupe rule —
   `POST /v1/prefill` would keep handing out a job id nothing is executing. The
   app's `status` is repaired the same way (otherwise a permanent yellow badge).
   **Honest caveat: do not point two vault-api processes at the same database.**
   The second one's startup would fail the first one's genuinely running job.
   Single-process operation is the documented deployment model (one container),
   not something the code enforces.
2. **Loop:** claim (atomic conditional `UPDATE` inside `BEGIN IMMEDIATE`) →
   run → record; sleep `VAULT_WORKER_POLL_SECONDS` when the queue is empty.
   A crash inside a job is caught, recorded on that job, and the thread lives on
   — a bug must not silently stop the queue draining.
3. **Shutdown:** the stop event makes the loop exit before claiming another job,
   *and* a prefill subprocess in flight is terminated (then killed after a
   grace period) — otherwise `docker stop` would block for as long as the
   download takes. The aborted job is recorded as `error` with a clear reason.
   A hard `SIGKILL` still leaves a `running` row, which step 1 cleans up.

### SteamPrefill invocation — verified, not assumed

Checked empirically against `poc/steamprefill/bin/SteamPrefill.exe` (v3.7.1)
while writing this package:

- `prefill --help` offers `--all`, `--recent`, `--recently-purchased`, `--top`,
  `-f|--force`, `--os`, `--verbose`, `--unit`, `--no-ansi`. **There is no
  `--app-ids`-style option and no positional app id** — `prefill zzz` is
  rejected with `Unexpected parameter(s): <zzz>`, and the binary contains no
  `app-ids` string at all.
- The selection `prefill` consumes is a **state file**:
  `<exe dir>/Config/selectedAppsToPrefill.json`, a plain JSON array of app ids.
  Observed content `[3419430]`; `select-apps status` read it back and listed
  exactly that one app, confirming the file is the selection store (the
  interactive `select-apps` TUI is just one writer of it).

So the invocation vault-api uses is:

```
write <exe dir>/Config/selectedAppsToPrefill.json  =  [<appid>]
run   <exe> prefill --force --no-ansi     (cwd = exe dir, stdin = DEVNULL)
```

- **`--force` is deliberate.** Without it SteamPrefill skips apps its own
  `Config/successfullyDownloadedDepots.json` thinks are up to date — state that
  knows nothing about vault-api deleting an app from the cache
  (`DELETE /v1/cache/{appid}`, WP 1.6), so a non-forced run would silently
  refuse to refill a game we just deleted. Chunks still on disk are re-requested
  and served by vault-core as local HITs, so the cost is disk speed, not
  internet bandwidth (Phase 0: HIT ~120× faster than MISS, ADR-0001).
- **`--no-ansi` is not sufficient** — Spectre.Console's exception renderer still
  emits SGR escapes (observed), so captured output is ANSI-stripped in code.
- **vault-api OWNS `Config/selectedAppsToPrefill.json`** and overwrites it on
  every job. A manual `select-apps` selection on the same SteamPrefill
  installation will be replaced. That is the trade-off of the only
  non-interactive selection mechanism v3.7.1 offers.
- OS selection is left at SteamPrefill's default (Windows). Prefilling Linux
  depots for Steam Deck clients (ADR-0002) would need `--os linux` and is not
  in this package's scope.

### Login prerequisite (and why a job can't hang on it)

SteamPrefill needs a Steam session and prompts interactively when it has none.
vault-api runs it with **stdin closed** (`DEVNULL`), which was verified against
a fresh copy of the real binary with an empty `Config/`: it does *not* hang — it
prints `A Steam account is required in order to prefill apps!` /
`Please enter your Steam account name :` and dies with
`InvalidOperationException: Failed to read input in non-interactive mode.`,
exit code 1, in about a second. Login happens *before* cache detection, so this
is the first thing that fails on an unconfigured install.

vault-api detects that output and fails the job with an actionable message
("run `SteamPrefill select-apps` in a terminal once, enter your
account/password/Steam Guard code, then retry"). **Log in once by hand on the
server before the first job.** `VAULT_PREFILL_TIMEOUT_SECONDS` is still enforced
as a backstop — the fast-fail above is proven, a wedged network connection or a
future Steam Guard prompt variant is not.

**vault-api never sees, stores, transmits or logs Steam credentials.** The
session lives in SteamPrefill's own `Config/` directory next to the executable.

### Mapping import — replace-semantics per app (ADR-0003 decision 3)

Which depots belong to the prefilled app is determined by **diffing the cache**
around the run, not by parsing SteamPrefill's output:

1. Before the subprocess starts, snapshot every
   `<VAULT_CACHE_ROOT>/depot/<depotid>/` directory as
   `(file_count, total_bytes, newest_mtime_ns)`. An aggregate, not a file
   listing — a real cache holds hundreds of thousands of chunks and this runs
   twice per job. Directories with zero files are ignored (nothing was stored).
2. After a **successful** run, snapshot again. Depots that are new or whose
   signature changed were filled by this job — unambiguous because only one job
   runs at a time.
3. `prefill.apply_observed_mapping` then writes the mapping:
   - **Replace within the app:** rows mapped to this appid that were *not*
     observed are deleted, so a depot Steam reassigned away from the app stops
     being reported as its content (and stops blocking WP 1.6's deletion as a
     false "shared" depot).
   - **Additive across apps:** only rows for *this* appid are touched. An
     observed depot that another app also maps keeps that mapping — plan §4's
     shared-depot case (redistributables); both apps keep reporting it as
     `shared`.

Edge cases, stated honestly:

- **Nothing observed → nothing changed.** A fully-cached app re-prefilled with
  `--force` serves every chunk from disk, writes nothing, and therefore yields
  an empty observation. The existing mapping is then left *completely* alone —
  wiping it on the strength of no evidence would delete correct data. The cost:
  a stale depot row only disappears once a prefill actually writes something for
  that app again (a game update), or via
  `DELETE /v1/mapping/{depotid}/{appid}`. The job log says so explicitly.
- **A failed, timed-out or aborted run never touches the mapping.** A partial
  run is not evidence about which depots belong to the app.
- **Concurrent cache writes are misattributed.** One job at a time makes
  *prefill* attribution unambiguous, but vault-core's store-on-miss keeps
  writing while a job runs — a LAN client downloading a *different* game
  through the cache during the window would have its depots attributed to the
  prefilled app. Acceptable for now (prefill is the primary fill mechanism, and
  the result is an over-broad mapping, not a lost one); a genuine fix needs
  per-app depot lists from PICS, which is a later package's business.
- **`successfullyDownloadedDepots.json` is not usable as a mapping source.** It
  maps depot id → manifest id (observed:
  `{"242921":[...],"3419431":[...]}`) with no app attribution, so it cannot
  replace the diff.

### Depot→app mapping semantics (plan §4)

`vault_api/mapping.py::upsert_mapping(conn, depotid, appid, name)` is the
single write path for the mapping table:

- Creates the `apps` row if it doesn't exist yet (status `'idle'`).
- Idempotent: calling it again with the same `(depotid, appid)` pair is a
  no-op the second time (`INSERT OR IGNORE` into `depot_app_map`) — the
  same pair legitimately gets reported repeatedly (repeated prefill runs,
  repeated manual calls).
- A `None` name never overwrites an existing name (only fills it in if the
  app row didn't have one yet).
- **This is the hook WP 1.4's prefill flow calls directly** — SteamPrefill
  knows the depot→app mapping at download time (plan §4), so the prefill
  job writes mappings by calling `upsert_mapping` in-process, not via HTTP.
- `PUT /v1/mapping/{depotid}` / `GET /v1/mapping` are the **manual
  fallback** for edge cases plan §4 names explicitly (delisted games,
  depots SteamPrefill doesn't recognize) — deliberately minimal, not a
  general CRUD surface.

**Mapping is additive, not replace-on-conflict (WP 1.3 review, B2 — decided
by the orchestrator, plan §4-conformant).** `PUT /v1/mapping/{depotid}`
with a *different* `appid` than an existing mapping for that depotid does
**not** replace the old mapping — it **adds** a second `(depotid, appid)`
row. A depot legitimately belonging to two tracked apps is exactly plan
§4's shared-depot case (redistributables, shared content), not an error
condition; a "last write wins" upsert would silently break that. Concretely:
re-`PUT`-ing depotid 999 first under appid 440 then under appid 730 leaves
*both* mappings in place, and `GET /v1/games/440` and `GET /v1/games/730`
both report depot 999 as `"shared": true`.

**Corrections use `DELETE /v1/mapping/{depotid}/{appid}`**, not the `PUT`.
If a mapping was created with the wrong `appid` (fat-fingered manual entry),
delete the specific wrong pair and `PUT` the correct one — don't rely on
`PUT` to overwrite it, since it won't. `DELETE` removes exactly that one
pair from `depot_app_map` and **never touches the `apps` row** — it's a
mapping correction, not a "forget this game" operation (that's
`DELETE /v1/cache/{appid}`, later work package, plan §4/§6). `404` if the
pair doesn't exist.

**Shared-depot flagging** (`GET /v1/games/{appid}`): a depot is reported
`"shared": true` if `depot_app_map` has any other `appid` row for the same
`depotid` (a single correlated `EXISTS` subquery per the full depot list,
not a per-depot round-trip). Plan §4: shared depots (redistributables,
shared content) must be skipped on deletion and reported, not silently
removed — this endpoint is what surfaces that fact before a deletion is
even attempted.

### Input validation on the manual fallback endpoints (WP 1.3 review, should-fix)

Steam depot/app ids are always positive, and this is a human-driven
fallback endpoint (typos are the expected failure mode, not malice):

- `depotid` (`PUT`/`DELETE` path parameter) and `appid` (`PUT` body field,
  `DELETE` path parameter) all require `>= 1`; `0` or negative values `422`
  instead of creating a permanent junk row that nothing can clean up yet
  (no delete-by-appid exists before this endpoint).
- `MappingUpsertRequest` uses `model_config = ConfigDict(extra="forbid")` —
  an unrecognized body field (e.g. a typo'd `appId`) `422`s instead of
  silently upserting with `app_name` defaulting to `None`.

## Auth

Every endpoint requires the header `X-Api-Key: <VAULT_API_KEY>`, checked
with a constant-time comparison (`hmac.compare_digest`) in the
`require_api_key` FastAPI dependency (`vault_api/auth.py`). Missing or
wrong key → `401`.

**Non-ASCII keys (WP 1.3 fix):** `hmac.compare_digest` raises `TypeError`
for a non-ASCII `str` argument (confirmed empirically — a key containing
e.g. "café" raised `TypeError: comparing strings with non-ASCII characters
is not supported`), which FastAPI/Starlette previously surfaced as an
unhandled `500` instead of a `401`. `require_api_key` now encodes both the
provided and expected key to UTF-8 bytes (`surrogateescape` error handler,
which also absorbs the rarer case of a lone surrogate code point reaching
the header) and compares the bytes with `hmac.compare_digest` — bytes have
no charset restriction, so a non-ASCII key is now treated like any other
wrong key: `401`. Covered by
`tests/test_auth.py::test_non_ascii_key_is_rejected_with_401_not_500`.

**The single documented exception is `GET /v1/health`.** Plan §9 states
"API designed with no unauthenticated endpoints"; plan §6 and §10
simultaneously designate `/v1/health` as the liveness endpoint "polled by
any external monitoring system", which by definition cannot present an API
key. This work package resolves the conflict in `/v1/health`'s favor for
that one route because:

- it returns a fixed body (`{"status": "ok"}`) and nothing else — no app
  names, sizes, job state, or client identifiers ever appear in it;
- it exposes no data and offers no way to enumerate or mutate anything;
- external monitoring (uptime checks, container healthchecks) is a stated
  requirement (plan §10) that an API-key gate would defeat.

This is now factually the *only* unauthenticated surface: FastAPI's
auto-generated docs (`/docs`, `/redoc`) and schema (`/openapi.json`) are
disabled (`FastAPI(..., openapi_url=None)` in `main.py`) rather than left
open, since they would otherwise expose the full route/schema map without
a key — and Swagger UI's assets load from a CDN anyway, so they wouldn't
render on an offline homelab install regardless.

**Secure-by-default pattern.** Auth is attached at the `APIRouter` level,
not per-route: `routers/health.py` is the one router with no
`require_api_key` dependency; every other router (`routers/games.py`,
`routers/mapping.py`, `routers/jobs.py`, and future ones — cache, clients,
agent) is constructed as
`APIRouter(dependencies=[Depends(require_api_key)])`, so a route added to it is
authenticated automatically and can't be forgotten.
`tests/test_security.py` enforces this by walking `app.routes` and
asserting the dependency is present everywhere except `/v1/health` — it picked
up WP 1.4's three new routes with no test change, verified by rerunning it.

**`GET /v1/ping` removed (WP 1.3).** It was WP 1.2's scaffold route,
existing solely so the test suite had an authenticated endpoint to exercise
`require_api_key` against before real routes existed. Now that
`/v1/games`, `/v1/games/{appid}`, `/v1/mapping/{depotid}` and `/v1/mapping`
exist, the scaffold is gone (`routers/internal.py` deleted) and the test
suite (`tests/test_auth.py`) exercises auth against `/v1/games` instead.

## Running natively (Windows dev, no Docker until WP 1.9)

```powershell
cd api
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # then edit VAULT_API_KEY
.venv\Scripts\python -m uvicorn vault_api.main:create_app --factory --host 0.0.0.0 --port 8000
```

Verify:

```powershell
curl http://localhost:8000/v1/health
# {"status":"ok"}

curl http://localhost:8000/v1/games
# 401 - missing X-Api-Key

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/games
# []

curl -H "X-Api-Key: <your key>" -X PUT http://localhost:8000/v1/mapping/441 ^
     -H "Content-Type: application/json" -d "{\"appid\": 440, \"app_name\": \"Team Fortress 2\"}"
# {"depotid":441,"appid":440}

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/games
# [{"appid":440,"name":"Team Fortress 2","status":"idle","last_prefill_at":null,"depot_count":1,"size_bytes":null}]

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/games/440
# {"appid":440,"name":"Team Fortress 2","status":"idle","last_prefill_at":null,"depots":[{"depotid":441,"shared":false}],"size_bytes":null}

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/games/999999
# 404

curl -H "X-Api-Key: <your key>" -X DELETE http://localhost:8000/v1/mapping/441/440
# 204 (mapping removed, apps row untouched)

curl -H "X-Api-Key: <your key>" -X DELETE http://localhost:8000/v1/mapping/441/440
# 404 (already gone - second delete of the same pair)

curl http://localhost:8000/v1/ping
# 404 - removed in WP 1.3, real routes now exist

curl http://localhost:8000/docs
curl http://localhost:8000/openapi.json
# both 404 - disabled
```

All of the above was run against a live `uvicorn` instance during WP 1.3
review, not assumed from library docs.

### WP 1.4: prefill via curl (live-verified)

Requires `VAULT_STEAMPREFILL_PATH` and a SteamPrefill that has been logged in
once interactively.

```powershell
curl -H "X-Api-Key: <your key>" -X POST http://localhost:8000/v1/prefill ^
     -H "Content-Type: application/json" -d "{\"appids\": [440, 730, 440]}"
# 202
# [{"appid":440,"job_id":1,"status":"queued","deduplicated":false},
#  {"appid":730,"job_id":2,"status":"queued","deduplicated":false},
#  {"appid":440,"job_id":1,"status":"queued","deduplicated":true}]

curl -H "X-Api-Key: <your key>" -X POST http://localhost:8000/v1/prefill ^
     -H "Content-Type: application/json" -d "{\"appids\": [440]}"
# 202 - [{"appid":440,"job_id":1,"status":"queued","deduplicated":true}]  (no new job)

curl -H "X-Api-Key: <your key>" -X POST http://localhost:8000/v1/prefill ^
     -H "Content-Type: application/json" -d "{\"appids\": []}"
# 422 (also for [0], a non-list, or an unknown body field)

curl -H "X-Api-Key: <your key>" "http://localhost:8000/v1/jobs?limit=5"
# [{"id":2,"appid":730,"type":"prefill","status":"done","created_at":"...","started_at":"...","finished_at":"..."},
#  {"id":1,"appid":440,"type":"prefill","status":"done", ...}]

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/jobs/1
# {"id":1,"appid":440,...,"status":"done","log_excerpt":"...\n[vault-api] Depot mapping
#  updated (replace-semantics for this app): added=[441, 442] removed=[] unchanged=[]"}

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/jobs/9999
# 404

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/games/440
# {"appid":440,...,"status":"done","last_prefill_at":"...",
#  "depots":[{"depotid":441,"shared":false},{"depotid":442,"shared":true}],"size_bytes":null}
```

Run against a live `uvicorn` instance with a fake SteamPrefill
(`tests/stub_prefill.py`) pointed at by `VAULT_STEAMPREFILL_PATH`; the depot
directories, the shared-depot flag for the depot both apps filled, and the
`Config/selectedAppsToPrefill.json` content were all checked on disk
afterwards. `401` without a key and `404` for `/docs` still hold.

## Tests

```powershell
cd api
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

107 tests, no network and no Steam login required.

Covers: health returns `ok` without a key and leaks nothing else; every
registered route requires `require_api_key` except `/v1/health` (route-walk
regression test, `test_security.py`); `/docs`, `/redoc`, `/openapi.json` all
return 404; schema creation is idempotent
(tables exist, `schema_version` has exactly one row after calling `init_db`
twice, expected indexes and the reshaped `agent_reports` columns are
present, WAL + busy_timeout pragmas are active); missing/blank
`VAULT_API_KEY` fails loudly via `Settings.from_env`.

WP 1.3 additions:

- `test_auth.py`: 401 without/with-wrong key and 200 with the correct key
  against `/v1/games` (the auth dependency's real target now that
  `/v1/ping` is gone); a **non-ASCII key returns 401, not 500**
  (`test_non_ascii_key_is_rejected_with_401_not_500` — the carry-over fix,
  reproduced by sending raw non-ASCII UTF-8 bytes as the header value via
  `httpx.Headers`, since `str` headers with non-ASCII characters can't even
  be sent by httpx/TestClient).
- `test_mapping.py`: `upsert_mapping` unit tests (app auto-creation, same
  pair upserted twice is idempotent, a `None` name doesn't clobber an
  existing name, the same depotid under two appids is allowed/shared) plus
  HTTP tests for `PUT`/`GET`/`DELETE /v1/mapping` (401 without a key, upsert
  then list round-trip, `app_name` is optional).
- `test_games.py`: `GET /v1/games` happy path (empty list, depot count,
  `size_bytes: null`) and `GET /v1/games/{appid}` happy path (depot list,
  `shared` flagging across two apps sharing one depot, `404` for an unknown
  appid), both with a 401-without-key check.
- `test_concurrency.py` (B1 regression): ~30 concurrent mixed requests
  (games list/detail, mapping PUT) via `httpx.ASGITransport` +
  `asyncio.gather`, asserting every response is non-5xx — see the
  `check_same_thread=False` note above for what this pins.

WP 1.3-review additions (B2 + should-fixes), also in `test_mapping.py`:

- **Additive mapping is pinned**, not just implemented: two `PUT`s of the
  same depotid under different appids leave both mappings in place and
  both apps report `shared: true` for that depot
  (`test_put_same_depot_different_appids_adds_both_and_flags_shared`).
- `DELETE /v1/mapping/{depotid}/{appid}`: 401 without a key, removes
  exactly the targeted pair (sibling pair and the `apps` row survive),
  `404` for a pair that doesn't exist, plus a DB-level unit test for
  `mapping.delete_mapping`'s `True`/`False` return value.
- Validation: `depotid <= 0`, `appid <= 0` (on both `PUT` and `DELETE`),
  and an unrecognized `PUT` body field all `422`.
- `test_security.py`'s existing route-walk test covers the new `DELETE`
  route automatically — no test change needed there, verified by rerunning
  it after adding the route.

WP 1.4 additions:

- `tests/stub_prefill.py` — a **fake SteamPrefill executable** generated per
  test. It reads its selection from `Config/selectedAppsToPrefill.json` next to
  itself (so the tests prove the runner really uses the verified mechanism),
  writes fake depot chunk files into a temp cache root, records its argv and its
  start/finish timestamps, and can reproduce: success, an arbitrary non-zero
  exit, the **verbatim** not-logged-in output of the real binary, a hang, a
  chatty run (>16 KB output), and a no-op "already cached" run. Emitted as a
  `.cmd` shim on Windows and a `sh` shim elsewhere — both verified to pass argv
  through, hand the child an EOF stdin, and propagate the exit code.
- `test_jobs_api.py`: 401 on all three new routes; `202` + one queued job per
  appid; the `apps` row is created at enqueue (so `GET /v1/games/{appid}` is 200
  while queued); dedupe against a queued job, duplicate ids inside one body, and
  the fact that a *finished* job doesn't block a new one; eight distinct `422`
  bodies; `?limit` bounds; newest-first ordering; `log_excerpt` absent from the
  list and present in the detail; `404` for an unknown job id.
- `test_jobs_queue.py`: FIFO claiming, `started_at`/`finished_at` transitions,
  dedupe against a *running* job, tail-truncation of the log excerpt, the
  crash-recovery rule (running → error, queued untouched, `apps.status`
  repaired, and the recovered job no longer blocking a new enqueue), rollback +
  `isolation_level` restoration of `immediate_transaction`, and two
  **concurrency** tests: 6 threads racing to claim 40 jobs hand out each job
  exactly once, and 8 threads enqueueing the same appid create exactly one job.
- `test_prefill_runner.py`: missing/nonexistent `VAULT_STEAMPREFILL_PATH` is a
  per-job failure not an exception; **the verified argv
  `["prefill", "--force", "--no-ansi"]` and the selection file are asserted**;
  stdin is EOF in the child; the selection is rewritten per job; non-zero exit
  (with stderr merged in), not-logged-in detection with the actionable hint,
  timeout kill, abort-on-request, ANSI stripping, >16 KB output captured without
  a pipe deadlock; `scan_depots`/`diff_depots` (missing root, non-numeric and
  empty depot dirs ignored, new + changed detected, removed ignored); and
  `apply_observed_mapping`'s three rules (replace within the app, shared depots
  of other apps preserved, empty observation changes nothing).
- `test_worker.py`: full stack through HTTP with the stub — success updates job,
  `apps.status`, `last_prefill_at` and the mapping; the ADR-0003 replace +
  shared-depot case end to end; the "nothing observed" case; **strict
  one-at-a-time FIFO execution proven by non-overlapping subprocess timestamps**;
  failure exit code, not-logged-in, and timeout paths all leaving the mapping
  untouched; a hanging job not stalling the queue; a missing SteamPrefill path
  failing the job while `/v1/health` and `/v1/games` keep working; startup
  crash-recovery through the lifespan; shutdown aborting a running job instead
  of waiting out a 600 s timeout; and a guard that no worker runs when the
  lifespan doesn't (which is what keeps the other test modules deterministic).
- `test_concurrency.py`: the mixed hammer grew to 50 parallel requests including
  `POST /v1/prefill` and `GET /v1/jobs`, plus a new test that hammers reads
  while the real worker thread writes in the background. Both pin the
  access-violation fix described under "Connection handling".
- `test_db.py` / `test_config.py`: the two new `jobs` indexes, the v1 → v2
  in-place upgrade, the newer-database downgrade guard, thread-confinement of
  connections, and the three new settings incl. their loud validation.
