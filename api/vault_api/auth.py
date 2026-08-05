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

    ``hmac.compare_digest`` only accepts ASCII-only ``str`` arguments — it
    raises ``TypeError`` for a non-ASCII ``str`` (confirmed empirically: a
    key containing e.g. "café" raised ``TypeError: comparing strings with
    non-ASCII characters is not supported``, which FastAPI/Starlette turns
    into an unhandled 500 since this dependency didn't catch it). Comparing
    UTF-8-encoded *bytes* instead sidesteps the ASCII restriction entirely —
    ``compare_digest`` has no charset restriction on ``bytes`` — while still
    being constant-time. ``surrogateescape`` additionally guards against a
    ``UnicodeEncodeError`` if a client header ever contains lone surrogate
    code points (e.g. from an ASGI server that latin-1-decoded raw header
    bytes containing an invalid sequence).
    """
    settings = request.app.state.settings

    if x_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-Api-Key header",
        )

    try:
        provided = x_api_key.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        provided = None

    expected = settings.vault_api_key.encode("utf-8", "surrogateescape")

    if provided is None or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-Api-Key header",
        )
