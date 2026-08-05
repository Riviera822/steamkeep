from __future__ import annotations

import sqlite3

from vault_api.db import SCHEMA_VERSION, get_connection, init_db

EXPECTED_TABLES = {
    "schema_version",
    "apps",
    "depot_app_map",
    "jobs",
    "agent_reports",
}


def test_init_db_creates_expected_tables(tmp_path) -> None:
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        table_names = {row["name"] for row in rows}
    finally:
        conn.close()

    assert EXPECTED_TABLES.issubset(table_names)


def test_init_db_is_idempotent(tmp_path) -> None:
    db_path = str(tmp_path / "vault.db")

    init_db(db_path)
    init_db(db_path)  # must not raise, must not duplicate schema_version rows

    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT version FROM schema_version").fetchall()
    finally:
        conn.close()

    assert len(rows) == 1
    assert rows[0]["version"] == SCHEMA_VERSION


def test_init_db_creates_parent_directory(tmp_path) -> None:
    nested_path = str(tmp_path / "nested" / "dir" / "vault.db")
    init_db(nested_path)

    conn = sqlite3.connect(nested_path)
    conn.close()


def test_depot_app_map_has_appid_index(tmp_path) -> None:
    # plan §4's main lookup direction is appid -> depots; the PK alone
    # (depotid, appid) doesn't serve that direction efficiently.
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'depot_app_map'"
        ).fetchall()
        index_names = {row["name"] for row in rows}
    finally:
        conn.close()

    assert "idx_depot_app_map_appid" in index_names


def test_agent_reports_is_one_row_per_report_snapshot(tmp_path) -> None:
    # ADR-0002: the agent reports its FULL installed-app-ID list every time;
    # vault-api diffs the two most recent rows per client_id. That means one
    # row per report (client_id, reported_at, appids-as-JSON), not one row
    # per (client, app) pair.
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(agent_reports)")}
        assert columns == {"client_id", "reported_at", "appids"}

        conn.execute(
            "INSERT INTO agent_reports (client_id, reported_at, appids) VALUES (?, ?, ?)",
            ("pc-1", "2026-08-05T10:00:00Z", "[440, 730]"),
        )
        conn.commit()

        row = conn.execute(
            "SELECT client_id, reported_at, appids FROM agent_reports WHERE client_id = 'pc-1'"
        ).fetchone()
        assert row["appids"] == "[440, 730]"
    finally:
        conn.close()


def test_agent_reports_has_client_time_index(tmp_path) -> None:
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'agent_reports'"
        ).fetchall()
        index_names = {row["name"] for row in rows}
    finally:
        conn.close()

    assert "idx_agent_reports_client_time" in index_names


def test_connection_pragmas_are_applied(tmp_path) -> None:
    # S2: WAL mode + a busy_timeout so HTTP reads don't fail immediately
    # while a background job-queue writer (WP 1.4+) holds a write lock.
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        (journal_mode,) = conn.execute("PRAGMA journal_mode").fetchone()
        (busy_timeout,) = conn.execute("PRAGMA busy_timeout").fetchone()
    finally:
        conn.close()

    assert journal_mode.lower() == "wal"
    assert busy_timeout == 5000
