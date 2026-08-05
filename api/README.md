# vault-api

FastAPI + SQLite backend for SteamVault. Plain `sqlite3` with small helper
functions — no ORM. This work package (1.2) ships only the project
skeleton: config, DB schema, auth dependency, and a single public endpoint
(`GET /v1/health`). Everything else (`/v1/games`, `/v1/prefill`, ...) is
scope for WP 1.3+ (see `docs/PROJECT_PLAN.md` §6).

## Layout

```
api/
├── vault_api/
│   ├── main.py           # FastAPI app factory
│   ├── config.py         # Settings, read once from env vars
│   ├── db.py             # SQLite schema v1, idempotent init
│   ├── auth.py           # X-Api-Key dependency (constant-time compare)
│   └── routers/
│       ├── health.py     # GET /v1/health — the ONE public router
│       └── internal.py   # GET /v1/ping — auth attached at router level
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

## Auth

Every endpoint requires the header `X-Api-Key: <VAULT_API_KEY>`, checked
with a constant-time comparison (`hmac.compare_digest`) in the
`require_api_key` FastAPI dependency (`vault_api/auth.py`). Missing or
wrong key → `401`.

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
`require_api_key` dependency; every other router (starting with
`routers/internal.py`) is constructed as
`APIRouter(dependencies=[Depends(require_api_key)])`, so a route added to
it is authenticated automatically and can't be forgotten. This is the
pattern every later router (games, jobs, cache, clients, agent) must
follow. `tests/test_security.py` enforces this by walking `app.routes` and
asserting the dependency is present everywhere except `/v1/health`.

`GET /v1/ping` (in `routers/internal.py`) is **not** part of the public
API. It exists solely as an authenticated route for the test suite to
exercise `require_api_key` against, since WP 1.2 ships no other
authenticated endpoint yet. It will be removed once WP 1.3 adds real
authenticated routes.

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

curl http://localhost:8000/v1/ping
# 401 - missing X-Api-Key

curl -H "X-Api-Key: <your key>" http://localhost:8000/v1/ping
# {"status":"pong"}

curl http://localhost:8000/docs
curl http://localhost:8000/openapi.json
# both 404 - disabled
```

## Tests

```powershell
cd api
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

Covers: health returns `ok` without a key and leaks nothing else; `/v1/ping`
returns 401 without/with-wrong key and 200 with the correct key (plus a 404
scaffold check for an unknown route); `/docs`, `/redoc`, `/openapi.json` all
return 404; every registered route requires `require_api_key` except
`/v1/health` (route-walk regression test); schema creation is idempotent
(tables exist, `schema_version` has exactly one row after calling `init_db`
twice, expected indexes and the reshaped `agent_reports` columns are
present, WAL + busy_timeout pragmas are active); missing/blank
`VAULT_API_KEY` fails loudly via `Settings.from_env`.
