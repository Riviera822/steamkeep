"""Internal auth-gated scaffold router.

The auth dependency is attached at the APIRouter level (`dependencies=`),
not per-route — this is the secure-by-default pattern every later router
(games, jobs, cache, clients, agent) must follow: a route added to an
auth-gated router is authenticated automatically, it cannot be forgotten.

GET /v1/ping is not part of the public API. It exists only so the test
suite has an authenticated route to exercise before WP 1.3 adds real ones;
it will be removed once real authenticated endpoints exist.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from vault_api.auth import require_api_key

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/v1/ping")
def ping() -> dict[str, str]:
    """Internal auth-dependency scaffold. Not part of the public API surface."""
    return {"status": "pong"}
