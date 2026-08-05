# vault-api

FastAPI + SQLite backend for SteamVault. Plain `sqlite3` with small helper
functions — no ORM. WP 1.2 shipped the project skeleton (config, DB schema,
auth dependency, `GET /v1/health`); WP 1.3 added depot→app mapping storage and
the games endpoints; WP 1.4 added **prefill orchestration** — a job queue, the
SteamPrefill subprocess runner, and the prefill-driven mapping import; WP 1.5
added **per-game size calculation** (`vault_api/sizes.py`) — a cached "du over
depot folders" (`docs/PROJECT_PLAN.md` §3), wired into `GET /v1/games`/
`GET /v1/games/{appid}`, plus `GET /v1/cache/summary` (§6); WP 1.6 adds
**per-game deletion** (`vault_api/deletion.py`) — `DELETE /v1/cache/{appid}`
with shared-depot protection, path/link safety guards and an audit trail in the
log (§4). The scheduler, garbage collection and the miss trigger remain scope
for later work packages.

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
│   ├── sizes.py          # depot disk walk, TTL size cache, summary aggregation
│   ├── deletion.py       # per-game deletion: path/link guards, shared-depot plan
│   └── routers/
│       ├── health.py     # GET /v1/health — the ONE public router
│       ├── games.py      # GET /v1/games, GET /v1/games/{appid}
│       ├── mapping.py    # PUT /v1/mapping/{depotid}, GET /v1/mapping
│       ├── jobs.py       # POST /v1/prefill, GET /v1/jobs[/{id}]
│       └── cache.py      # GET /v1/cache/summary, DELETE /v1/cache/{appid}
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
| `VAULT_CACHE_ROOT`              | no       | `./cache`    | Depot cache root — diffed before/after a prefill, and the **deletion base** (guarded, see below) |
| `VAULT_LOG_LEVEL`               | no       | `INFO`       | Log level                                                          |
| `VAULT_STEAMPREFILL_PATH`       | no*      | *(empty)*    | Path to the SteamPrefill executable; *required to run prefill jobs* |
| `VAULT_PREFILL_TIMEOUT_SECONDS` | no       | `14400`      | Hard time budget for one SteamPrefill run (hang backstop)           |
| `VAULT_WORKER_POLL_SECONDS`     | no       | `1.0`        | Worker sleep between polls of an empty queue                        |
| `VAULT_SIZE_CACHE_TTL`          | no       | `60`         | TTL (seconds) for the in-process per-game size cache; **must be > 0** (see below) |

`VAULT_API_KEY` has no default. Starting the app without it raises
`RuntimeError` immediately (`Settings.from_env`) — this is the "fail loudly"
behavior required by the work package, verified in `tests/test_config.py`.

`VAULT_STEAMPREFILL_PATH` is deliberately **not** validated at startup (the
`no*` above): vault-api must still serve `/v1/games`, `/v1/mapping` and
`/v1/health` on a host where SteamPrefill hasn't been set up yet. A missing or
wrong path fails the individual *job* with an actionable message
(`tests/test_worker.py::test_missing_steamprefill_path_fails_the_job_but_not_the_app`).
The three numeric settings (`VAULT_PREFILL_TIMEOUT_SECONDS`,
`VAULT_WORKER_POLL_SECONDS`, `VAULT_SIZE_CACHE_TTL`) reject non-numeric and
non-positive values loudly at startup. In particular **`VAULT_SIZE_CACHE_TTL`
must be greater than 0**: `0` is rejected rather than accepted as "no caching",
because a zero TTL would mean a full `depot/` tree walk on *every* request —
a footgun on a large cache, not a feature. Set a small value (e.g. `1`) if you
want near-live numbers; note the cache is invalidated explicitly after a
successful prefill and after a deletion anyway, so a long TTL does not make
those two events look stale.

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

**Ordering fix (WP 1.5 carry-over from the WP 1.4 review):** `init_db` now
creates only the `schema_version` table, reads the stored version, and checks
the downgrade guard *before* running the rest of `_DDL` — previously the full
DDL ran unconditionally first and the guard was checked afterwards, so it read
as a gate without functioning as one (harmless so far since every change has
been additive, but a future non-additive statement would already have run
against a newer-than-understood schema by the time the check fired).

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

## Endpoints (WP 1.3 + 1.4 + 1.5 + 1.6)

All routes below require `X-Api-Key` (see "Auth"). Full API table:
`docs/PROJECT_PLAN.md` §6; the games, mapping, prefill, jobs and cache rows
are implemented so far.

| Method | Endpoint                          | Purpose |
|--------|-------------------------------------|---------|
| GET    | `/v1/games`                        | All tracked apps: `appid`, `name`, `status`, `last_prefill_at`, `depot_count`, `size_bytes` (sum of the app's mapped depots' bytes on disk; `null` if unmapped or not yet cached — see "Per-game size calculation" below) |
| GET    | `/v1/games/{appid}`                | Detail for one app: same fields plus `depots` (list of `{depotid, shared, size_bytes}`); `404` for an unknown `appid` |
| PUT    | `/v1/mapping/{depotid}`            | Body `{"appid": int, "app_name": str \| null}` — **additively** upsert one depot→app mapping fact (manual fallback, see below); `422` for `depotid <= 0`, `appid <= 0`, or an unrecognized body field |
| GET    | `/v1/mapping`                      | Full depot→app mapping table: list of `{depotid, appid}` |
| DELETE | `/v1/mapping/{depotid}/{appid}`    | Remove one mapping pair (correction path for the additive `PUT`, see below); `204` on success, `404` if the pair doesn't exist, `422` for non-positive ids |
| POST   | `/v1/prefill`                      | Body `{"appids": [int, ...]}` — queue one prefill job per app id. `202` with a list of `{appid, job_id, status, deduplicated}`. `422` for an empty list, an appid `< 1`, a non-list, or an unrecognized body field |
| GET    | `/v1/jobs`                         | Recent jobs, newest first. `?limit=` 1–200, default 20 (`422` outside that range). Omits `log_excerpt` on purpose — this is the polling list |
| GET    | `/v1/jobs/{id}`                    | One job incl. `log_excerpt`; `404` for an unknown id |
| DELETE | `/v1/cache/{appid}`                | Delete this game's depot directories. `200` with `{appid, deleted_depots[], skipped_shared[], failed[], total_bytes_freed}`; `404` unknown appid or no mappings; `409` while a prefill job for the app is queued/running; `422` for `appid < 1`; `500` if the cache-root guards refuse. See "Per-game deletion" below |
| GET    | `/v1/cache/summary`                | `total_bytes` (disk usage of `depot/`, each depot counted once), `top_consumers` (top 10 `{appid, name, size_bytes}`, largest first), `unmapped_depots` (`{count, size_bytes}` for depot dirs on disk with no mapping row for any app), `free_disk_bytes` (free space on the cache filesystem, `null` if undeterminable) |

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
4. **On success only:** the shared `SizeCache` (WP 1.5, see "Per-game size
   calculation" below) is invalidated so `GET /v1/games` reflects the new
   disk content immediately instead of waiting out `VAULT_SIZE_CACHE_TTL`.

`PrefillWorker` previously carried a `_finished` event that was set on exit
but never read anywhere (WP 1.4 review carry-over) — removed in WP 1.5 rather
than kept as unused state; `stop()`'s `thread.join()` already provides the
deterministic wait tests need.

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
- **The write is atomic (WP 1.5 carry-over fix from the WP 1.4 review):**
  `write_selected_apps` writes to a tempfile in the same `Config/` directory,
  then `os.replace`s it over the real path — a same-directory tempfile keeps
  the replace a same-filesystem rename (atomic on both POSIX and Windows), so
  nothing that reads the file (SteamPrefill itself, mid-write) can ever
  observe a half-written selection. The previous `open(path, "w")` truncated
  the file in place first, with no such guarantee.
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

## Per-game size calculation and cache summary (WP 1.5)

`vault_api/sizes.py` implements plan §3's "per-game size calculation (du over
depot folders, cached)" and feeds `GET /v1/games`, `GET /v1/games/{appid}` and
the new `GET /v1/cache/summary` (plan §6).

### The disk walk — shared with the prefill attribution diff

`sizes.scan_depot_signatures(cache_root)` walks `<cache_root>/depot/<depotid>/`
and returns `(file_count, total_bytes, newest_mtime_ns)` per depot id. This is
the **same** function `vault_api.prefill.scan_depots` uses for its
before/after attribution diff (WP 1.4) — `prefill.scan_depots` is now a thin
wrapper around it (WP 1.5 carry-over fix from the WP 1.4 review: the walk used
to be duplicated, `os.walk` + a separate `os.stat()` per file in both places).
The walk itself (`sizes.walk_file_stats`) uses `os.scandir` +
`DirEntry.stat()` instead — measured 17x faster on a 21k-file depot tree
(0.922s → 0.055s) because `DirEntry.stat()` is answered from data the
directory listing already returned rather than a second filesystem round
trip per file. `sizes.scan_depot_dir_bytes` is just the byte totals out of
the same signatures.

**Link handling (WP 1.6 carry-over fix #1 from the WP 1.5 review).** Top-level
depot directories are detected **following** links (`entry.is_dir()`), because
placing one depot directory on another volume via a symlink/junction is a
legitimate homelab move. Previously they were detected with
`follow_symlinks=False`, which made a *symlinked* depot directory **invisible**
— and the damage went beyond an under-reported size: the depot never showed up
in a prefill's before/after snapshot either, so `prefill.apply_observed_mapping`
treated the app's correct mapping row as stale and **deleted** it on the next
prefill. Measured: `DirEntry.is_dir(follow_symlinks=False)` is `False` for a
directory symlink but `True` for a Windows junction, so only symlinks were
affected — and that same asymmetry is why the fix is pinned by a test using a
**real** directory symlink (`make_dir_link` in `tests/conftest.py`, which falls
back to `mklink /J` where Windows refuses symlink creation without Developer
Mode).

*Inside* the tree the walk still refuses to follow links, and now refuses
junctions too (`sizes.is_link_like`): `follow_symlinks=False` alone reports a
junction as an ordinary directory, so a junction pointing at one of its own
ancestors would have recursed forever, and a junction pointing outside the cache
would have counted foreign bytes as this depot's size. Skipping them also keeps
"reported size" equal to "bytes a deletion would actually free".

### The TTL cache

`sizes.SizeCache` is deliberately the simplest thing that could work (plan
§9): one `threading.Lock`, one cached `SizeSnapshot` (`{depot_bytes,
total_bytes, computed_at}`), no background thread, no second table. A cache
miss (TTL expired, or never computed) walks the whole `depot/` tree once
**while holding the lock**, so concurrent callers that arrive during that walk
wait for and reuse the one fresh result instead of each re-walking the tree —
the lock doubles as request coalescing. One `SizeCache` instance lives on
`app.state.size_cache` (created in `main.create_app`, alongside
`app.state.settings`) and is shared by every request via the
`deps.get_size_cache` dependency — the whole point of the cache is that
concurrent requests share one scan.

TTL default: 60s, tunable via `VAULT_SIZE_CACHE_TTL`. **Invalidation is
explicit, not polled** — plan §3 says "cached", not "polled every N seconds":
`vault_api/worker.py` calls `size_cache.invalidate()` right after a
**successful** prefill job (unconditionally — even a "nothing new observed"
`--force` run may have rewritten existing chunks), so a game's size is never
more than `VAULT_SIZE_CACHE_TTL` seconds stale for an otherwise-idle cache,
and is fresh immediately after a fill. `SizeCache.invalidate()` is exported
for exactly this pattern — WP 1.6's deletion endpoint will call it the same
way after removing an app's depot folders.

### Per-app sizes: shared depots counted into every app that maps them

`sizes.app_size_bytes(depotids, depot_bytes)` sums an app's mapped depots'
bytes. A depot shared with another tracked app (plan §4: redistributables)
counts its **full** size into **every** app that maps it — this answers "how
much would deleting just this game free up if its depots weren't shared",
which is what an operator deciding what to delete wants to know. The
consequence, stated plainly and pinned by `tests/test_sizes.py`: **per-app
sizes may sum to more than the cache's actual disk usage.**
`GET /v1/cache/summary`'s `total_bytes` is the other number — each depot
counted exactly once — precisely so both questions can be answered.

Returns `None` (not `0`) in two distinct cases the API must not confuse:
- **unmapped** — the app has no depot rows at all (`depot_count == 0`).
- **uncached** — depots are mapped, but none have ever been written to disk
  (mapped before the first successful prefill, or a `--force` re-run that
  wrote nothing new — see the prefill mapping semantics above).

A *partially*-cached app (some depots on disk, others not yet) returns the sum
of what IS on disk — a missing depot there contributes `0` correctly, it just
hasn't been filled yet.

### `GET /v1/cache/summary`

`sizes.build_cache_summary` assembles the whole response from one `SizeCache`
snapshot plus two small queries (`depot_app_map` for per-app depot membership +
`apps` for names) — a summary request inside the TTL window costs no filesystem
access at all.

**WP 1.6 carry-over fix #2 from the WP 1.5 review:** the function now takes a
ready `SizeSnapshot` instead of the `SizeCache`, and `routers/cache.py` fetches
that snapshot *before* opening its SQLite connection. Previously a cache miss
walked the whole `depot/` tree under `SizeCache`'s lock while an open database
connection sat idle in the caller. Same fix removed the dynamically built
`WHERE appid IN (?,?,?)` list (with its `WHERE 0` special case for the empty
set) in favour of `SELECT appid, name FROM apps` plus a dict lookup — one row
per tracked game is less data than the mapping query already reads.

- **`total_bytes`** — real disk usage of `depot/`, each depot id counted once
  (see above).
- **`top_consumers`** — top 10 apps by `size_bytes` (ties broken by `appid`
  for a deterministic order), using the same `app_size_bytes` per-app sums
  `GET /v1/games` reports.
- **`unmapped_depots`** — `{count, size_bytes}` for depot directories present
  on disk with **no** `depot_app_map` row for *any* app. This is real operator
  information (plan §6): either a mapping was deleted/never created, or
  vault-core's store-on-miss wrote a depot that hasn't been attributed to an
  app by a prefill run yet (see the "concurrent cache writes" caveat under
  Mapping import above).
- **`free_disk_bytes`** — `shutil.disk_usage()` on `VAULT_CACHE_ROOT`, walking
  up to the nearest existing ancestor directory first (a fresh install may not
  have created it yet); `null` if no ancestor exists at all (defensive, should
  not happen on a real filesystem).

## Per-game deletion (WP 1.6)

`DELETE /v1/cache/{appid}` implements plan §4's "Deletion" section:
`appid → [depotids] → remove those depot directories → reset status to idle`,
with **shared-depot protection**. The mechanics live in
`vault_api/deletion.py`, the HTTP shape in `vault_api/routers/cache.py`.

### Semantics

| Situation | Result |
|---|---|
| `appid` has no `apps` row | `404` — nothing to delete |
| `appid` has no `depot_app_map` rows | `404` — nothing to delete |
| a prefill job for the app is `queued`/`running` | `409` — retry after the job |
| `appid < 1` | `422` |
| the `VAULT_CACHE_ROOT` guards refuse | `500`, nothing deleted (see "Path safety") |
| otherwise | `200` with the per-depot report |

```json
{
  "appid": 440,
  "deleted_depots":  [{"depotid": 441, "size_bytes_freed": 1000000}],
  "skipped_shared":  [{"depotid": 900, "shared_with": [730]}],
  "failed":          [],
  "total_bytes_freed": 1000000
}
```

- **Shared depots are never deleted.** A depot that any *other* tracked app also
  maps is skipped and reported with the other app ids (plan §4: "2 depots shared
  with game Y, not deleted"). This holds in both directions — after deleting
  game A, deleting game B still finds depot 900 shared, because A's mapping rows
  are kept (see below). Exclusivity is decided **twice**: once when the plan is
  built, and again immediately before each individual depot is removed (see
  "Concurrency" below) — a depot that only became shared in between is kept and
  reported in `skipped_shared` with its fresh owner list.
- **A depot that is already gone** counts as `deleted` with
  `size_bytes_freed: 0` rather than as an error: the endpoint is idempotent, so a
  repeated `DELETE` (or a racing second one) answers `200` with zeros instead of
  failing.
- **`total_bytes_freed` is a floor, never an overstatement.** Sizes are measured
  by walking each depot directory immediately before removing it. A depot whose
  removal fails part-way contributes `0`, and a symlinked/junctioned depot
  directory contributes `0` because only the link is removed (its target's bytes
  are not freed).
- **`failed` is a partial deletion, still reported as `200`** (not `207`): the
  body already says per depot what happened, and a multi-status code would force
  every client to special-case something it cannot act on differently. What
  matters is that a depot still on disk is *never* reported as deleted.
- **`last_prefill_at` is always cleared, and `apps.status` reflects the
  outcome**: `idle` after a clean deletion, **`error` if anything landed in
  `failed`**. The status rule is deliberate (WP 1.6 review): *"failed" does not
  mean "untouched"*. `shutil.rmtree` deletes files as it walks and only then
  raises — measured here, an open handle on the 11th of 20 chunk files left 10
  of them already deleted — so a failed depot is typically **half** deleted
  while reporting `0` bytes freed. Leaving such an app at `done` would put a
  green badge on a half-destroyed game, and Phase 3's staleness check compares
  manifest ids rather than file counts, so it would never re-fill it. `error` is
  the honest state: the app UI surfaces it, and a re-prefill (which runs with
  `--force`) repairs the cache. Clearing `last_prefill_at` is unconditional for
  the same reason — that timestamp is no longer true either way. (Honest edge
  case: an app whose depots are *all* shared has nothing deleted and is still
  reset to `idle`, even though its content remains on disk inside the shared
  depots. `GET /v1/games` still reports its real `size_bytes`, so the operator
  sees the truth.)
- **The `SizeCache` is invalidated** right after the deletion
  (`sizes.SizeCache.invalidate()`, the hook WP 1.5 exported for exactly this),
  so `GET /v1/games` and `GET /v1/cache/summary` never serve pre-deletion sizes
  for up to `VAULT_SIZE_CACHE_TTL` seconds.

### The mapping rows are KEPT (decision)

Deleting a game from the cache does **not** delete its `depot_app_map` rows. The
mapping is *knowledge* — "these depots are this game's content" — not cache
state. Keeping it means a later prefill reuses it, the shared-depot protection
keeps working for the other apps that share a depot, and no information gathered
from PICS/prefill is thrown away by a cleanup action.

Consequences, stated plainly:

- `GET /v1/games/{appid}` keeps listing the app's depots, each with
  `size_bytes: null`, and the app's own `size_bytes` becomes `null` **only if all
  of its depots are gone**. An app that kept a shared depot still reports that
  depot's bytes — which is correct: that content is still on disk.
- `depot_count` does not drop after a deletion.
- Removing a mapping stays a separate, explicit operation:
  `DELETE /v1/mapping/{depotid}/{appid}` (ADR-0003's repair path).

### Path safety

The deletion target is built from the **integer** depot id only (`int` → `str`),
joined under the resolved cache root, and verified before anything is removed.
Both inputs come from outside the code, so both are guarded — as small pure
functions with direct unit tests (`tests/test_cache_delete.py`):

- `deletion.resolve_depot_root(cache_root)` refuses, deleting nothing, when
  `VAULT_CACHE_ROOT` is **empty** (measured: `os.path.abspath("")` is the current
  working directory, so an unset value would aim a recursive delete at wherever
  vault-api happens to run), resolves to a **filesystem root** (`/`, `C:\`, a
  bare UNC share — note `os.path.realpath("/")` is `C:\` on Windows, so a
  Docker-style path pasted onto a Windows host lands here), or contains **no
  `depot/` directory**. That last one refuses a misconfigured root and a
  never-used cache alike: both mean "there is nothing here to delete from", and
  saying so loudly beats "successfully deleted nothing".
- `deletion.depot_dir_path(depot_root, depotid)` rejects anything that is not a
  positive integer (`0`, negatives, `None`, `True`, floats, `"../../etc"`,
  `"441/../.."`, `"441; rm -rf /"`, `"C:\Windows"`, …) and then verifies the
  result is a **direct child** of `depot_root` (`dirname(candidate) ==
  depot_root`, which a `startswith` prefix check would not give you — that would
  accept `…/depot-evil`). Non-integer depot ids are not merely theoretical:
  SQLite's INTEGER *affinity* does not enforce the column type, so a hand-edited
  or corrupted database really can hold a string there. Such a row is reported in
  `failed` (with `depotid: 0`, since it cannot be named) and nothing is touched
  for it.
- A mapping row whose *co-owner* app id is unreadable makes the depot count as
  **shared** — something references it, so refusing to delete is the safe
  reading.
- String depot ids must be **exactly ASCII digits** (`isascii() and isdigit()`),
  not "whatever `int()` accepts" (WP 1.6 review). `int()` parses `" 441 "`,
  `"1_0"` and Arabic-Indic `"٤٤١"` perfectly happily; none of those is a depot id
  vault-api ever writes, and on the path that decides which directory gets
  destroyed a value that odd means the row is broken and the operator should be
  told rather than have it silently normalised.

### Link safety (symlinks and Windows junctions), measured

A depot directory that is a symlink or a junction must never be traversed —
recursively deleting through it would destroy data outside the cache. Everything
below was **measured** on Windows 11 / CPython 3.12.10 against a real
`mklink /J` junction pointing at a directory outside the cache root, and every
row is pinned by a test:

| Probe | Result |
|---|---|
| `os.path.islink(<junction>)` | **`False`** — an islink-only guard misses junctions |
| `os.path.isjunction(<junction>)` | `True` (CPython ≥ 3.12) |
| `DirEntry.is_dir(follow_symlinks=False)` on a junction | **`True`** — looks like a plain directory to a walk |
| `shutil.rmtree(<junction>)` | **raises** `OSError("Cannot call rmtree on a symbolic link")`; the target is untouched |
| `os.rmdir(<junction>)` | removes the junction only; target and its contents survive |
| `shutil.rmtree(<real dir containing a junction>)` | completes, removes the junction *as a link*, target survives |

So `shutil.rmtree` does **not** follow junctions — neither at the top level,
where it refuses outright, nor nested inside a tree, where it unlinks them. But
because it refuses outright, it also cannot delete a legitimately linked depot
directory. `deletion.remove_depot_dir` therefore handles the link case itself: if
the depot directory is link-like (`sizes.is_link_like`, which checks *both*
`islink` and `isjunction`), only the link is removed (`os.rmdir`, falling back to
`os.unlink` for POSIX symlinks, where `rmdir` fails with `ENOTDIR`); otherwise
`shutil.rmtree` runs. The nested-junction behavior is pinned by its own test so a
future CPython regression surfaces in this suite instead of in a user's data.

### Audit trail

Every decision goes to the standard `logging` module at INFO (failures at ERROR),
prefixed `cache-delete`, and records the **resolved absolute path** rather than
just the depot id — this is the audit trail for an operation that destroys user
data. Real output from the live verification below:

```
INFO vault_api.routers.cache: cache-delete appid=440 starting: depot_root=...\cache\depot exclusive=[441, 442] shared=[900] unusable_rows=0
INFO vault_api.deletion:      cache-delete appid=440 depot=900 KEPT: shared with app(s) [730]
INFO vault_api.deletion:      cache-delete appid=440 depot=441 DELETED path=...\cache\depot\441 bytes_freed=1000000 link=False
INFO vault_api.deletion:      cache-delete appid=440 depot=442 DELETED path=...\cache\depot\442 bytes_freed=2000000 link=False
INFO vault_api.routers.cache: cache-delete appid=440 finished: deleted=2 skipped_shared=1 (of which 0 late) failed=0 bytes_freed=3000000; status set to 'idle', last_prefill_at cleared; mapping rows kept
```

A depot that was already gone logs `ALREADY-ABSENT`, a path-guard rejection logs
`REFUSED by the path guard`, a failed removal logs `FAILED` with the exception
type and message, a depot removed by a racing request logs `ALREADY-ABSENT`
with the reason, a depot that became shared between plan and removal logs
`KEPT (late recheck)`, and a refused cache root logs
`REFUSED (cache root guard)`.

### Concurrency, transactions and cancellation

- **No database transaction is held across the filesystem work.** The endpoint
  opens a connection for the read (app row, mapping rows, active-job check),
  closes it, deletes, then opens a connection again for the status reset. That
  keeps the `deps.db_opener` rule intact (a connection never leaves the thread
  that created it, see "Connection handling") and means a long `rmtree` cannot
  block writers.
- **The shared-depot decision is rechecked at execute time (TOCTOU).** The plan
  is built from one snapshot at the start of the request, and no lock is held
  across the filesystem work — so a `PUT /v1/mapping/{depotid}` landing in that
  window could make a depot shared *after* it was planned for deletion, and
  deleting it would then destroy another game's content, breaking plan §4's
  guarantee. Immediately before removing **each** depot, its owners are re-read
  with one indexed lookup on `depot_app_map(depotid, appid)` (its primary key —
  an index seek, no transaction, which is what makes a per-depot recheck
  affordable); a depot that became shared is kept, logged as `KEPT (late
  recheck)` and reported in `skipped_shared`. If the recheck itself fails, the
  depot is **not** deleted and is reported in `failed` — "unknown ownership"
  must never resolve to "delete it". This narrows the window to the microseconds
  between the recheck and `remove_depot_dir`; a mapping written *during* the
  `rmtree` was always going to lose that race, and closing that last gap would
  need a lock held across filesystem work.
- **The 409 guard is check-then-act, and that is stated rather than hidden.** A
  prefill job enqueued in the microseconds after the check would still race the
  deletion. Single-worker operation keeps the window tiny and the outcome is
  benign (the job refills what was deleted), so this stays a guard rather than a
  lock that would have to be held across filesystem work.
- **Two concurrent `DELETE`s of the same app** both answer `200`: the loser
  reports zeros, so the freed bytes are counted exactly once. Getting that right
  needs more than a `try`/`except` — the loser's `rmtree` walks a tree the winner
  is deleting underneath it and raises part-way through, while the outer depot
  directory may still be there for another moment. `deletion.remove_depot_dir_settling`
  therefore decides the outcome from the **settled** filesystem state: a removal
  that raised an `OSError` is retried a few times (it is idempotent; worst case
  ~80 ms) and only a path that is *still present* at the end counts as a failure.
  Retrying every `OSError` rather than just `FileNotFoundError` is measured, not
  padding: on Windows a racing deletion surfaces as `PermissionError [WinError 5]`
  just as often, because a deleted-but-still-open file enters "delete pending" and
  reports access-denied instead of not-found. Without this, a perfectly clean
  concurrent deletion landed in `failed` and dragged the app to `status='error'`
  (measured: `test_two_racing_deletes_do_not_5xx_or_double_delete` failed in 2 of
  15 isolated module runs before the fix, 0 of 70 after). Also covered by the
  mixed hammer in `tests/test_concurrency.py`.
- **Deletion runs in the request thread** (a sync endpoint, so FastAPI's
  threadpool) — right for the size of a typical depot tree, and it is what lets
  the response report exactly what happened. A client that disconnects
  mid-deletion does **not** abort the work; only the report is lost.

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
`routers/mapping.py`, `routers/jobs.py`, `routers/cache.py`, and future ones —
clients, agent) is constructed as
`APIRouter(dependencies=[Depends(require_api_key)])`, so a route added to it is
authenticated automatically and can't be forgotten.
`tests/test_security.py` enforces this by walking `app.routes` and
asserting the dependency is present everywhere except `/v1/health` — it picked
up WP 1.4's three new routes and WP 1.6's `DELETE /v1/cache/{appid}` with no
test change, verified by rerunning it (a route that deletes user data is exactly
the one you do not want to add unauthenticated by accident).

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
# {"appid":440,"name":"Team Fortress 2","status":"idle","last_prefill_at":null,
#  "depots":[{"depotid":441,"shared":false,"size_bytes":null}],"size_bytes":null}
# (size_bytes fields added in WP 1.5; null here because nothing has been
# written to VAULT_CACHE_ROOT yet in this walkthrough)

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

### WP 1.5: sizes and cache summary via curl (live-verified)

Against a live `uvicorn` instance with `VAULT_CACHE_ROOT` pointed at a
directory seeded by hand with depot files (441: 1,000,000 bytes, mapped only
to appid 440; 900: 5,000,000 bytes, mapped to both 440 and 730 — the shared
case; 555: 250,000 bytes, deliberately left unmapped) and mappings created via
the existing `PUT /v1/mapping/{depotid}`:

```
curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/games
# [{"appid":440,"name":"Team Fortress 2","status":"idle","last_prefill_at":null,
#   "depot_count":2,"size_bytes":6000000},
#  {"appid":730,"name":"Counter-Strike 2","status":"idle","last_prefill_at":null,
#   "depot_count":1,"size_bytes":5000000}]

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/games/440
# {"appid":440,"name":"Team Fortress 2","status":"idle","last_prefill_at":null,
#  "depots":[{"depotid":441,"shared":false,"size_bytes":1000000},
#            {"depotid":900,"shared":true,"size_bytes":5000000}],
#  "size_bytes":6000000}

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/games/730
# {"appid":730,...,"depots":[{"depotid":900,"shared":true,"size_bytes":5000000}],
#  "size_bytes":5000000}

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/cache/summary
# {"total_bytes":6250000,
#  "top_consumers":[{"appid":440,"name":"Team Fortress 2","size_bytes":6000000},
#                    {"appid":730,"name":"Counter-Strike 2","size_bytes":5000000}],
#  "unmapped_depots":{"count":1,"size_bytes":250000},
#  "free_disk_bytes":119640584192}

curl http://localhost:8000/v1/cache/summary
# 401 - missing X-Api-Key header (router-level auth, no route added here forgets it)
```

`total_bytes` (6,250,000 = 1,000,000 + 5,000,000 + 250,000) counts depot 900
exactly once; `size_bytes` on the two games (6,000,000 + 5,000,000 =
11,000,000) sums to MORE than that, because the shared depot is counted fully
into both — exactly the documented asymmetry (see "Per-game size calculation"
above). `unmapped_depots` correctly picked up depot 555, which has no mapping
row at all.

### WP 1.6: per-game deletion via curl (live-verified)

Against a live `uvicorn` instance with `VAULT_CACHE_ROOT` seeded by hand (441:
1,000,000 bytes and 442: 2,000,000 bytes mapped only to appid 440; 900:
5,000,000 bytes mapped to both 440 and 730 — the shared case; 555: 250,000 bytes
left unmapped) and `VAULT_WORKER_POLL_SECONDS=30` so a queued job stays queued
long enough to demonstrate the 409:

```
curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/games
# [{"appid":440,...,"depot_count":3,"size_bytes":8000000},
#  {"appid":730,...,"depot_count":1,"size_bytes":5000000}]

curl -X DELETE http://localhost:8000/v1/cache/440
# 401 - missing X-Api-Key

curl -H "X-Api-Key: <your key>" -X DELETE http://localhost:8000/v1/cache/0
# 422 - appid must be >= 1

curl -H "X-Api-Key: <your key>" -X DELETE http://localhost:8000/v1/cache/999999
# 404 {"detail":"Unknown appid 999999 - vault-api tracks no such app, so there is nothing to delete."}

curl -H "X-Api-Key: <your key>" -X DELETE http://localhost:8000/v1/cache/440
# 200
# {"appid":440,
#  "deleted_depots":[{"depotid":441,"size_bytes_freed":1000000},
#                    {"depotid":442,"size_bytes_freed":2000000}],
#  "skipped_shared":[{"depotid":900,"shared_with":[730]}],
#  "failed":[],"total_bytes_freed":3000000}
# on disk afterwards: depot/ contains only 555 and 900 - the shared depot and the
# unrelated unmapped one both survived

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/games/440
# {"appid":440,"name":"Team Fortress 2","status":"idle","last_prefill_at":null,
#  "depots":[{"depotid":441,"shared":false,"size_bytes":null},
#            {"depotid":442,"shared":false,"size_bytes":null},
#            {"depotid":900,"shared":true,"size_bytes":5000000}],
#  "size_bytes":5000000}
# mapping rows kept (depot list intact), the deleted depots report null, and the
# app's total is now just the kept shared depot

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/cache/summary
# {"total_bytes":5250000,...}   (was 8250000 - immediately, without waiting out
#                                VAULT_SIZE_CACHE_TTL: the cache was invalidated)

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/mapping
# [{"depotid":441,"appid":440},{"depotid":442,"appid":440},
#  {"depotid":900,"appid":440},{"depotid":900,"appid":730}]

curl -H "X-Api-Key: <your key>" -X DELETE http://localhost:8000/v1/cache/440
# 200 - clean no-op: deleted_depots report size_bytes_freed 0, failed is empty
# {"appid":440,"deleted_depots":[{"depotid":441,"size_bytes_freed":0},
#                               {"depotid":442,"size_bytes_freed":0}],
#  "skipped_shared":[{"depotid":900,"shared_with":[730]}],
#  "failed":[],"total_bytes_freed":0}

curl -H "X-Api-Key: <your key>" -X DELETE http://localhost:8000/v1/cache/730
# 200 - the other direction: 900 is still shared with 440, so nothing is deleted
# {"appid":730,"deleted_depots":[],
#  "skipped_shared":[{"depotid":900,"shared_with":[440]}],
#  "failed":[],"total_bytes_freed":0}

curl -H "X-Api-Key: <your key>" -X POST http://localhost:8000/v1/prefill ^
     -H "Content-Type: application/json" -d "{\"appids\": [440]}"
curl -H "X-Api-Key: <your key>" -X DELETE http://localhost:8000/v1/cache/440
# 409 {"detail":"Prefill job 1 for app 440 is queued. Deleting depots while they
#      are being downloaded would delete under an active write - retry once that
#      job has finished (poll GET /v1/jobs/{id})."}
```

Depot 900's chunk file was checked on disk after all of the above: still present,
still 5,000,000 bytes. The `cache-delete` audit lines quoted under "Audit trail"
are from this same run.

## Tests

```powershell
cd api
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

200 tests, no network and no Steam login required.

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

WP 1.5 additions:

- `test_sizes.py`: the disk walk (`walk_file_stats` finds nested files, a
  missing path yields nothing) and `scan_depot_dir_bytes`/
  `scan_depot_signatures` (per-depot byte sums, non-numeric/empty dirs
  ignored, **pinned equal to `prefill.scan_depots`'s output** — the shared-walk
  carry-over fix, not just "both happen to work"); `app_size_bytes`'s four
  cases (unmapped → `None`, uncached → `None`, partially cached → sum of what
  exists, a shared depot counted fully into two different apps' sums);
  `SizeCache` TTL behavior with an injected clock (stale within the window,
  recomputes after expiry, `invalidate()` forces a recompute regardless of
  TTL) and that `total_bytes` counts a depot once even though two apps map it;
  `free_disk_bytes` on both an existing path and one that needs to walk up to
  an existing ancestor; `build_cache_summary`'s total/top-consumers/unmapped
  shape, the top-10 cap and sort order, and that it goes through the injected
  `SizeCache` (a second call inside the TTL sees stale data; after
  `invalidate()` it doesn't).
- `test_games.py`: three new tests with real files under a real
  `VAULT_CACHE_ROOT` (a fresh `TestClient`/`Settings` per test, not the shared
  `client` fixture, since the cache root path matters here) — `GET /v1/games`
  reports a real non-null size once depots are on disk; `GET /v1/games/{appid}`
  reports per-depot `size_bytes` (null for a depot that's mapped but not yet
  written) alongside the app total; a depot shared between two apps counts its
  full size into both.
- `test_cache_summary.py`: 401 without a key; an empty cache reports
  `total_bytes: 0`, `top_consumers: []`, `unmapped_depots: {0, 0}`, and a real
  positive `free_disk_bytes`; a seeded cache (two apps, one shared depot, one
  unmapped depot) reports the correct total, per-app top consumers, and the
  unmapped depot's count/bytes.
- `test_prefill_runner.py`: two new tests for `write_selected_apps`'s atomic
  write (carry-over fix) — exactly one file (never a stray `.tmp`) is left in
  `Config/` after a write, and after two successive writes.
- `test_worker.py`: a successful job invalidates the shared `SizeCache` — with
  the *default* 60s TTL (not shortened for the test), so this only passes if
  the worker actively calls `invalidate()`; a passive TTL expiry could never
  complete inside the test's runtime.
- `test_concurrency.py`: `GET /v1/cache/summary` (which takes `SizeCache`'s
  own lock and, on a miss, walks the depot tree) joined both existing hammers
  — the mixed-endpoint gather (60 requests, up from 50) and the
  worker-writing-into-the-same-cache-root-while-HTTP-reads test.
- `test_db.py` / `test_mapping.py` / `test_games.py`: updated, not added —
  `init_db`'s reordered downgrade-guard check (WP 1.5 carry-over fix) doesn't
  change any DB test's observable behavior, so the existing tests continue to
  pin it unchanged; the depot list now always carries `size_bytes` (`null` in
  these fixtures, since none of them write real cache files), so three
  existing assertions were updated to include it.

WP 1.6 additions:

- `test_cache_delete.py` (new, the bulk of this package) — three layers:
  - **Path guards, as pure functions:** empty / whitespace / `None` cache root,
    `"/"` and the platform root, a cache root without a `depot/` directory and
    one where `depot` is a *file*, a UNC-style path, a *relative* cache root
    (the default `./cache` is relative, so this is the normal case), a cache root
    reached through a directory link (the resolved path becomes the deletion
    base), plus 17 parametrized poisoned depot ids (`"../../etc"`, `"441/../.."`,
    `".."`, `"0x1c1"`, `"441; rm -rf /"`, `"C:\Windows"`, `0`, `-5`, `None`,
    `True`, `1.5`, …) each asserted to raise instead of yielding a path, and
    `coerce_positive_id`'s `bool` exclusion (`True == 1` would otherwise become
    "delete depot 1").
  - **Link mechanics against the real filesystem** (no mocks): `shutil.rmtree`
    refusing a linked directory while its target survives, `remove_depot_dir`
    unlinking a link-like depot directory and sparing the target, a link *nested*
    inside a depot tree not being followed, and `depot_dir_bytes` reporting `0`
    for a link (so freed bytes are never overstated). Each of these has a
    **junction-specific** twin (`make_junction`, Windows-only) because
    `make_dir_link` prefers a symlink where it can create one and the two behave
    differently exactly where it matters.
  - **The endpoint end to end:** 401 without a key, 422 for `appid < 1`, 404 for
    an unknown app and for an app with no mappings, 409 while a prefill job is
    queued (and *not* for a job belonging to a different app), the happy path
    (exclusive depots removed, an unrelated unmapped depot and the `depot/` root
    itself untouched, exact freed byte counts), shared-depot protection **in both
    directions**, `status` reset to `idle` with `last_prefill_at` cleared, mapping
    rows surviving, size-cache invalidation asserted with the **default 60 s TTL**
    (so only an explicit `invalidate()` can make it pass), a second `DELETE` as a
    clean no-op, four racing `DELETE`s via `httpx.ASGITransport` (all 200, bytes
    freed exactly once in total, no exception), **partial failure** with one depot
    made undeletable (an open file handle on Windows, a read-only directory mode
    on POSIX — skipped as root) asserting the other depot still deleted and the
    failure reported per depot, an end-to-end symlinked *and* junctioned depot
    directory pointing OUTSIDE the cache root (link gone, foreign data intact,
    `size_bytes_freed: 0`), and both cache-root guard refusals surfacing as `500`
    with nothing deleted. Plus a **real poisoned database row**: a non-numeric
    `depotid` inserted as TEXT (which SQLite's INTEGER affinity permits,
    asserted via `typeof()`) is reported in `failed` with `depotid: 0`, the
    healthy depot is still deleted, and a file that the poisoned value pointed
    at outside the cache root survives.
- Two tests added for the WP 1.6 review findings, both verified to fail against
  the pre-fix code:
  - `test_a_partial_failure_leaves_the_app_in_error_not_done` — an open handle
    on a file in the **middle** of the only mapped depot (so `rmtree` really
    does remove part of the tree before raising) must end at `status='error'`
    with `last_prefill_at` cleared. Against the old rule the app stayed `done`
    with its timestamp intact.
  - `test_a_depot_that_becomes_shared_between_plan_and_removal_survives` — the
    exact TOCTOU interleaving: `deletion.plan_deletion` is wrapped so a second
    app maps depot 900 *after* the (real) plan is computed and *before* any
    removal, with nothing inside the deletion loop patched. The depot must
    survive and appear in `skipped_shared` with the fresh owner list; without
    the recheck it was deleted (50 bytes of another game's content).
  Plus unit tests for the recheck seam itself: a depot kept when `co_owners`
  reports a new owner, a recheck that *raises* leaving the depot alone and
  reporting it in `failed`, `load_co_owners` returning only other apps, and the
  ASCII-digit exactness rule (`" 441 "`, `"1_0"`, `"٤٤١"`, `"²"` all rejected
  even though `int()` accepts the first three).
- `test_sizes.py`: the carry-over fixes are pinned, not just implemented — a
  **real directory symlink** as a depot directory is now visible to
  `scan_depot_signatures` (this test fails against the pre-fix code: the depot is
  absent from the result entirely), a link *inside* the tree is not followed, the
  junction-specific version of that (which is the one that fails without the
  explicit junction check, since `is_dir(follow_symlinks=False)` reports a
  junction as a directory), and `is_link_like` detecting both kinds. The
  `build_cache_summary` tests were updated for its new `SizeSnapshot` parameter.
- `test_concurrency.py`: the mixed hammer grew to 70 parallel requests with
  `DELETE /v1/cache/{appid}` in the mix against a pre-seeded depot tree — several
  of them target the same app ids the concurrent mapping `PUT`s are creating, so
  deletes race each other, race the size-cache scan, and race the mapping writer.
  Assertion unchanged: nothing may 5xx.

Each of the three carry-over fixes and both link guards were verified by
temporarily reverting the fix and re-running the affected tests: the
symlinked-depot test, the junction-walk test and the three link-deletion tests
all fail against the pre-fix code, so they are genuine regression guards rather
than tests that happen to pass.
