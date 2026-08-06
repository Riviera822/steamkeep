"""Scheduler endpoint (WP 3.5): ``GET /v1/schedule``.

Read-only on purpose. There is **no POST/PUT** to change the window, the
interval or the staleness bound at runtime: every setting in vault-api comes
from the environment and is read once at startup (plan §9's simplicity
stance, ``vault_api/config.py``). A config-write API would need persistence,
precedence rules against the env, and a validation path separate from the
startup one — three moving parts to save an operator one ``docker compose up
-d`` after editing ``.env``.

What it answers is "what will the scheduler do, and what did it last do",
which is the question you actually have when a game did not get updated
overnight.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth").
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from vault_api import scheduler as scheduler_module
from vault_api.auth import require_api_key
from vault_api.deps import DbOpener, db_opener

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["schedule"])


def _format_offset(moment: datetime) -> str:
    """``UTC+02:00`` for the server's current local offset."""
    offset = moment.utcoffset()
    if offset is None:  # pragma: no cover - the scheduler clock is always aware
        return "UTC"
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    return f"UTC{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"


class ScheduleOut(BaseModel):
    #: False unless VAULT_SCHEDULE_WINDOW is set — the safe default. Every
    #: field below still reports the configured values so an operator can see
    #: what *would* happen before switching it on.
    enabled: bool
    #: The window exactly as configured, or null when unset.
    window: str | None
    #: True for a window that wraps past midnight (e.g. "22:00-06:00").
    overnight: bool
    interval_minutes: int
    #: Clients whose newest agent report is older than this are excluded from
    #: the target set.
    client_stale_days: int
    #: The window is interpreted in SERVER-LOCAL time; this is that zone's
    #: current UTC offset. Every timestamp in this response is UTC.
    server_timezone: str
    #: When the last sweep STARTED (UTC). Null if none has ever run.
    last_sweep_at: str | None
    #: Size of the last sweep's target set, and how many NEW jobs it created
    #: (apps that already had a queued/running job are not counted — the queue
    #: dedupes). Both null while a sweep is in flight, or if the process died
    #: during one.
    last_sweep_targets: int | None
    last_sweep_enqueued: int | None
    #: Earliest UTC instant a sweep could next run (interval gate, then window
    #: gate). An estimate computed from the current clock, not a promise — it
    #: can be an hour off across a DST transition, and it is null when the
    #: scheduler is disabled. Nothing decides anything from this value.
    next_eligible_at: str | None


@router.get("/v1/schedule", response_model=ScheduleOut)
def get_schedule(
    request: Request,
    open_db: DbOpener = Depends(db_opener),
) -> ScheduleOut:
    """Scheduler configuration plus last-sweep bookkeeping (WP 3.5)."""
    scheduler = request.app.state.scheduler
    settings = scheduler.settings
    window = settings.schedule_window
    now = scheduler.now()

    with open_db() as conn:
        state = scheduler_module.read_state(conn)

    return ScheduleOut(
        enabled=settings.scheduler_enabled,
        window=None if window is None else window.raw,
        overnight=bool(window is not None and window.overnight),
        interval_minutes=settings.schedule_interval_minutes,
        client_stale_days=settings.schedule_client_stale_days,
        server_timezone=_format_offset(now),
        last_sweep_at=state.last_sweep_at,
        last_sweep_targets=state.last_sweep_targets,
        last_sweep_enqueued=state.last_sweep_enqueued,
        next_eligible_at=scheduler_module.next_eligible_at(settings, state, now),
    )
