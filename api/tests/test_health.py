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


def test_health_body_is_byte_for_byte_unchanged_by_wp_4e7(client: TestClient) -> None:
    """WP 4e.7 deliberately does NOT add ``server_version`` (or anything
    else) to ``/v1/health`` -- see ``vault_api/routers/settings.py``'s module
    docstring for why (the one unauthenticated route, a fixed body by
    design, and a version there is free fingerprinting for anything that can
    reach the port). Asserting the raw response TEXT, not just a parsed-JSON
    key set, is deliberate: a future change that adds a field named
    something other than ``server_version`` still trips this, which is the
    point -- this test exists so a future package trips over an assertion
    instead of a review comment (the brief's own words).
    """
    response = client.get("/v1/health")
    assert response.text == '{"status":"ok"}'
