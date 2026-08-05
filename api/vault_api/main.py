"""App factory for vault-api.

Run natively (Windows dev, no Docker until WP 1.9):

    uvicorn vault_api.main:create_app --factory --host 0.0.0.0 --port 8000

The factory pattern keeps env-var validation (Settings.from_env) and DB
initialization out of module-import time, which makes the app trivially
testable with an in-memory/temp-file Settings instance (see api/tests/).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from vault_api.config import Settings
from vault_api.db import init_db
from vault_api.routers import games, health, mapping


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app. Pass `settings` explicitly in tests; omit it to read from env."""
    settings = settings or Settings.from_env()

    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

    init_db(settings.db_path)

    # openapi_url=None also disables the derived /docs and /redoc routes.
    # Plan §9 ("no unauthenticated endpoints") only carves out /v1/health;
    # Swagger/ReDoc would otherwise expose the full route/schema map without
    # a key, and Swagger UI loads its assets from a CDN anyway (won't work
    # offline on an isolated homelab install).
    app = FastAPI(title="vault-api", version="0.1.0", openapi_url=None)
    app.state.settings = settings
    app.include_router(health.router)
    app.include_router(games.router)
    app.include_router(mapping.router)
    return app
