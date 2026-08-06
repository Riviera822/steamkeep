from __future__ import annotations

import sqlite3

from vault_api.db import SCHEMA_VERSION, get_connection, init_db

EXPECTED_TABLES = {
    "schema_version",
    "apps",
    "depot_app_map",
    "jobs",
    "agent_reports",
    "depot_manifests",
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


def test_jobs_has_the_queue_indexes(tmp_path) -> None:
    # Schema v2 (WP 1.4): the worker polls "oldest queued job" on every tick
    # and the enqueue path checks "queued/running job for this appid?" on every
    # POST /v1/prefill — both would otherwise scan the whole jobs table.
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'jobs'"
        ).fetchall()
        index_names = {row["name"] for row in rows}
    finally:
        conn.close()

    assert {"idx_jobs_status_id", "idx_jobs_appid_status"} <= index_names


def test_init_db_upgrades_a_v1_database_in_place(tmp_path) -> None:
    """v1 -> v2 is additive, so running the current DDL + bumping the marker is
    the whole migration. Simulate a v1 file by dropping the new indexes and
    resetting the recorded version."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("DROP INDEX idx_jobs_status_id")
        conn.execute("DROP INDEX idx_jobs_appid_status")
        conn.execute("UPDATE schema_version SET version = 1")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)

    conn = get_connection(db_path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        index_names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'jobs'"
            )
        }
    finally:
        conn.close()

    assert version == SCHEMA_VERSION
    assert {"idx_jobs_status_id", "idx_jobs_appid_status"} <= index_names


def test_depot_manifests_has_the_expected_columns_and_depotid_index(tmp_path) -> None:
    # Schema v3 (WP 3.2): last-known manifest state per (appid, depotid);
    # GC (ADR-0007, later) needs "every app's current manifest for a given
    # depot", which the PK (appid, depotid) alone doesn't serve efficiently.
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(depot_manifests)")}
        index_names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'depot_manifests'"
            )
        }
    finally:
        conn.close()

    assert columns == {
        "appid",
        "containing_appid",
        "depotid",
        "manifestid",
        "chunk_count",
        "total_bytes",
        "recorded_at",
        "source",
    }
    assert "idx_depot_manifests_depotid" in index_names


def test_depot_manifests_primary_key_is_appid_depotid(tmp_path) -> None:
    """Latest-per-(appid, depotid) with replace semantics (ADR-0006 decision
    3), not a history table -- a second INSERT for the same pair must be
    rejected by the PK unless it explicitly replaces (see
    vault_api/depot_manifests.py's ON CONFLICT upsert)."""
    import sqlite3

    import pytest

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO depot_manifests
                (appid, containing_appid, depotid, manifestid, chunk_count,
                 total_bytes, recorded_at, source)
            VALUES (440, NULL, 441, '123', 1, 100, '2026-08-06T00:00:00Z', 'prefill_bin')
            """
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO depot_manifests
                    (appid, containing_appid, depotid, manifestid, chunk_count,
                     total_bytes, recorded_at, source)
                VALUES (440, NULL, 441, '999', 2, 200, '2026-08-06T01:00:00Z', 'prefill_bin')
                """
            )
    finally:
        conn.close()


def test_init_db_upgrades_a_v2_database_to_v3_in_place(tmp_path) -> None:
    """v2 -> v3 is additive (one new table + its index), so running the
    current DDL and bumping the marker is the whole migration. Simulate a v2
    file by dropping depot_manifests and resetting the recorded version."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("DROP TABLE depot_manifests")
        conn.execute("UPDATE schema_version SET version = 2")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)

    conn = get_connection(db_path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        table_names = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        conn.close()

    assert version == SCHEMA_VERSION
    assert "depot_manifests" in table_names


def test_init_db_upgrades_a_v3_database_to_v4_in_place(tmp_path) -> None:
    """v3 -> v4 (WP 3.3) adds jobs.updated/up_to_date/summary_parse_ok. Unlike
    every earlier bump, `CREATE TABLE IF NOT EXISTS jobs` is a no-op against
    an existing v1-v3 jobs table (it already exists, just without these
    columns) -- this is the one migration that needs an explicit ALTER,
    which is what this test actually pins. Simulate a v3 file by dropping the
    three new columns and resetting the recorded version; a pre-existing job
    row proves the migration doesn't lose data."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        # Recreate `jobs` in its exact pre-v4 shape (no updated/up_to_date/
        # summary_parse_ok) rather than ALTER ... DROP COLUMN: SQLite's DROP
        # COLUMN rewrites the table's stored CREATE TABLE text and trips over
        # the multi-line `--` comment block _DDL has right before those
        # columns ("incomplete input") -- a real pre-v4 database was never
        # produced by dropping columns from the current DDL, so this isn't
        # a gap in the migration itself, just the right way to simulate one.
        conn.execute("DROP TABLE jobs")
        conn.execute(
            """
            CREATE TABLE jobs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                appid       INTEGER NOT NULL,
                type        TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'idle',
                created_at  TEXT NOT NULL,
                started_at  TEXT,
                finished_at TEXT,
                log_excerpt TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO jobs (id, appid, type, status, created_at) "
            "VALUES (1, 440, 'prefill', 'done', '2026-08-06T00:00:00Z')"
        )
        conn.execute("UPDATE schema_version SET version = 3")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)

    conn = get_connection(db_path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        row = conn.execute("SELECT * FROM jobs WHERE id = 1").fetchone()
    finally:
        conn.close()

    assert version == SCHEMA_VERSION
    assert {"updated", "up_to_date", "summary_parse_ok"} <= columns
    # The pre-existing row survived the ALTER with the new columns NULL —
    # never a silently-guessed 0 (ADR-0006 decision 1's whole point).
    assert row["appid"] == 440
    assert row["updated"] is None
    assert row["up_to_date"] is None
    assert row["summary_parse_ok"] is None


def test_init_db_upgrade_to_v4_is_idempotent_if_called_twice(tmp_path) -> None:
    """Calling init_db twice against the same pre-v4 file must not raise
    'duplicate column name' -- _add_missing_job_columns guards per-column,
    not just per-version."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("ALTER TABLE jobs DROP COLUMN updated")
        conn.execute("UPDATE schema_version SET version = 3")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)
    init_db(db_path)  # must not raise

    conn = get_connection(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    finally:
        conn.close()
    assert "updated" in columns


def test_jobs_has_the_summary_columns(tmp_path) -> None:
    # Schema v4 (WP 3.3): SteamPrefill's own summary-table counters
    # (ADR-0006 decision 1), stored per job.
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    finally:
        conn.close()

    assert {"updated", "up_to_date", "summary_parse_ok"} <= columns


def test_apps_has_the_needs_force_column(tmp_path) -> None:
    # Schema v5 (WP 3.4): ADR-0006 decision 2's per-app flag deciding whether
    # the next prefill must run with --force.
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(apps)")}
    finally:
        conn.close()

    assert "needs_force" in columns


def test_a_fresh_app_row_defaults_needs_force_to_one(tmp_path) -> None:
    # A never-filled app has never had a chance to prove it's current, so its
    # first prefill must be forced (ADR-0006 decision 2).
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("INSERT INTO apps (appid, status) VALUES (440, 'idle')")
        conn.commit()
        row = conn.execute(
            "SELECT needs_force FROM apps WHERE appid = 440"
        ).fetchone()
    finally:
        conn.close()

    assert row["needs_force"] == 1


def test_init_db_upgrades_a_v4_database_to_v5_in_place(tmp_path) -> None:
    """v4 -> v5 (WP 3.4) adds apps.needs_force. Same situation as the v3->v4
    jobs bump: `CREATE TABLE IF NOT EXISTS apps` is a no-op against an
    existing v1-v4 apps table (it already exists, just without this column),
    so this is the one migration that needs an explicit ALTER — pinned here.
    Simulate a v4 file by dropping the column and resetting the recorded
    version; a pre-existing app row proves the migration doesn't lose data."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        # Recreate `apps` in its exact pre-v5 shape (no needs_force) rather
        # than ALTER ... DROP COLUMN, matching the v3->v4 test's own
        # reasoning for jobs (DROP COLUMN trips over _DDL's comment block).
        conn.execute("DROP TABLE apps")
        conn.execute(
            """
            CREATE TABLE apps (
                appid               INTEGER PRIMARY KEY,
                name                TEXT,
                status              TEXT NOT NULL DEFAULT 'idle',
                last_prefill_at     TEXT,
                last_manifest_check TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO apps (appid, name, status, last_prefill_at) "
            "VALUES (440, 'Team Fortress 2', 'done', '2026-08-06T00:00:00Z')"
        )
        conn.execute("UPDATE schema_version SET version = 4")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)

    conn = get_connection(db_path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(apps)")}
        row = conn.execute("SELECT * FROM apps WHERE appid = 440").fetchone()
    finally:
        conn.close()

    assert version == SCHEMA_VERSION
    assert "needs_force" in columns
    # The pre-existing row survived the ALTER, and — because the column has a
    # constant DEFAULT 1, which SQLite applies retroactively to existing rows
    # for ALTER TABLE ADD COLUMN — it is populated, not NULL.
    assert row["name"] == "Team Fortress 2"
    assert row["needs_force"] == 1


def test_init_db_upgrade_to_v5_is_idempotent_if_called_twice(tmp_path) -> None:
    """Calling init_db twice against the same pre-v5 file must not raise
    'duplicate column name' -- _add_missing_app_columns guards per-column,
    not just per-version."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("ALTER TABLE apps DROP COLUMN needs_force")
        conn.execute("UPDATE schema_version SET version = 4")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)
    init_db(db_path)  # must not raise

    conn = get_connection(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(apps)")}
    finally:
        conn.close()
    assert "needs_force" in columns


def test_init_db_refuses_a_database_from_a_newer_vault_api(tmp_path) -> None:
    import pytest

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(RuntimeError, match="newer version"):
        init_db(db_path)


def test_connections_are_thread_confined_by_default(tmp_path) -> None:
    """WP 1.4: check_same_thread must stay ON.

    Handing a connection to another thread is what produced a native access
    violation in WP 1.3's shared-connection design (see vault_api/deps.py).
    Every connection here is opened and closed in the thread that uses it, so
    the safe default must be in force — a future accidental hand-off has to be
    a loud ProgrammingError, not a crash.
    """
    import threading

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    failures: list[str] = []

    def use_from_another_thread() -> None:
        try:
            conn.execute("SELECT 1").fetchone()
            failures.append("no ProgrammingError was raised")
        except sqlite3.ProgrammingError:
            pass

    thread = threading.Thread(target=use_from_another_thread)
    thread.start()
    thread.join(timeout=10)
    conn.close()

    assert not failures, failures
