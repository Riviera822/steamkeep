from __future__ import annotations

from fastapi.testclient import TestClient

from vault_api.auth import require_api_key
from vault_api.config import Settings
from vault_api.main import create_app

PUBLIC_PATHS = {"/v1/health"}


def _route_has_auth_dependency(route) -> bool:
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False
    return any(dep.call is require_api_key for dep in dependant.dependencies)


def test_docs_and_openapi_are_disabled(client: TestClient) -> None:
    # B1: FastAPI's auto-docs would otherwise expose the full route/schema
    # map without an API key, and Swagger UI's assets come from a CDN
    # anyway (no use on an offline homelab install).
    for path in ("/docs", "/redoc", "/openapi.json"):
        response = client.get(path)
        assert response.status_code == 404, f"{path} should be disabled, got {response.status_code}"


def test_health_is_still_reachable_with_docs_disabled(client: TestClient) -> None:
    response = client.get("/v1/health")
    assert response.status_code == 200


def test_every_route_requires_api_key_except_health(settings: Settings) -> None:
    # B2: auth must be attached at the router level (secure by default), not
    # per-route — walk every registered route and assert require_api_key is
    # present in its dependant, except the one documented public route.
    app = create_app(settings)

    routes_checked = 0
    for route in app.routes:
        path = getattr(route, "path", None)
        if path is None or not hasattr(route, "dependant"):
            continue  # skip non-endpoint routes (e.g. static mounts)

        routes_checked += 1
        has_auth = _route_has_auth_dependency(route)

        if path in PUBLIC_PATHS:
            assert not has_auth, f"{path} is meant to be public but has the auth dependency"
        else:
            assert has_auth, f"{path} is missing the require_api_key dependency"

    # Sanity: make sure this test actually walked routes and isn't vacuously
    # passing (e.g. because app.routes was unexpectedly empty).
    assert routes_checked >= 2
