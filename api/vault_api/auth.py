"""API-key authentication dependency.

Every endpoint requires the ``X-Api-Key`` header to match ``VAULT_API_KEY``
— EXCEPT ``GET /v1/health``, which is deliberately left unauthenticated
(see api/README.md, section "Auth exception", for the documented rationale
against plan §9's "no unauthenticated endpoints" stance).
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request, status


def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    """FastAPI dependency: raise 401 unless X-Api-Key matches the configured key.

    Uses a constant-time comparison (hmac.compare_digest) so response timing
    cannot be used to guess the key byte by byte.
    """
    settings = request.app.state.settings
    if not x_api_key or not hmac.compare_digest(x_api_key, settings.vault_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-Api-Key header",
        )
