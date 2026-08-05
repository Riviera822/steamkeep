from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY


def test_request_without_key_is_rejected(client: TestClient) -> None:
    response = client.get("/v1/games")
    assert response.status_code == 401


def test_request_with_wrong_key_is_rejected(client: TestClient) -> None:
    response = client.get("/v1/games", headers={"X-Api-Key": "wrong-key"})
    assert response.status_code == 401


def test_request_with_correct_key_succeeds(client: TestClient) -> None:
    response = client.get("/v1/games", headers={"X-Api-Key": TEST_API_KEY})
    assert response.status_code == 200


def test_unknown_route_with_correct_key_returns_404(client: TestClient) -> None:
    # Proves the auth dependency itself isn't what's producing 404s, and that
    # a nonexistent route behaves like a normal FastAPI app once authenticated.
    response = client.get("/v1/does-not-exist", headers={"X-Api-Key": TEST_API_KEY})
    assert response.status_code == 404


def test_non_ascii_key_is_rejected_with_401_not_500(client: TestClient) -> None:
    # Carry-over fix from the WP 1.2 review: hmac.compare_digest raises
    # TypeError for a non-ASCII `str` (confirmed empirically against this
    # repo's require_api_key before the fix: "comparing strings with
    # non-ASCII characters is not supported"), which FastAPI/Starlette
    # turned into an unhandled 500 since nothing caught it. A wrong key —
    # ASCII or not — must produce a 401, never a crash.
    #
    # HTTP header values are opaque bytes on the wire. httpx/TestClient
    # refuse to send a non-ASCII `str` header at all (raises
    # UnicodeEncodeError client-side, before any request is made), so the
    # raw UTF-8 bytes are passed directly via httpx.Headers — this is what
    # actually reaches the server for a client that sent a non-ASCII key.
    headers = httpx.Headers([(b"x-api-key", "café-schlüssel".encode("utf-8"))])
    response = client.get("/v1/games", headers=headers)
    assert response.status_code == 401
