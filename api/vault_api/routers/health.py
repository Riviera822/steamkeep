"""Health/liveness router — the ONE deliberately unauthenticated router.

GET /v1/health — public, no auth, for external monitoring (plan §6/§10).
No other route may be added to this router; anything else belongs in an
auth-gated router (see routers/internal.py for the pattern).
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/v1/health")
def health() -> dict[str, str]:
    """Liveness check. Returns a fixed body only — no data leak, hence unauthenticated."""
    return {"status": "ok"}
