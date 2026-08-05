from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.mapping import delete_mapping, upsert_mapping

AUTH = {"X-Api-Key": TEST_API_KEY}


# --- vault_api.mapping.upsert_mapping (DB-level unit tests) ----------------


def test_upsert_mapping_creates_app_row_if_missing(settings: Settings) -> None:
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        upsert_mapping(conn, depotid=441, appid=440, name="Team Fortress 2")

        app_row = conn.execute(
            "SELECT appid, name, status FROM apps WHERE appid = 440"
        ).fetchone()
        assert app_row is not None
        assert app_row["name"] == "Team Fortress 2"
        assert app_row["status"] == "idle"

        map_row = conn.execute(
            "SELECT depotid, appid FROM depot_app_map WHERE depotid = 441 AND appid = 440"
        ).fetchone()
        assert map_row is not None
    finally:
        conn.close()


def test_upsert_mapping_is_idempotent_for_the_same_pair(settings: Settings) -> None:
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        upsert_mapping(conn, depotid=441, appid=440, name="Team Fortress 2")
        upsert_mapping(conn, depotid=441, appid=440, name="Team Fortress 2")  # must not raise or duplicate

        rows = conn.execute(
            "SELECT * FROM depot_app_map WHERE depotid = 441 AND appid = 440"
        ).fetchall()
        assert len(rows) == 1

        (app_row_count,) = conn.execute(
            "SELECT COUNT(*) FROM apps WHERE appid = 440"
        ).fetchone()
        assert app_row_count == 1
    finally:
        conn.close()


def test_upsert_mapping_does_not_overwrite_name_with_none(settings: Settings) -> None:
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        upsert_mapping(conn, depotid=441, appid=440, name="Team Fortress 2")
        upsert_mapping(conn, depotid=442, appid=440, name=None)  # second depot, no name given

        (name,) = conn.execute("SELECT name FROM apps WHERE appid = 440").fetchone()
        assert name == "Team Fortress 2"
    finally:
        conn.close()


def test_upsert_mapping_flags_shared_depot(settings: Settings) -> None:
    # plan §4: a depot mapped to two tracked apps is "shared" and must be
    # skipped on deletion. The mapping layer itself just needs to allow the
    # same depotid under two different appids without conflict.
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        upsert_mapping(conn, depotid=999, appid=440, name="Team Fortress 2")
        upsert_mapping(conn, depotid=999, appid=730, name="Counter-Strike 2")

        rows = conn.execute(
            "SELECT appid FROM depot_app_map WHERE depotid = 999 ORDER BY appid"
        ).fetchall()
        assert {row["appid"] for row in rows} == {440, 730}
    finally:
        conn.close()


# --- HTTP endpoints ---------------------------------------------------------


def test_put_mapping_without_key_is_rejected(client: TestClient) -> None:
    response = client.put("/v1/mapping/441", json={"appid": 440, "app_name": "TF2"})
    assert response.status_code == 401


def test_put_mapping_upserts_and_get_mapping_lists_it(client: TestClient) -> None:
    put_response = client.put(
        "/v1/mapping/441", json={"appid": 440, "app_name": "Team Fortress 2"}, headers=AUTH
    )
    assert put_response.status_code == 200
    assert put_response.json() == {"depotid": 441, "appid": 440}

    get_response = client.get("/v1/mapping", headers=AUTH)
    assert get_response.status_code == 200
    assert {"depotid": 441, "appid": 440} in get_response.json()


def test_put_mapping_without_app_name_is_accepted(client: TestClient) -> None:
    response = client.put("/v1/mapping/442", json={"appid": 440}, headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"depotid": 442, "appid": 440}


def test_get_mapping_without_key_is_rejected(client: TestClient) -> None:
    response = client.get("/v1/mapping")
    assert response.status_code == 401


# --- B2: mapping is additive, not a replace-on-conflict upsert -------------


def test_put_same_depot_different_appids_adds_both_and_flags_shared(
    client: TestClient,
) -> None:
    # Pins the B2 decision (plan §4 conformant): re-PUTing an existing
    # depotid under a different appid ADDS a second mapping row rather than
    # replacing the first one. Both apps must then show shared:true for
    # that depot.
    put1 = client.put(
        "/v1/mapping/999", json={"appid": 440, "app_name": "Team Fortress 2"}, headers=AUTH
    )
    put2 = client.put(
        "/v1/mapping/999", json={"appid": 730, "app_name": "Counter-Strike 2"}, headers=AUTH
    )
    assert put1.status_code == 200
    assert put2.status_code == 200

    mapping_rows = client.get("/v1/mapping", headers=AUTH).json()
    assert {"depotid": 999, "appid": 440} in mapping_rows
    assert {"depotid": 999, "appid": 730} in mapping_rows

    tf2_detail = client.get("/v1/games/440", headers=AUTH).json()
    cs2_detail = client.get("/v1/games/730", headers=AUTH).json()
    assert {"depotid": 999, "shared": True, "size_bytes": None} in tf2_detail["depots"]
    assert {"depotid": 999, "shared": True, "size_bytes": None} in cs2_detail["depots"]


def test_delete_mapping_without_key_is_rejected(client: TestClient) -> None:
    response = client.delete("/v1/mapping/441/440")
    assert response.status_code == 401


def test_delete_mapping_removes_exactly_one_pair(client: TestClient) -> None:
    client.put("/v1/mapping/999", json={"appid": 440, "app_name": "Team Fortress 2"}, headers=AUTH)
    client.put("/v1/mapping/999", json={"appid": 730, "app_name": "Counter-Strike 2"}, headers=AUTH)

    delete_response = client.delete("/v1/mapping/999/440", headers=AUTH)
    assert delete_response.status_code == 204

    mapping_rows = client.get("/v1/mapping", headers=AUTH).json()
    assert {"depotid": 999, "appid": 440} not in mapping_rows
    assert {"depotid": 999, "appid": 730} in mapping_rows  # untouched

    # the app row itself must survive the mapping deletion (B2: this is a
    # mapping correction, not a "forget this game" operation)
    games = client.get("/v1/games", headers=AUTH).json()
    assert any(game["appid"] == 440 for game in games)


def test_delete_mapping_unknown_pair_returns_404(client: TestClient) -> None:
    response = client.delete("/v1/mapping/441/440", headers=AUTH)
    assert response.status_code == 404


# --- should-fixes: validation on the manual fallback endpoint ---------------


def test_put_mapping_rejects_non_positive_depotid(client: TestClient) -> None:
    response = client.put("/v1/mapping/0", json={"appid": 440}, headers=AUTH)
    assert response.status_code == 422


def test_put_mapping_rejects_negative_appid(client: TestClient) -> None:
    response = client.put("/v1/mapping/441", json={"appid": -1}, headers=AUTH)
    assert response.status_code == 422


def test_put_mapping_rejects_unknown_field(client: TestClient) -> None:
    # extra="forbid": a typo'd field name (e.g. "appId" instead of "appid")
    # must fail loudly with 422, not silently upsert with app_name=None.
    response = client.put(
        "/v1/mapping/441", json={"appId": 440, "app_name": "TF2"}, headers=AUTH
    )
    assert response.status_code == 422


def test_delete_mapping_rejects_non_positive_ids(client: TestClient) -> None:
    response = client.delete("/v1/mapping/0/440", headers=AUTH)
    assert response.status_code == 422

    response = client.delete("/v1/mapping/441/0", headers=AUTH)
    assert response.status_code == 422


# --- delete_mapping (DB-level unit test) ------------------------------------


def test_delete_mapping_function_returns_false_when_pair_missing(
    settings: Settings,
) -> None:
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        assert delete_mapping(conn, depotid=441, appid=440) is False

        upsert_mapping(conn, depotid=441, appid=440, name=None)
        assert delete_mapping(conn, depotid=441, appid=440) is True
        assert delete_mapping(conn, depotid=441, appid=440) is False  # already gone
    finally:
        conn.close()


def test_concurrent_upsert_of_the_same_new_appid_does_not_500(tmp_path) -> None:
    """WP 1.4 fix: upsert_mapping's app-row creation must be race-free.

    The old SELECT-then-INSERT made two writers that both saw "no apps row"
    both try to insert; the loser got
    ``sqlite3.IntegrityError: UNIQUE constraint failed: apps.appid`` and the
    request 500'd. Reproduced with parallel threads, so it is pinned here.
    """
    import threading

    from vault_api.db import get_connection, init_db

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    errors: list[BaseException] = []
    start = threading.Barrier(10)

    def upsert(index: int) -> None:
        conn = get_connection(db_path)
        try:
            start.wait(timeout=10)
            for offset in range(10):
                upsert_mapping(
                    conn, depotid=5000 + index * 10 + offset, appid=999, name="Same App"
                )
        except BaseException as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=upsert, args=(index,)) for index in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors

    conn = get_connection(db_path)
    try:
        (app_rows,) = conn.execute("SELECT COUNT(*) FROM apps WHERE appid = 999").fetchone()
        (map_rows,) = conn.execute(
            "SELECT COUNT(*) FROM depot_app_map WHERE appid = 999"
        ).fetchone()
        name = conn.execute("SELECT name FROM apps WHERE appid = 999").fetchone()["name"]
    finally:
        conn.close()

    assert app_rows == 1
    assert map_rows == 100
    assert name == "Same App"
