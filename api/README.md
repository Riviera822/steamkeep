# vault-api

FastAPI + SQLite backend for SteamVault. Plain `sqlite3` with small helper
functions — no ORM. WP 1.2 shipped the project skeleton (config, DB schema,
auth dependency, `GET /v1/health`). This work package (1.3) adds the first
real authenticated endpoints: depot→app mapping storage and the games
endpoints (`docs/PROJECT_PLAN.md` §4, §6). Prefill orchestration, size
calculation, deletion and the rest of the API table remain scope for later
work packages.

## Layout

```
api/
├── vault_api/
│   ├── main.py           # FastAPI app factory
│   ├── config.py         # Settings, read once from env vars
│   ├── db.py             # SQLite schema v1, idempotent init
│   ├── auth.py           # X-Api-Key dependency (constant-time compare)
│   ├── deps.py           # Shared FastAPI dependencies (get_db)
│   ├── mapping.py         # upsert_mapping() — the depot->app write path
│   └── routers/
│       ├── health.py     # GET /v1/health — the ONE public router
│       ├── games.py      # GET /v1/games, GET /v1/games/{appid}
│       └── mapping.py    # PUT /v1/mapping/{depotid}, GET /v1/mapping
├── tests/                # pytest
├── requirements.txt      # pinned, runtime only
├── requirements-dev.txt  # pinned, adds test-only deps (pytest, httpx)
├── .env.example          # committed template — never commit a real .env
└── pytest.ini
```

## Configuration

Read once at startup from environment variables (`vault_api/config.py`).
Copy `.env.example` to `.env` and adjust:

| Variable            | Required | Default     | Purpose                                    |
|---------------------|----------|-------------|---------------------------------------------|
| `VAULT_API_KEY`     | yes      | *(none)*    | Shared secret for the `X-Api-Key` header    |
| `VAULT_DB_PATH`      | no       | `./vault.db`| SQLite database file                        |
| `VAULT_CACHE_ROOT`   | no       | `./cache`   | Depot cache root (used by later packages)   |
| `VAULT_LOG_LEVEL`    | no       | `INFO`      | Log level                                   |

`VAULT_API_KEY` has no default. Starting the app without it raises
`RuntimeError` immediately (`Settings.from_env`) — this is the "fail loudly"
behavior required by the work package, verified in `tests/test_config.py`.

## Database schema (v1)

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
client for the diff).

**Retention (not yet implemented):** `agent_reports` grows one row per
client per report interval indefinitely. A simple policy — e.g. keep only
the last N reports per `client_id`, pruned on insert or via a periodic job —
is deferred to the work package that implements the diff logic (Phase 2).

No foreign keys enforced between `depot_app_map`/`jobs`/`agent_reports` and
`apps.appid` in v1 — depot mappings and agent reports may arrive before an
app row exists (e.g. first prefill of a new title). Revisit if this becomes
a data-integrity problem in practice.

**Connection pragmas** (`db.py::get_connection`): `journal_mode=WAL` and
`busy_timeout=5000` (ms) on every connection, so HTTP request handlers
reading the database don't immediately fail with "database is locked" while
a background job-queue writer (WP 1.4+) holds a write transaction.

`vault_api/deps.py::get_db` is the FastAPI dependency routers use to get a
per-request connection (opened via `db.get_connection`, closed after the
request). Kept separate from `db.py` so that module stays importable
without FastAPI (plain sqlite3 helpers only).

**`check_same_thread=False` (WP 1.3 review fix, B1 — severe).** FastAPI
runs a sync generator dependency (`deps.get_db`) and the sync endpoint body
that consumes its yielded connection via `run_in_threadpool` — under
concurrency, the two legitimately land in *different* anyio worker threads
within a single request. sqlite3's default `check_same_thread=True` raises
`ProgrammingError` for that (the reviewer measured 60/60 requests failing
with a 500 before this fix, 264/264 succeeding after, via
`httpx.ASGITransport` + `asyncio.gather`). Safe to disable here because
each connection is opened fresh per request and only that one request ever
touches it — never shared across *different* concurrent requests — and
CPython's bundled sqlite3 reports `sqlite3.threadsafety == 3` ("Serialized"
— safe from multiple threads without restriction), asserted at the top of
`get_connection`. Regression test:
`tests/test_concurrency.py::test_concurrent_mixed_requests_do_not_500`.

## Endpoints (WP 1.3)

All routes below require `X-Api-Key` (see "Auth"). Full API table:
`docs/PROJECT_PLAN.md` §6; only the games and mapping rows are implemented
so far.

| Method | Endpoint                          | Purpose |
|--------|-------------------------------------|---------|
| GET    | `/v1/games`                        | All tracked apps: `appid`, `name`, `status`, `last_prefill_at`, `depot_count`, `size_bytes` (always `null` until WP 1.5 adds the per-app size calculation) |
| GET    | `/v1/games/{appid}`                | Detail for one app: same fields plus `depots` (list of `{depotid, shared}`); `404` for an unknown `appid` |
| PUT    | `/v1/mapping/{depotid}`            | Body `{"appid": int, "app_name": str \| null}` — **additively** upsert one depot→app mapping fact (manual fallback, see below); `422` for `depotid <= 0`, `appid <= 0`, or an unrecognized body field |
| GET    | `/v1/mapping`                      | Full depot→app mapping table: list of `{depotid, appid}` |
| DELETE | `/v1/mapping/{depotid}/{appid}`    | Remove one mapping pair (correction path for the additive `PUT`, see below); `204` on success, `404` if the pair doesn't exist, `422` for non-positive ids |

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
`routers/mapping.py`, and future ones — jobs, cache, clients, agent) is
constructed as `APIRouter(dependencies=[Depends(require_api_key)])`, so a
route added to it is authenticated automatically and can't be forgotten.
`tests/test_security.py` enforces this by walking `app.routes` and
asserting the dependency is present everywhere except `/v1/health`.

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

## Tests

```powershell
cd api
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

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
