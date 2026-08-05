"""SQLite schema (v1) and connection helpers — plain sqlite3, no ORM.

Schema is created idempotently: ``init_db`` uses ``CREATE TABLE IF NOT
EXISTS`` and only seeds ``schema_version`` once, so calling it repeatedly
(e.g. on every app startup) is safe and cheap.

See api/README.md for a description of each table's purpose.
"""

from __future__ import annotations

import os
import sqlite3

SCHEMA_VERSION = 1

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
    database while a background job-queue writer (WP 1.4+) holds a write
    transaction, instead of failing immediately with "database is locked".
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(db_path: str) -> None:
    """Create the schema if it doesn't exist yet. Idempotent — safe to call on every startup."""
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    conn = get_connection(db_path)
    try:
        conn.executescript(_DDL)
        (row_count,) = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()
        if row_count == 0:
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )
        conn.commit()
    finally:
        conn.close()
