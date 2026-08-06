"""SQLite schema (v1) and connection helpers — plain sqlite3, no ORM.

Schema is created idempotently: ``init_db`` uses ``CREATE TABLE IF NOT
EXISTS`` and only seeds ``schema_version`` once, so calling it repeatedly
(e.g. on every app startup) is safe and cheap.

See api/README.md for a description of each table's purpose.
"""

from __future__ import annotations

import os
import sqlite3

#: v1 (WP 1.2): initial schema.
#: v2 (WP 1.4): added the two ``jobs`` indexes below. Purely additive
#: (``CREATE INDEX IF NOT EXISTS``), so upgrading a v1 file is just running
#: the current DDL and recording the new version — see ``init_db``.
#: v3 (WP 3.2): added ``depot_manifests`` (ADR-0006 decision 3: last-known
#: manifest state per (appid, depotid), recorded from SteamPrefill's
#: temp-cache filenames) plus its ``depotid`` index. Also purely additive.
#: v4 (WP 3.3): added ``jobs.updated``, ``jobs.up_to_date``,
#: ``jobs.summary_parse_ok`` — SteamPrefill's own summary-table counters
#: (ADR-0006 decision 1), stored per job so the API can expose them and job
#: outcome honesty (worker.py) can be audited after the fact. Additive, but
#: NOT expressible as ``CREATE TABLE IF NOT EXISTS`` alone: that statement is
#: a no-op against an *existing* ``jobs`` table from v1-v3, which lacks these
#: columns, so ``init_db`` runs an explicit ``ALTER TABLE ... ADD COLUMN``
#: step for pre-v4 databases (see ``_add_missing_job_columns``) — a fresh
#: install gets them straight from ``_DDL``'s ``CREATE TABLE``.
#: v5 (WP 3.4): added ``apps.needs_force`` — ADR-0006 decision 2's per-app
#: flag deciding whether the next prefill for this app must run with
#: ``--force``. ``DEFAULT 1`` on the column itself encodes "a fresh app row
#: has never been filled, so its first run needs --force" without any
#: application code having to special-case a never-seen app. Same
#: not-expressible-as-``CREATE TABLE IF NOT EXISTS`` situation as v4 (an
#: existing pre-v5 ``apps`` table lacks the column), so this needs its own
#: explicit ``ALTER TABLE ... ADD COLUMN`` step too (see
#: ``_add_missing_app_columns``).
SCHEMA_VERSION = 5

_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS apps (
    appid               INTEGER PRIMARY KEY,
    name                TEXT,
    status              TEXT NOT NULL DEFAULT 'idle',
    last_prefill_at     TEXT,
    last_manifest_check TEXT,
    -- v5 (WP 3.4): ADR-0006 decision 2. 1 = the next prefill for this app
    -- must run with --force; 0 = a non-forced run is enough (SteamPrefill's
    -- own up-to-date bookkeeping is trusted). DEFAULT 1 because a brand-new
    -- app row has never been filled -- see worker.py/deletion.py for who
    -- flips it and README.md's "needs_force lifecycle" table for the full
    -- state machine.
    needs_force         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS depot_app_map (
    depotid INTEGER NOT NULL,
    appid   INTEGER NOT NULL,
    PRIMARY KEY (depotid, appid)
);

-- plan §4's main lookup direction is appid -> depots (delete/size a game by
-- its depot list); the PK above only indexes depotid-first.
CREATE INDEX IF NOT EXISTS idx_depot_app_map_appid ON depot_app_map (appid);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    appid       INTEGER NOT NULL,
    type        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'idle',
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    log_excerpt TEXT,
    -- v4 (WP 3.3): SteamPrefill's own summary-table counters (ADR-0006
    -- decision 1). NULL for every job type/outcome that never reaches a
    -- parsed summary (GC jobs, a job that never finished, a finished job
    -- whose summary could not be parsed). summary_parse_ok is 0/1, not a
    -- real SQLite boolean (there isn't one) -- NULL means "not applicable",
    -- 0 means "parse failed", 1 means "parsed".
    updated           INTEGER,
    up_to_date        INTEGER,
    summary_parse_ok  INTEGER
);

-- The job worker polls "give me the oldest queued job" on every tick and the
-- enqueue path asks "does this app already have a queued/running job?" on
-- every POST /v1/prefill. Both would otherwise scan the whole (append-only,
-- ever-growing) jobs table. Schema v2, WP 1.4.
CREATE INDEX IF NOT EXISTS idx_jobs_status_id ON jobs (status, id);
CREATE INDEX IF NOT EXISTS idx_jobs_appid_status ON jobs (appid, status);

-- One row per report (full-list snapshot), matching ADR-0002 literally:
-- the agent reports its complete installed-app-ID list every time; vault-api
-- derives additions/removals by diffing the two most recent rows per
-- client_id. appids is a JSON array of ints, e.g. "[440, 730]".
CREATE TABLE IF NOT EXISTS agent_reports (
    client_id   TEXT NOT NULL,
    reported_at TEXT NOT NULL,
    appids      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_reports_client_time
    ON agent_reports (client_id, reported_at DESC);

-- WP 3.2 / ADR-0006 decision 3: LATEST manifest state per (appid, depotid),
-- not a history table -- GC (ADR-0007) and the future staleness comparison
-- only ever need "what does vault-api currently believe this app's depot
-- manifest is", so a newer ingest for the same pair REPLACES the row
-- (vault_api/depot_manifests.py's upsert) rather than adding a new one.
--
-- appid: the app this row is attributed to for THIS mapping (usually the
-- job's own appid, i.e. the .bin filename's originalAppId).
-- containing_appid: the .bin filename's containingAppId -- the shared-depot
-- signal (research doc: e.g. 107100_228980_229002_...bin = app 107100
-- pulling a depot that "belongs" to app 228980). NULL for a cache-stored
-- manifest, which carries no such distinction.
-- manifestid is TEXT, not INTEGER, on purpose: SQLite's INTEGER storage is a
-- signed 64-bit value, and Steam manifest ids are u64 -- a real id CAN
-- exceed 2^63-1 even though every id observed during this project's research
-- (e.g. 3040704736299968944, ~3e18) stayed under it. TEXT never overflows
-- and this column is never used for arithmetic, only equality/lookup.
CREATE TABLE IF NOT EXISTS depot_manifests (
    appid            INTEGER NOT NULL,
    containing_appid INTEGER,
    depotid          INTEGER NOT NULL,
    manifestid       TEXT NOT NULL,
    chunk_count      INTEGER NOT NULL,
    total_bytes      INTEGER NOT NULL,
    recorded_at      TEXT NOT NULL,
    source           TEXT NOT NULL,
    PRIMARY KEY (appid, depotid)
);

-- Future GC (ADR-0007) needs "every app's current manifest for this depot"
-- (the shared-depot keep-set is a UNION across apps) -- the PK above only
-- indexes appid-first.
CREATE INDEX IF NOT EXISTS idx_depot_manifests_depotid ON depot_manifests (depotid);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open a new connection with sane defaults (row access by column name).

    WAL journal mode + a busy_timeout let HTTP request handlers read the
    database while the background job worker holds a write transaction,
    instead of failing immediately with "database is locked".

    ``check_same_thread`` is left at its safe default of ``True`` (WP 1.4;
    WP 1.3 had set it to ``False``). Every connection in this codebase is now
    thread-confined by construction — request handlers open and close theirs
    inside the endpoint body (``deps.db_opener``) and the job worker owns one
    connection created inside its own thread — so the default costs nothing
    and turns a future accidental hand-off into a loud ``ProgrammingError``
    instead of a native crash. See ``deps.py`` for the measured access
    violation that made the old shared-connection approach untenable.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(db_path: str) -> None:
    """Create/upgrade the schema. Idempotent — safe to call on every startup.

    Migration story (deliberately minimal, plan §9 "keep it simple"): every
    statement in ``_DDL`` is ``CREATE ... IF NOT EXISTS``, so running the
    current DDL against an older file brings it to the current shape as long
    as all changes so far have been *additive*. That holds for v1 -> v2 (two
    new ``jobs`` indexes), so the "migration" is just recording the new
    version number afterwards.

    The day a change is NOT additive (dropping/retyping a column), this
    function needs a real per-version step list — the ``stored > SCHEMA_VERSION``
    guard below exists so a downgrade is caught loudly instead of silently
    operating on a schema this code doesn't understand.

    **Ordering (WP 1.5 carry-over fix from the WP 1.4 review):** the stored
    version is read and checked BEFORE the rest of ``_DDL`` runs, not after.
    The previous ordering ran the full DDL unconditionally and only checked
    the version afterwards — harmless so far because every statement so far
    has been additive, but it read as a downgrade *gate* without being one:
    a future non-additive statement would already have executed against a
    newer-than-understood schema by the time the guard fired. Only the
    ``schema_version`` table itself (a single, always-safe ``CREATE TABLE IF
    NOT EXISTS``) runs before the check.
    """
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    conn = get_connection(db_path)
    try:
        # Only the version marker table — cheap and always additive — before
        # the guard runs. The rest of _DDL (which also (re)creates this same
        # table, harmlessly, via IF NOT EXISTS) follows only once we know this
        # code understands the stored version.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is not None and row["version"] > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database {db_path} has schema version {row['version']}, but this "
                f"vault-api only understands up to {SCHEMA_VERSION}. It was written "
                "by a newer version — upgrade vault-api instead of downgrading."
            )

        # v4 (WP 3.3): an existing pre-v4 database already has a `jobs` table
        # without the new columns, so `CREATE TABLE IF NOT EXISTS` below is a
        # no-op for it — unlike the v2/v3 bumps, this one needs an explicit
        # ALTER. A brand-new database (row is None) skips this: _DDL's CREATE
        # TABLE already includes the columns directly. Runs before
        # executescript so the two migration mechanisms (ALTER here, CREATE
        # ... IF NOT EXISTS below) never depend on ordering relative to each
        # other.
        if row is not None and row["version"] < 4:
            _add_missing_job_columns(conn)

        # v5 (WP 3.4): same situation as the v4 step above -- an existing
        # pre-v5 `apps` table already exists, so `CREATE TABLE IF NOT EXISTS`
        # below is a no-op for it and needs_force needs an explicit ALTER.
        if row is not None and row["version"] < 5:
            _add_missing_app_columns(conn)

        conn.executescript(_DDL)

        if row is None:
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )
        elif row["version"] < SCHEMA_VERSION:
            conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))

        conn.commit()
    finally:
        conn.close()


def _add_missing_job_columns(conn: sqlite3.Connection) -> None:
    """v1->v4 migration step: add ``jobs.updated``/``up_to_date``/``summary_parse_ok``.

    Guarded per-column via ``PRAGMA table_info`` (not just per-version) so
    calling ``init_db`` twice against the same pre-v4 file — or against a file
    some *other* future migration already partially touched — never raises
    ``duplicate column name`` instead of silently doing nothing on the second
    call, matching the idempotency the rest of ``init_db`` already promises.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    for column in ("updated", "up_to_date", "summary_parse_ok"):
        if column not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} INTEGER")


def _add_missing_app_columns(conn: sqlite3.Connection) -> None:
    """v1->v5 migration step: add ``apps.needs_force`` (ADR-0006 decision 2).

    Same reasoning as ``_add_missing_job_columns``: guarded via ``PRAGMA
    table_info`` rather than only the version check, so calling ``init_db``
    twice against the same pre-v5 file never raises ``duplicate column
    name``. ``DEFAULT 1`` on the ``ALTER TABLE`` is deliberate and matches
    ``_DDL``'s own column default exactly: every pre-existing app row already
    represents "has been prefilled before" from vault-api's point of view,
    but this migration has no way to know whether that prior prefill ran with
    or without ``--force`` (schema versions before this one always passed
    ``--force`` unconditionally — see ``vault_api/prefill.py``). Starting
    every pre-existing app at ``needs_force=1`` costs at most one redundant
    forced run per app after the upgrade (cheap: disk-speed re-requests, not
    bandwidth, per the same ADR-0001 measurement `--force` always relied on),
    which is a safe default to err on compared to silently trusting
    SteamPrefill's own stale bookkeeping for an app this code has no history
    for.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(apps)")}
    if "needs_force" not in existing:
        conn.execute(
            "ALTER TABLE apps ADD COLUMN needs_force INTEGER NOT NULL DEFAULT 1"
        )
