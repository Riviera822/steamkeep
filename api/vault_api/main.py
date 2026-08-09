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
from vault_api.manifest_ingest import log_cache_dir_canary
from vault_api.routers import (
    agent,
    cache,
    clients,
    games,
    health,
    jobs,
    mapping,
    schedule,
    stats,
)
from vault_api.scheduler import PrefillScheduler
from vault_api.sizes import SizeCache
from vault_api.worker import PrefillWorker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start/stop the background threads: the prefill job worker (WP 1.4) and,
    when a window is configured, the scheduler (WP 3.5).

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

    # WP 3.2 coupling canary (research doc risk 6): warn once at startup if
    # SteamPrefill's temp-cache directory holds files that don't match the
    # filename contract manifest ingestion depends on. Never fails startup —
    # see manifest_ingest.log_cache_dir_canary's docstring.
    log_cache_dir_canary(settings.steamprefill_cache_dir)

    worker = PrefillWorker(settings, size_cache=app.state.size_cache)
    app.state.worker = worker
    worker.start()

    # WP 3.5: the second background thread. Started AFTER the worker (a sweep
    # is only useful if something is there to drain the queue) and stopped
    # BEFORE it (stop producing jobs before stopping the consumer). Only
    # started when a window is configured — an unset VAULT_SCHEDULE_WINDOW
    # means "off", the safe default, and then no thread exists at all.
    scheduler: PrefillScheduler = app.state.scheduler
    if scheduler.thread_needed:
        scheduler.start()
    if scheduler.enabled:
        window = settings.schedule_window
        assert window is not None  # implied by scheduler_enabled
        logger.info(
            "Scheduler enabled: window %s (server-local time%s), sweeping "
            "every %d minute(s); clients silent for more than %d day(s) are "
            "excluded from the target set.",
            window.raw,
            ", overnight" if window.overnight else "",
            settings.schedule_interval_minutes,
            settings.schedule_client_stale_days,
        )
    else:
        logger.info(
            "Scheduler disabled (VAULT_SCHEDULE_WINDOW is unset). Prefills "
            "only run when something asks for them (POST /v1/prefill). Set a "
            "window like '09:00-17:00' to have vault-api keep the gaming "
            "machines' installed apps current on its own."
        )

    # WP 3.11 (ADR-0008). Independent of the window above: the cache-event
    # sweep rides the same thread but on its own interval and with no window
    # gate at all (it owns the event log's rotation and feeds bypass
    # detection, both of which break if the log goes unread for hours).
    if settings.event_sweep_enabled:
        logger.info(
            "Cache-event sweep enabled: reading %s every %d minute(s). "
            "Miss-triggered prefill is %s (cooldown %d min, at most %d "
            "enqueue(s) per sweep). Bypass detection stays silent until the "
            "feed is %d day(s) old.",
            settings.event_log_path,
            settings.event_sweep_interval_minutes,
            "ON" if settings.miss_trigger_enabled else "OFF",
            settings.miss_trigger_cooldown_minutes,
            settings.miss_trigger_max_per_sweep,
            settings.bypass_window_days,
        )
    else:
        logger.info(
            "Cache-event sweep disabled (VAULT_EVENT_LOG_PATH is unset). No "
            "per-client hit statistics, no bypass detection and no "
            "miss-triggered prefill. Point it at vault-core's structured "
            "event log (VAULT_EVENT_LOG on that side) to switch it on."
        )

    try:
        yield
    finally:
        scheduler.stop()
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
    # Created here (not inside the lifespan) so it exists for a plain
    # TestClient() too, the same reasoning as app.state.settings above —
    # size-reporting endpoints don't need the worker running to be testable.
    app.state.size_cache = SizeCache(ttl_seconds=settings.size_cache_ttl_seconds)
    # WP 3.5: created here rather than in the lifespan (same reasoning as
    # size_cache above) for two reasons: GET /v1/schedule needs it to answer
    # even when the scheduler is disabled or the lifespan never ran (a plain
    # TestClient()), and a test that wants a deterministic clock can replace
    # app.state.scheduler before entering the lifespan, which then starts the
    # replacement. Constructing it starts no thread.
    app.state.scheduler = PrefillScheduler(settings)
    app.include_router(health.router)
    app.include_router(games.router)
    app.include_router(mapping.router)
    app.include_router(jobs.router)
    app.include_router(cache.router)
    app.include_router(agent.router)
    app.include_router(clients.router)
    app.include_router(schedule.router)
    app.include_router(stats.router)
    return app
