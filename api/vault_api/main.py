"""App factory for vault-api.

Run natively (Windows dev, no Docker until WP 1.9):

    uvicorn vault_api.main:create_app --factory --host 0.0.0.0 --port 8000

The factory pattern keeps env-var validation (Settings.from_env) and DB
initialization out of module-import time, which makes the app trivially
testable with an in-memory/temp-file Settings instance (see api/tests/).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.jobs import recover_stale_jobs
from vault_api.routers import games, health, jobs, mapping
from vault_api.worker import PrefillWorker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start/stop the single prefill job worker (WP 1.4).

    Startup order matters: stale-claim recovery runs BEFORE the worker starts,
    so a job left 'running' by a process that died mid-job is failed while
    nothing can be concurrently claiming it (see ``jobs.recover_stale_jobs``
    for the rule and its single-process caveat).

    The worker is started even when ``VAULT_STEAMPREFILL_PATH`` is unset: the
    missing path is reported per job, not by refusing to boot, so the rest of
    the API stays available on a partially configured host.
    """
    settings: Settings = app.state.settings

    conn = get_connection(settings.db_path)
    try:
        recovered = recover_stale_jobs(conn)
    finally:
        conn.close()
    if recovered:
        logger.warning(
            "Recovered %d job(s) left in 'running' by a previous process; "
            "marked them as 'error'.",
            recovered,
        )

    worker = PrefillWorker(settings)
    app.state.worker = worker
    worker.start()
    try:
        yield
    finally:
        worker.stop()


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
    app = FastAPI(
        title="vault-api", version="0.1.0", openapi_url=None, lifespan=_lifespan
    )
    app.state.settings = settings
    app.include_router(health.router)
    app.include_router(games.router)
    app.include_router(mapping.router)
    app.include_router(jobs.router)
    return app
