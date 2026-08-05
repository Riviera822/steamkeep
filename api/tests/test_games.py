from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY

AUTH = {"X-Api-Key": TEST_API_KEY}


def _seed_mapping(client: TestClient, depotid: int, appid: int, app_name: str | None) -> None:
    response = client.put(
        f"/v1/mapping/{depotid}", json={"appid": appid, "app_name": app_name}, headers=AUTH
    )
    assert response.status_code == 200


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
    # size_bytes lands in WP 1.5 (per-app size calculation) — documented
    # null, not a guessed value.
    assert game["size_bytes"] is None


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
    assert body["depots"] == [{"depotid": 441, "shared": False}]
    assert body["size_bytes"] is None


def test_get_game_detail_flags_shared_depot(client: TestClient) -> None:
    # plan §4 shared-depot semantics: a depot mapped to two tracked apps
    # must be reported as shared on both.
    _seed_mapping(client, depotid=999, appid=440, app_name="Team Fortress 2")
    _seed_mapping(client, depotid=999, appid=730, app_name="Counter-Strike 2")

    tf2_response = client.get("/v1/games/440", headers=AUTH)
    cs2_response = client.get("/v1/games/730", headers=AUTH)

    assert tf2_response.json()["depots"] == [{"depotid": 999, "shared": True}]
    assert cs2_response.json()["depots"] == [{"depotid": 999, "shared": True}]
