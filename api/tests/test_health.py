from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY


def test_health_returns_ok_without_key(client: TestClient) -> None:
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_leaks_nothing_beyond_status(client: TestClient) -> None:
    response = client.get("/v1/health")
    assert set(response.json().keys()) == {"status"}
