"""The prefill scheduler (WP 3.5) — plan §7 Phase 3's "configurable cron window".

What it does, in one sentence: inside a configurable daytime window, every
``VAULT_SCHEDULE_INTERVAL_MINUTES``, take the union of the app ids every
gaming machine most recently reported as installed and enqueue a normal
prefill job for each one.

Plan A7 ("prefill updates automatically during the day") + A8 ("the criterion
is: games actually installed on the gaming machines"). The target set is the
agent reports and **nothing else** in v1 — no popularity heuristic, no
size budget, no manual include/exclude list. Installed *is* the prefill set.

Why enqueuing everything is already the rate limiting
-----------------------------------------------------
ADR-0006's honest-limits section: each per-app check costs a Steam login
(~3 s), so sweeps are "spaced across the cron window, not batched" — batching
several apps into one SteamPrefill invocation would destroy per-app
attribution (one exit code, one summary table, N apps).

The spacing mechanism is therefore deliberately *not* a rate limiter in this
module. There is exactly ONE worker thread and it runs exactly one job at a
time (plan §3, ``vault_api/worker.py``), so a sweep that enqueues 60 apps
produces 60 sequential runs — the queue drains at whatever pace SteamPrefill
manages, one login at a time, and the next sweep cannot even start until the
interval has elapsed. Adding a sleep between enqueues would only make the
*queueing* slow, not the work, while breaking the queue's own dedupe window
and delaying shutdown. Nothing to add.

Cost of a sweep, for the same reason: a non-forced run for an already-current
app is a ~3 s no-op (ADR-0006 decision 1 — SteamPrefill's own up-to-date
bookkeeping). A sweep over an unchanged library is minutes of idle checking,
not a re-download.

Why it needs no lock against ``DELETE /v1/cache/{appid}``
---------------------------------------------------------
Because the scheduler is *just another client of the existing enqueue path*.
It calls ``jobs.enqueue_prefill``, exactly like ``POST /v1/prefill`` does for
a button press in the app (and like the Phase-3 miss trigger will). It never
touches the filesystem, never claims a job and never runs SteamPrefill. So it
introduces no interaction that the reviewed WP 1.4/1.6/3.4 semantics do not
already cover:

* **Job already queued/running for that app** — ``enqueue_prefill`` dedupes
  inside ``BEGIN IMMEDIATE`` and hands back the existing job. The sweep counts
  it as "already active" and moves on.
* **A DELETE is in flight for that app** — ``DELETE`` refuses with 409 while a
  job is queued/running, and its own docstring already documents the
  check-then-act window in the other direction (a job enqueued microseconds
  after its guard). The consequence there is stated and benign: the job
  refills what was deleted. A sweep makes that window no more dangerous, only
  slightly more likely to be hit; the operator-visible effect is that a DELETE
  during the window may 409 and need a retry.
* **A DELETE lands while a swept job is running** — covered by WP 3.4's
  compare-and-swap in ``jobs.clear_needs_force_if_unchanged``: the deletion's
  ``needs_force = 1`` survives, so the next run for that app is forced. That
  is precisely the wedge that CAS was added to close, and it closes it for
  scheduler-enqueued jobs identically.

A lock would therefore protect nothing that is not already protected, while
adding a way for a stuck sweep to block deletions. Deliberately absent.

Threading and database access
-----------------------------
A second daemon thread next to the worker, started and stopped by the FastAPI
lifespan (``vault_api/main.py``), same shape as ``PrefillWorker``. It opens its
own SQLite connection **inside its own thread** and closes it there — the
one-thread-one-connection rule this project's LEARNINGS.md was written in
blood over (a connection touched from two threads segfaulted CPython, see
``vault_api/deps.py``). It shares nothing with the worker but the database
file, and WAL + ``busy_timeout`` (``db.get_connection``) is what makes that
safe.

Timezone
--------
The window is interpreted in **server-local time** — the machine's timezone
(the container's ``TZ``, if you set one). That is the useful reading for
"09:00-17:00 while I'm at work". Every *timestamp* the scheduler stores or
reports is UTC in the project's standard ``YYYY-MM-DDTHH:MM:SSZ`` form, same
as every other table.

DST: the tick loop re-reads the clock and re-evaluates ``contains`` every
minute, so a transition simply shifts when the window opens and closes in UTC
— nothing to correct. On the "spring forward" night an overnight window can
be one hour shorter (and one hour longer in autumn); the interval gate is
UTC-based and unaffected. The only value that can be an hour off across a
transition is the advisory ``next_eligible_at`` in ``GET /v1/schedule`` (see
``schedule_window.next_open``); no decision is made from it.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from vault_api import agent_reports, event_sweep, jobs, webhooks
from vault_api.config import Settings
from vault_api.db import get_connection

# Re-exported under their historical names (WP 3.11 moved the bodies into
# ``jobs`` so ``event_sweep`` can share them without an import cycle — this
# module imports ``event_sweep``, so ``event_sweep`` must not import this one).
# ``scheduler.to_utc_iso`` / ``scheduler.parse_utc_iso`` keep working for every
# existing caller and test.
from vault_api.jobs import (  # noqa: F401  (re-export)
    TIMESTAMP_FORMAT as _TIMESTAMP_FORMAT,
    immediate_transaction,
    parse_utc_iso,
    to_utc_iso,
)
from vault_api.schedule_window import next_open

logger = logging.getLogger(__name__)

#: How often the thread wakes up to ask "is a sweep due?". Not the sweep
#: interval — that is ``VAULT_SCHEDULE_INTERVAL_MINUTES``, checked on each of
#: these ticks. One minute is the resolution the window itself has (whole
#: minutes), and a wakeup that reads one row and usually decides "no" is free.
#: Deliberately not an env var: it is an implementation detail with no
#: operator-visible effect, and the tests inject it directly.
DEFAULT_TICK_SECONDS = 60.0

#: How long ``stop()`` waits for the thread. A tick does a handful of small
#: queries, so this only has to cover an in-flight sweep's enqueue loop (which
#: also checks the stop flag between apps).
SHUTDOWN_JOIN_TIMEOUT_SECONDS = 30.0

#: Returns the current time as an AWARE datetime in the server's local zone.
Clock = Callable[[], datetime]


def local_now() -> datetime:
    """Current server-local time as an *aware* datetime (the default clock).

    Aware, not naive, on purpose: the window is evaluated on the local wall
    clock while every stored timestamp is UTC, and one aware value gives both
    without a second clock reading that could disagree with the first.
    ``datetime.now().astimezone()`` attaches the system's current UTC offset,
    so ``.hour``/``.minute`` are local and ``.astimezone(timezone.utc)`` is
    exact.

    Tests inject a fixed-offset clock instead, which is what makes every
    window decision reproducible on any machine's timezone.
    """
    return datetime.now().astimezone()


# --------------------------------------------------------------------------
# schedule_state (schema v6) — the single-row sweep bookkeeping
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleState:
    """The ``schedule_state`` row. All fields ``None`` before the first sweep."""

    last_sweep_at: str | None
    #: Size of the last sweep's target set. ``None`` while a sweep is running
    #: (or if the process died during one) — see the table's DDL comment.
    last_sweep_targets: int | None
    #: How many NEW jobs the last sweep created (dedupe hits are not counted).
    last_sweep_enqueued: int | None


#: What ``read_state`` returns for a database that has never been swept.
EMPTY_STATE = ScheduleState(
    last_sweep_at=None, last_sweep_targets=None, last_sweep_enqueued=None
)


def read_state(conn: sqlite3.Connection) -> ScheduleState:
    """Read the single ``schedule_state`` row (``EMPTY_STATE`` if absent)."""
    row = conn.execute(
        """
        SELECT last_sweep_at, last_sweep_targets, last_sweep_enqueued
        FROM schedule_state WHERE id = 1
        """
    ).fetchone()
    if row is None:
        return EMPTY_STATE
    return ScheduleState(
        last_sweep_at=row["last_sweep_at"],
        last_sweep_targets=row["last_sweep_targets"],
        last_sweep_enqueued=row["last_sweep_enqueued"],
    )


def interval_elapsed(
    state: ScheduleState, now: datetime, interval_minutes: int
) -> bool:
    """Has ``interval_minutes`` passed since the last sweep started?

    ``True`` when nothing has ever been swept, or when the stored timestamp is
    unreadable (logged — an unreadable value must not disable the scheduler
    permanently; the next sweep overwrites it with a good one).

    A stored timestamp in the *future* (the operator stepped the clock back,
    or the container's clock was wrong on a previous boot) makes this
    ``False`` until real time catches up. That is the deliberate direction to
    fail in: skipping sweeps is recoverable and quiet, whereas treating a
    future timestamp as "long ago" would sweep on every single tick — a Steam
    login storm — until somebody noticed.
    """
    if state.last_sweep_at is None:
        return True
    last = parse_utc_iso(state.last_sweep_at)
    if last is None:
        logger.warning(
            "scheduler: schedule_state.last_sweep_at is %r, which is not a "
            "'YYYY-MM-DDTHH:MM:SSZ' timestamp; treating it as 'never swept' "
            "(the next sweep replaces it)",
            state.last_sweep_at,
        )
        return True
    return now.astimezone(timezone.utc) >= last + timedelta(minutes=interval_minutes)


def claim_sweep(
    conn: sqlite3.Connection, now: datetime, interval_minutes: int
) -> bool:
    """Re-check the interval and stamp ``last_sweep_at`` atomically.

    Claim-then-work, the same shape as ``jobs.claim_next_job``: the interval
    check and the write that consumes it are one ``BEGIN IMMEDIATE``
    transaction, so the check-then-act cannot be interleaved (LEARNINGS.md:
    "every check-then-act needs BEGIN IMMEDIATE"). Only one scheduler thread
    exists today, so this is belt-and-braces — but it is also what makes the
    crash-recovery rule real: ``last_sweep_at`` is committed **before** any
    job is enqueued, so a process that dies mid-sweep does not re-sweep on
    restart. It waits out the interval like any other sweep, and the apps it
    did not reach are picked up by the next one (they are still installed —
    the target set is recomputed from scratch every time, not a work list).

    The two counters are reset to NULL by the same statement; ``finish_sweep``
    fills them in afterwards.
    """
    if not interval_elapsed(read_state(conn), now, interval_minutes):
        return False

    with immediate_transaction(conn):
        # Re-read inside the lock: the value may have changed between the
        # cheap pre-check above (which exists only to avoid taking the write
        # lock on the ~99% of ticks that decide "not yet") and here.
        if not interval_elapsed(read_state(conn), now, interval_minutes):
            return False
        conn.execute(
            """
            INSERT INTO schedule_state
                (id, last_sweep_at, last_sweep_targets, last_sweep_enqueued)
            VALUES (1, ?, NULL, NULL)
            ON CONFLICT(id) DO UPDATE SET
                last_sweep_at = excluded.last_sweep_at,
                last_sweep_targets = NULL,
                last_sweep_enqueued = NULL
            """,
            (to_utc_iso(now),),
        )
    return True


def finish_sweep(conn: sqlite3.Connection, targets: int, enqueued: int) -> None:
    """Record what the just-claimed sweep found and created.

    Deliberately does NOT touch ``last_sweep_at`` — that was stamped at claim
    time and the interval is measured from the sweep's *start*, so a long
    sweep cannot push the next one further out than the configured interval.
    """
    conn.execute(
        """
        UPDATE schedule_state
        SET last_sweep_targets = ?, last_sweep_enqueued = ?
        WHERE id = 1
        """,
        (targets, enqueued),
    )
    conn.commit()


# --------------------------------------------------------------------------
# Target set — plan A8: installed on the gaming machines IS the prefill set
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ExcludedClient:
    """A client whose latest snapshot did not contribute to the target set."""

    client_id: str
    #: One of ``"stale"``, ``"unreadable-snapshot"``, ``"unreadable-timestamp"``.
    reason: str
    #: Its newest report's timestamp, for the log line / the operator.
    last_reported_at: str | None


@dataclass(frozen=True)
class TargetSet:
    """The union of every fresh client's latest installed list."""

    appids: list[int]
    included_clients: list[str]
    excluded_clients: list[ExcludedClient]


def compute_targets(
    conn: sqlite3.Connection, now: datetime, stale_after_days: int
) -> TargetSet:
    """Union the app ids from every client's LATEST agent report.

    "Latest" is ``agent_reports.latest_snapshot`` — newest by **rowid**, i.e.
    insertion order, not by ``reported_at`` (WP 2.4's rule: second-precision
    timestamps tie, and a clock stepped backwards would reorder the chain).
    Reusing that function rather than writing a second query keeps the rule in
    exactly one place.

    A client is EXCLUDED when:

    * its newest report is older than ``stale_after_days`` (see
      ``config.DEFAULT_SCHEDULE_CLIENT_STALE_DAYS`` for why a silent machine
      should stop driving downloads),
    * that report's ``appids`` JSON is unreadable (corrupt row — the same
      degradation ``POST /v1/agent/installed`` already applies), or
    * its ``reported_at`` is not a parseable timestamp, which makes the
      staleness question unanswerable. Excluded rather than assumed fresh:
      the fail-safe direction here is "prefill less", never "prefill on the
      strength of a value we could not read".

    Exclusions are returned (and logged by the caller), never silently
    dropped — "the scheduler stopped covering my Steam Deck" must be
    diagnosable from ``GET /v1/schedule`` plus the log, not from reading this
    source file.

    Intersected with nothing else: plan A8 makes the installed list the whole
    criterion in v1.
    """
    cutoff = now.astimezone(timezone.utc) - timedelta(days=stale_after_days)

    client_rows = conn.execute(
        "SELECT DISTINCT client_id FROM agent_reports ORDER BY client_id"
    ).fetchall()

    appids: set[int] = set()
    included: list[str] = []
    excluded: list[ExcludedClient] = []

    for row in client_rows:
        client_id = str(row["client_id"])
        snapshot = agent_reports.latest_snapshot(conn, client_id)
        if snapshot is None:  # pragma: no cover - DISTINCT proves a row exists
            continue

        if snapshot.appids is None:
            excluded.append(
                ExcludedClient(client_id, "unreadable-snapshot", snapshot.reported_at)
            )
            continue

        reported_at = parse_utc_iso(snapshot.reported_at)
        if reported_at is None:
            excluded.append(
                ExcludedClient(
                    client_id, "unreadable-timestamp", snapshot.reported_at
                )
            )
            continue

        if reported_at < cutoff:
            excluded.append(
                ExcludedClient(client_id, "stale", snapshot.reported_at)
            )
            continue

        included.append(client_id)
        appids.update(snapshot.appids)

    return TargetSet(
        appids=sorted(appids),
        included_clients=included,
        excluded_clients=excluded,
    )


# --------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SweepResult:
    """Outcome of one tick. ``swept`` is False when nothing was due."""

    swept: bool
    #: Why not, when ``swept`` is False: ``"disabled"``, ``"outside-window"``,
    #: ``"interval-not-elapsed"``. ``""`` when a sweep did run.
    skipped_reason: str = ""
    targets: tuple[int, ...] = ()
    #: Apps a NEW job was created for.
    enqueued: tuple[int, ...] = ()
    #: Apps that already had a queued/running job (the queue's own dedupe).
    already_active: tuple[int, ...] = ()
    excluded_clients: tuple[ExcludedClient, ...] = ()
    #: True when shutdown interrupted the enqueue loop.
    aborted: bool = False
    swept_at: str | None = None


def _never() -> bool:
    return False


def maybe_sweep(
    conn: sqlite3.Connection,
    settings: Settings,
    now: datetime,
    should_abort: Callable[[], bool] = _never,
) -> SweepResult:
    """One scheduler tick: decide, and sweep if due.

    Order of gates — cheapest and most decisive first:

    1. no window configured -> the scheduler is off (nothing here ever runs);
    2. ``now`` outside the window -> not our time of day;
    3. the interval has not elapsed since the last sweep STARTED -> too soon
       (this is also the crash-recovery gate, see ``claim_sweep``).

    Only then is the target set computed and the queue written to. Splitting
    this out of the thread makes every decision testable by passing a
    datetime, with no clock and no waiting.
    """
    window = settings.schedule_window
    if window is None:
        return SweepResult(swept=False, skipped_reason="disabled")
    if not window.contains(now):
        return SweepResult(swept=False, skipped_reason="outside-window")
    if not claim_sweep(conn, now, settings.schedule_interval_minutes):
        return SweepResult(swept=False, skipped_reason="interval-not-elapsed")

    swept_at = to_utc_iso(now)
    target_set = compute_targets(conn, now, settings.schedule_client_stale_days)

    enqueued: list[int] = []
    already_active: list[int] = []
    aborted = False
    for appid in target_set.appids:
        if should_abort():
            # Shutdown mid-sweep. The remaining apps are simply not enqueued;
            # nothing is left half-done, because each enqueue is its own
            # committed transaction and the next sweep recomputes the whole
            # target set anyway.
            aborted = True
            break
        _job, created = jobs.enqueue_prefill(conn, appid)
        (enqueued if created else already_active).append(appid)

    finish_sweep(conn, targets=len(target_set.appids), enqueued=len(enqueued))

    logger.info(
        "scheduler: sweep at %s enqueued %d new job(s), %d app(s) already "
        "queued/running, from %d target app(s) across %d client(s)%s%s",
        swept_at, len(enqueued), len(already_active), len(target_set.appids),
        len(target_set.included_clients),
        (
            " (EXCLUDED clients: "
            + ", ".join(
                f"{c.client_id}={c.reason}" for c in target_set.excluded_clients
            )
            + ")"
            if target_set.excluded_clients
            else ""
        ),
        " [ABORTED by shutdown]" if aborted else "",
    )

    return SweepResult(
        swept=True,
        targets=tuple(target_set.appids),
        enqueued=tuple(enqueued),
        already_active=tuple(already_active),
        excluded_clients=tuple(target_set.excluded_clients),
        aborted=aborted,
        swept_at=swept_at,
    )


def next_eligible_at(
    settings: Settings, state: ScheduleState, now: datetime
) -> str | None:
    """Earliest UTC instant at which a sweep could run. ``None`` if disabled.

    Both gates, in order: the interval (from the last sweep's start), then the
    window. ``now`` itself when a sweep is due right now.

    An **estimate**, not a promise — it is computed from the current clock and
    can be an hour out across a DST transition (see
    ``schedule_window.next_open``). Nothing decides anything from it; the
    thread re-evaluates the real gates every minute.
    """
    window = settings.schedule_window
    if window is None:
        return None

    now_utc = now.astimezone(timezone.utc)
    earliest = now_utc
    if state.last_sweep_at is not None:
        last = parse_utc_iso(state.last_sweep_at)
        if last is not None:
            earliest = max(
                now_utc, last + timedelta(minutes=settings.schedule_interval_minutes)
            )

    # Back into the local frame `now` carries, because the window is a
    # local-wall-clock concept.
    candidate_local = earliest.astimezone(now.tzinfo)
    return to_utc_iso(next_open(window, candidate_local))


# --------------------------------------------------------------------------
# The thread
# --------------------------------------------------------------------------


class PrefillScheduler:
    """The second background thread: ticks, and sweeps when due.

    Constructed unconditionally by ``main.create_app`` (so
    ``GET /v1/schedule`` has something to ask for the clock and the config,
    enabled or not) but only ``start()``ed by the lifespan when a window is
    configured. Constructing it starts nothing.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Clock = local_now,
        tick_seconds: float = DEFAULT_TICK_SECONDS,
        webhook_notifier: "webhooks.WebhookNotifier | None" = None,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._tick_seconds = tick_seconds
        #: WP 3.13: threaded through to event_sweep.maybe_sweep's persist step
        #: (client.bypass_suspected transitions). None in every existing
        #: caller/test that predates the webhook feature.
        self._webhook_notifier = webhook_notifier
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- accessors ---------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Is the PREFILL scheduler on (i.e. is a window configured)?

        Unchanged meaning since WP 3.5 — ``GET /v1/schedule`` reports it. Note
        that this is no longer the same question as "does the thread need to
        run": WP 3.11's event sweep rides the same tick and is enabled
        separately. See ``thread_needed``.
        """
        return self._settings.scheduler_enabled

    @property
    def thread_needed(self) -> bool:
        """Should the lifespan actually start this thread?

        True when EITHER background job wants ticks: the prefill sweep
        (``VAULT_SCHEDULE_WINDOW``) or the cache-event sweep
        (``VAULT_EVENT_LOG_PATH``, WP 3.11). They are independently
        configurable — running the event sweep with no prefill window is a
        perfectly ordinary setup ("collect statistics, never download on a
        schedule"), and so is the reverse.
        """
        return self._settings.scheduler_enabled or self._settings.event_sweep_enabled

    @property
    def settings(self) -> Settings:
        return self._settings

    def now(self) -> datetime:
        """Current time per this scheduler's clock (aware, server-local).

        Exposed so ``GET /v1/schedule`` reports ``next_eligible_at`` on the
        same clock the sweeps use — including an injected one in tests.
        """
        return self._clock()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:  # pragma: no cover - guarded by lifespan
            raise RuntimeError("scheduler already started")
        # daemon=True for the same reason as the worker: a hard shutdown must
        # never be able to wedge the interpreter.
        self._thread = threading.Thread(
            target=self._run, name="vault-prefill-scheduler", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = SHUTDOWN_JOIN_TIMEOUT_SECONDS) -> None:
        """Signal and join. A no-op when the scheduler was never started."""
        self._stop.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout=timeout)
        if thread.is_alive():  # pragma: no cover - a wedged DB write only
            logger.warning(
                "Scheduler thread did not stop within %.0fs; leaving it as a "
                "daemon thread.",
                timeout,
            )

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    # -- loop --------------------------------------------------------------

    def _run(self) -> None:
        conn = get_connection(self._settings.db_path)
        try:
            # Tick immediately, then wait: a restart inside the window with the
            # interval already elapsed should act now, not sit out a tick. The
            # interval gate in claim_sweep is what stops that from becoming a
            # restart-driven sweep storm.
            while True:
                self._tick(conn)
                if self._stop.wait(self._tick_seconds):
                    return
        finally:
            conn.close()

    def _tick(self, conn: sqlite3.Connection) -> None:
        """One decision. Never raises — a bug here must not kill the thread.

        Same last-resort net as the worker's job body: if this thread dies,
        nothing restarts it and the scheduler silently stops scheduling, which
        is far worse than a logged traceback and a retry in a minute.

        Two independent jobs ride this tick, each with its OWN ``try`` (WP
        3.11): a failing cache-event sweep must not stop prefill scheduling,
        and a failing prefill sweep must not stop the event sweep — which owns
        the event log's rotation, so silencing it would eventually fill a disk.
        The event sweep goes first because it is the cheap one and because a
        miss it discovers can enqueue work the prefill sweep would then see.
        """
        now = self._clock()
        try:
            event_sweep.maybe_sweep(conn, self._settings, now, self._webhook_notifier)
        except Exception:
            logger.exception(
                "Cache-event sweep failed (WP 3.11). The thread survives and "
                "retries in %.0fs. The event-log cursor only advances inside "
                "the transaction that commits a batch's effects, so nothing "
                "was half-applied and no line was consumed -- the next sweep "
                "re-reads from the last committed offset.",
                self._tick_seconds,
            )

        try:
            maybe_sweep(
                conn, self._settings, now, should_abort=self._stop.is_set
            )
        except Exception:
            logger.exception(
                "Scheduler tick failed. The thread survives and ticks again "
                "in %.0fs. If the failure happened AFTER the sweep was "
                "claimed, last_sweep_at is already stamped and its counters "
                "stay NULL, so the next sweep is one full interval away — "
                "apps it did not reach are picked up then (the target set is "
                "recomputed from scratch, never resumed).",
                self._tick_seconds,
            )
