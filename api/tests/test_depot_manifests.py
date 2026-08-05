"""vault_api/depot_manifests.py (WP 3.2): the depot_manifests write path."""

from __future__ import annotations

from vault_api.db import get_connection, init_db
from vault_api.depot_manifests import (
    get_depot_manifest,
    list_depot_manifests,
    upsert_depot_manifest,
)


def _conn(tmp_path):
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    return get_connection(db_path)


def test_upsert_then_get_round_trips_all_fields(tmp_path) -> None:
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn,
            appid=440,
            containing_appid=440,
            depotid=441,
            manifestid="123456789",
            chunk_count=3,
            total_bytes=3000,
            recorded_at="2026-08-06T00:00:00Z",
            source="prefill_bin",
        )

        row = get_depot_manifest(conn, appid=440, depotid=441)
    finally:
        conn.close()

    assert row == {
        "appid": 440,
        "containing_appid": 440,
        "depotid": 441,
        "manifestid": "123456789",
        "chunk_count": 3,
        "total_bytes": 3000,
        "recorded_at": "2026-08-06T00:00:00Z",
        "source": "prefill_bin",
    }


def test_get_unknown_pair_returns_none(tmp_path) -> None:
    conn = _conn(tmp_path)
    try:
        assert get_depot_manifest(conn, appid=440, depotid=441) is None
    finally:
        conn.close()


def test_manifestid_stores_a_value_beyond_sqlite_int64_range_as_text(tmp_path) -> None:
    """WP 3.2 schema decision: manifestid is TEXT because Steam manifest ids
    are u64 and SQLite INTEGER storage is signed 64-bit. A real id observed
    during research (3040704736299968944) stays under 2**63-1, but the
    column must not silently break on one that doesn't."""
    huge = str(2**64 - 1)  # far beyond i64's max (2**63 - 1)
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn,
            appid=440,
            containing_appid=None,
            depotid=441,
            manifestid=huge,
            chunk_count=1,
            total_bytes=10,
            recorded_at="2026-08-06T00:00:00Z",
            source="prefill_bin",
        )
        row = get_depot_manifest(conn, appid=440, depotid=441)
    finally:
        conn.close()

    assert row["manifestid"] == huge
    assert isinstance(row["manifestid"], str)


def test_upsert_replaces_the_row_for_the_same_app_and_depot(tmp_path) -> None:
    """ADR-0006 decision 3: latest-per-(appid, depotid), not a history table
    -- a second ingest for the same pair overwrites, never adds a row."""
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=100, recorded_at="2026-08-06T00:00:00Z",
            source="prefill_bin",
        )
        upsert_depot_manifest(
            conn, appid=440, containing_appid=228980, depotid=441, manifestid="2",
            chunk_count=5, total_bytes=500, recorded_at="2026-08-06T01:00:00Z",
            source="cache_manifest",
        )

        rows = list_depot_manifests(conn, appid=440)
    finally:
        conn.close()

    assert len(rows) == 1
    assert rows[0]["manifestid"] == "2"
    assert rows[0]["containing_appid"] == 228980
    assert rows[0]["chunk_count"] == 5
    assert rows[0]["source"] == "cache_manifest"


def test_different_depots_of_the_same_app_are_independent_rows(tmp_path) -> None:
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10, recorded_at="2026-08-06T00:00:00Z",
            source="prefill_bin",
        )
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=442, manifestid="2",
            chunk_count=2, total_bytes=20, recorded_at="2026-08-06T00:00:01Z",
            source="prefill_bin",
        )

        rows = list_depot_manifests(conn, appid=440)
    finally:
        conn.close()

    assert {row["depotid"] for row in rows} == {441, 442}


def test_same_depot_shared_across_two_apps_gets_two_rows(tmp_path) -> None:
    """Latest-per-(appid, depotid) is scoped per APP, not globally per depot
    -- a depot shared between two tracked apps gets one row per app, each
    potentially recording a different manifest state (e.g. one app's
    prefill is more current than the other's)."""
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=900, manifestid="1",
            chunk_count=1, total_bytes=10, recorded_at="2026-08-06T00:00:00Z",
            source="prefill_bin",
        )
        upsert_depot_manifest(
            conn, appid=730, containing_appid=730, depotid=900, manifestid="2",
            chunk_count=2, total_bytes=20, recorded_at="2026-08-06T00:00:01Z",
            source="prefill_bin",
        )

        rows = list_depot_manifests(conn)
    finally:
        conn.close()

    by_app = {row["appid"]: row for row in rows if row["depotid"] == 900}
    assert set(by_app) == {440, 730}
    assert by_app[440]["manifestid"] == "1"
    assert by_app[730]["manifestid"] == "2"


def test_containing_appid_is_nullable(tmp_path) -> None:
    """Cache-stored manifests carry no containing_appid distinction."""
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=None, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10, recorded_at="2026-08-06T00:00:00Z",
            source="cache_manifest",
        )
        row = get_depot_manifest(conn, appid=440, depotid=441)
    finally:
        conn.close()

    assert row["containing_appid"] is None
