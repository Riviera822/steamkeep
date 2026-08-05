from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY


def test_ping_without_key_is_rejected(client: TestClient) -> None:
    response = client.get("/v1/ping")
    assert response.status_code == 401


def test_ping_with_wrong_key_is_rejected(client: TestClient) -> None:
    response = client.get("/v1/ping", headers={"X-Api-Key": "wrong-key"})
    assert response.status_code == 401


def test_ping_with_correct_key_succeeds(client: TestClient) -> None:
    response = client.get("/v1/ping", headers={"X-Api-Key": TEST_API_KEY})
    assert response.status_code == 200
    assert response.json() == {"status": "pong"}


def test_unknown_route_with_correct_key_returns_404(client: TestClient) -> None:
    # Proves the auth dependency itself isn't what's producing 404s, and that
    # a nonexistent route behaves like a normal FastAPI app once authenticated.
    response = client.get("/v1/does-not-exist", headers={"X-Api-Key": TEST_API_KEY})
    assert response.status_code == 404
