"""GET /v1/cache/summary (plan §6, WP 1.5)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api.config import Settings
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}


def _write(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _seed_mapping(client: TestClient, depotid: int, appid: int, app_name: str | None) -> None:
    response = client.put(
        f"/v1/mapping/{depotid}", json={"appid": appid, "app_name": app_name}, headers=AUTH
    )
    assert response.status_code == 200


def test_cache_summary_without_key_is_rejected(client: TestClient) -> None:
    response = client.get("/v1/cache/summary")
    assert response.status_code == 401


def test_cache_summary_on_an_empty_cache(client: TestClient) -> None:
    response = client.get("/v1/cache/summary", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["total_bytes"] == 0
    assert body["top_consumers"] == []
    assert body["unmapped_depots"] == {"count": 0, "size_bytes": 0}
    assert isinstance(body["free_disk_bytes"], int)
    assert body["free_disk_bytes"] > 0


def test_cache_summary_reports_totals_top_consumers_and_unmapped(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "cache"
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(cache_root),
        log_level="INFO",
    )
    app = create_app(settings)
    client = TestClient(app)

    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")
    _seed_mapping(client, depotid=900, appid=440, app_name="Team Fortress 2")
    _seed_mapping(client, depotid=900, appid=730, app_name="Counter-Strike 2")

    _write(cache_root / "depot" / "441" / "chunk" / "a", b"1" * 10)
    _write(cache_root / "depot" / "900" / "chunk" / "a", b"1" * 50)
    _write(cache_root / "depot" / "555" / "chunk" / "a", b"1" * 5)  # unmapped

    body = client.get("/v1/cache/summary", headers=AUTH).json()

    assert body["total_bytes"] == 65  # 10 + 50 + 5, each counted once
    consumers = {c["appid"]: c for c in body["top_consumers"]}
    assert consumers[440] == {"appid": 440, "name": "Team Fortress 2", "size_bytes": 60}
    assert consumers[730] == {"appid": 730, "name": "Counter-Strike 2", "size_bytes": 50}
    assert body["unmapped_depots"] == {"count": 1, "size_bytes": 5}
