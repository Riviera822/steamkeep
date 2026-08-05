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
SCHEMA_VERSION = 2

_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS apps (
    appid               INTEGER PRIMARY KEY,
    name                TEXT,
    status              TEXT NOT NULL DEFAULT 'idle',
    last_prefill_at     TEXT,
    last_manifest_check TEXT
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
    log_excerpt TEXT
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
    """
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    conn = get_connection(db_path)
    try:
        conn.executescript(_DDL)

        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )
        elif row["version"] > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database {db_path} has schema version {row['version']}, but this "
                f"vault-api only understands up to {SCHEMA_VERSION}. It was written "
                "by a newer version — upgrade vault-api instead of downgrading."
            )
        elif row["version"] < SCHEMA_VERSION:
            conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))

        conn.commit()
    finally:
        conn.close()
