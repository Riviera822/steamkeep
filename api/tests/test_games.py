from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api.config import Settings
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}


def _seed_mapping(client: TestClient, depotid: int, appid: int, app_name: str | None) -> None:
    response = client.put(
        f"/v1/mapping/{depotid}", json={"appid": appid, "app_name": app_name}, headers=AUTH
    )
    assert response.status_code == 200


def _write(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_list_games_without_key_is_rejected(client: TestClient) -> None:
    response = client.get("/v1/games")
    assert response.status_code == 401


def test_list_games_is_empty_when_no_apps_tracked(client: TestClient) -> None:
    response = client.get("/v1/games", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == []


def test_list_games_reports_depot_count_and_null_size(client: TestClient) -> None:
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")
    _seed_mapping(client, depotid=442, appid=440, app_name=None)

    response = client.get("/v1/games", headers=AUTH)
    assert response.status_code == 200
    games = response.json()
    assert len(games) == 1
    game = games[0]
    assert game["appid"] == 440
    assert game["name"] == "Team Fortress 2"
    assert game["status"] == "idle"
    assert game["depot_count"] == 2
    # Mapped, but nothing has ever been written to disk for this app yet
    # (VAULT_CACHE_ROOT doesn't even exist) -> "uncached", reported as null,
    # not a guessed 0 (WP 1.5, vault_api.sizes.app_size_bytes).
    assert game["size_bytes"] is None


def test_list_games_exposes_needs_force_defaulting_true(client: TestClient) -> None:
    # Schema v5 default (WP 3.4, ADR-0006 decision 2): a never-filled app
    # needs its first prefill forced.
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")

    game = client.get("/v1/games", headers=AUTH).json()[0]
    assert game["needs_force"] is True


def test_get_game_detail_exposes_needs_force(client: TestClient) -> None:
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")

    body = client.get("/v1/games/440", headers=AUTH).json()
    assert body["needs_force"] is True


# -- last_manifest_check (WP 4c: surfacing apps.last_manifest_check) --------
#
# Write-path semantics (worker.py, pinned end-to-end in test_worker.py) are
# NOT this file's concern -- these tests only check that GameSummary/
# GameDetail expose whatever is already in the apps row, with the same
# null-when-never-set behavior last_prefill_at already has.


def test_list_games_reports_null_last_manifest_check_for_a_never_checked_app(
    client: TestClient,
) -> None:
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")

    game = client.get("/v1/games", headers=AUTH).json()[0]
    assert game["last_manifest_check"] is None


def test_get_game_detail_reports_null_last_manifest_check_for_a_never_checked_app(
    client: TestClient,
) -> None:
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")

    body = client.get("/v1/games/440", headers=AUTH).json()
    assert body["last_manifest_check"] is None


def test_list_games_round_trips_the_exact_last_manifest_check_value(
    client: TestClient, settings: Settings
) -> None:
    """Pin against timezone/format mangling: what is written to the DB
    column is exactly what the API returns, byte for byte -- no re-parsing
    through a datetime type that could normalize/shift it."""
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")
    from vault_api.db import get_connection

    # A deliberately odd-looking but well-formed stamp (not "now") so a bug
    # that silently substituted the current time, or ran the value through
    # something that re-normalizes/shifts it, would be caught.
    stamp = "2026-08-06T13:37:05Z"
    conn = get_connection(settings.db_path)
    try:
        conn.execute(
            "UPDATE apps SET last_manifest_check = ? WHERE appid = 440", (stamp,)
        )
        conn.commit()
    finally:
        conn.close()

    game = client.get("/v1/games", headers=AUTH).json()[0]
    detail = client.get("/v1/games/440", headers=AUTH).json()
    assert game["last_manifest_check"] == stamp
    assert detail["last_manifest_check"] == stamp


def test_get_game_without_key_is_rejected(client: TestClient) -> None:
    response = client.get("/v1/games/440")
    assert response.status_code == 401


def test_get_game_returns_404_for_unknown_appid(client: TestClient) -> None:
    response = client.get("/v1/games/999999", headers=AUTH)
    assert response.status_code == 404


def test_get_game_detail_lists_depots_not_shared(client: TestClient) -> None:
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")

    response = client.get("/v1/games/440", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["appid"] == 440
    assert body["name"] == "Team Fortress 2"
    assert body["depots"] == [{"depotid": 441, "shared": False, "size_bytes": None}]
    assert body["size_bytes"] is None


def test_get_game_detail_flags_shared_depot(client: TestClient) -> None:
    # plan §4 shared-depot semantics: a depot mapped to two tracked apps
    # must be reported as shared on both.
    _seed_mapping(client, depotid=999, appid=440, app_name="Team Fortress 2")
    _seed_mapping(client, depotid=999, appid=730, app_name="Counter-Strike 2")

    tf2_response = client.get("/v1/games/440", headers=AUTH)
    cs2_response = client.get("/v1/games/730", headers=AUTH)

    assert tf2_response.json()["depots"] == [
        {"depotid": 999, "shared": True, "size_bytes": None}
    ]
    assert cs2_response.json()["depots"] == [
        {"depotid": 999, "shared": True, "size_bytes": None}
    ]


# -- size wiring (WP 1.5): real files under VAULT_CACHE_ROOT ----------------


def _client_with_cache_root(tmp_path: Path) -> tuple[TestClient, Path]:
    cache_root = tmp_path / "cache"
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(cache_root),
        log_level="INFO",
    )
    return TestClient(create_app(settings)), cache_root


def test_list_games_reports_a_real_size_once_depots_are_on_disk(tmp_path: Path) -> None:
    client, cache_root = _client_with_cache_root(tmp_path)
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")
    _seed_mapping(client, depotid=442, appid=440, app_name=None)
    _write(cache_root / "depot" / "441" / "chunk" / "a", b"1" * 10)
    _write(cache_root / "depot" / "442" / "chunk" / "a", b"1" * 5)

    game = client.get("/v1/games", headers=AUTH).json()[0]

    assert game["size_bytes"] == 15


def test_get_game_detail_reports_per_depot_and_total_size(tmp_path: Path) -> None:
    client, cache_root = _client_with_cache_root(tmp_path)
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")
    _seed_mapping(client, depotid=442, appid=440, app_name=None)
    _write(cache_root / "depot" / "441" / "chunk" / "a", b"1" * 10)
    # 442 mapped but nothing written for it yet -> its own size_bytes is
    # null, while the app total still reports the 441 bytes it DOES have.

    body = client.get("/v1/games/440", headers=AUTH).json()

    depots_by_id = {d["depotid"]: d for d in body["depots"]}
    assert depots_by_id[441]["size_bytes"] == 10
    assert depots_by_id[442]["size_bytes"] is None
    assert body["size_bytes"] == 10


def test_shared_depot_counts_fully_into_both_apps(tmp_path: Path) -> None:
    client, cache_root = _client_with_cache_root(tmp_path)
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")
    _seed_mapping(client, depotid=900, appid=440, app_name="Team Fortress 2")
    _seed_mapping(client, depotid=900, appid=730, app_name="Counter-Strike 2")
    _write(cache_root / "depot" / "441" / "chunk" / "a", b"1" * 10)
    _write(cache_root / "depot" / "900" / "chunk" / "a", b"1" * 50)

    tf2 = client.get("/v1/games/440", headers=AUTH).json()
    cs2 = client.get("/v1/games/730", headers=AUTH).json()

    # Shared depot 900's 50 bytes counted fully into BOTH apps: per-app sizes
    # (60 + 50 = 110) sum to more than the 60 bytes actually on disk — see
    # vault_api/sizes.py::app_size_bytes and GET /v1/cache/summary's
    # total_bytes for the "counted once" figure.
    assert tf2["size_bytes"] == 60
    assert cs2["size_bytes"] == 50
