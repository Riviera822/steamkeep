"""The prefill scheduler (WP 3.5): decisions, target set, sweep, endpoint.

Every timing decision is driven by an INJECTED clock — a fixed-offset aware
datetime handed to ``maybe_sweep``/``PrefillScheduler`` — so nothing here
waits for real time to pass and nothing depends on the timezone of the
machine running the tests. The only real waiting is the end-to-end test that
lets the scheduler thread and the job worker drain a queue, and that polls the
API rather than sleeping a fixed amount.

Agent-report fixtures are written directly into ``agent_reports`` (synthetic,
modelled on the real row shape) because the staleness rules need control over
``reported_at``, which the HTTP endpoint takes from the server clock.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests import stub_prefill
from tests.conftest import TEST_API_KEY
from vault_api import jobs as jobs_queue
from vault_api import scheduler as scheduler_module
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app
from vault_api.schedule_window import parse_window
from vault_api.scheduler import (
    EMPTY_STATE,
    PrefillScheduler,
    ScheduleState,
    claim_sweep,
    compute_targets,
    finish_sweep,
    interval_elapsed,
    maybe_sweep,
    next_eligible_at,
    read_state,
)

AUTH = {"X-Api-Key": TEST_API_KEY}

#: Fixed +02:00, so a machine in UTC and a machine in Berlin agree.
TZ = timezone(timedelta(hours=2))


def local(hour: int, minute: int = 0, day: int = 6) -> datetime:
    """An aware server-local datetime on 2026-08-``day``."""
    return datetime(2026, 8, day, hour, minute, tzinfo=TZ)


def utc_iso_of(moment: datetime) -> str:
    """The stored-UTC rendering of any aware moment."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_iso(hour: int, minute: int = 0, day: int = 6) -> str:
    """The stored-UTC rendering of a local moment on 2026-08-``day``."""
    return utc_iso_of(local(hour, minute, day))


def make_settings(
    tmp_path: Path,
    *,
    window: str | None = "09:00-17:00",
    interval_minutes: int = 180,
    stale_days: int = 7,
    steamprefill_path: str = "",
    cache_root: Path | None = None,
) -> Settings:
    return Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(cache_root or (tmp_path / "cache")),
        log_level="INFO",
        steamprefill_path=steamprefill_path,
        prefill_timeout_seconds=60,
        worker_poll_seconds=0.02,
        steamprefill_cache_dir=str(tmp_path / "unused-steamprefill-cache"),
        manifest_archive_dir=str(tmp_path / "manifest-archive"),
        schedule_window=None if window is None else parse_window(window),
        schedule_interval_minutes=interval_minutes,
        schedule_client_stale_days=stale_days,
    )


@pytest.fixture
def conn(tmp_path: Path):
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    connection = get_connection(db_path)
    try:
        yield connection
    finally:
        connection.close()


def insert_report(
    conn: sqlite3.Connection,
    client_id: str,
    appids: list[int] | str,
    reported_at: str,
) -> None:
    """One synthetic ``agent_reports`` row with an explicit timestamp.

    ``appids`` may be a raw string instead of a list, to plant the corrupt
    row the unreadable-snapshot path degrades on.
    """
    payload = (
        appids
        if isinstance(appids, str)
        else json.dumps(appids, separators=(",", ":"))
    )
    conn.execute(
        "INSERT INTO agent_reports (client_id, reported_at, appids) VALUES (?, ?, ?)",
        (client_id, reported_at, payload),
    )
    conn.commit()


# ==========================================================================
# schedule_state bookkeeping (schema v6)
# ==========================================================================


def test_state_is_empty_before_the_first_sweep(conn: sqlite3.Connection) -> None:
    assert read_state(conn) == EMPTY_STATE


def test_claim_stamps_the_start_time_and_nulls_the_counters(
    conn: sqlite3.Connection,
) -> None:
    assert claim_sweep(conn, local(10), interval_minutes=180) is True

    state = read_state(conn)
    assert state.last_sweep_at == utc_iso(10)
    # NULL until finish_sweep — "in flight", not "zero apps".
    assert state.last_sweep_targets is None
    assert state.last_sweep_enqueued is None


def test_finish_records_counts_without_moving_the_start_time(
    conn: sqlite3.Connection,
) -> None:
    claim_sweep(conn, local(10), interval_minutes=180)
    finish_sweep(conn, targets=12, enqueued=3)

    state = read_state(conn)
    assert (state.last_sweep_targets, state.last_sweep_enqueued) == (12, 3)
    # The interval is measured from the sweep's START, so a slow sweep must
    # not push the next one out.
    assert state.last_sweep_at == utc_iso(10)


def test_schedule_state_holds_exactly_one_row(conn: sqlite3.Connection) -> None:
    """The CHECK (id = 1) is what makes 'the single row' a guarantee."""
    claim_sweep(conn, local(10), interval_minutes=180)
    claim_sweep(conn, local(14), interval_minutes=180)

    (count,) = conn.execute("SELECT COUNT(*) FROM schedule_state").fetchone()
    assert count == 1
    assert read_state(conn).last_sweep_at == utc_iso(14)

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO schedule_state (id, last_sweep_at) VALUES (2, 'x')")


# ==========================================================================
# Interval logic
# ==========================================================================


def test_interval_elapsed_when_nothing_was_ever_swept() -> None:
    assert interval_elapsed(EMPTY_STATE, local(10), 180) is True


def test_interval_not_elapsed_before_the_full_interval() -> None:
    state = ScheduleState(utc_iso(10), None, None)

    assert interval_elapsed(state, local(12, 59), 180) is False
    assert interval_elapsed(state, local(13, 0), 180) is True
    assert interval_elapsed(state, local(13, 1), 180) is True


def test_unreadable_last_sweep_at_is_treated_as_never_swept() -> None:
    """A hand-edited/corrupt value must not disable the scheduler forever."""
    for bad in ("", "not-a-time", "2026-08-06 10:00:00", "2026-08-06T10:00:00+02:00"):
        assert interval_elapsed(ScheduleState(bad, None, None), local(10), 180) is True


def test_a_future_last_sweep_at_blocks_rather_than_storms() -> None:
    """Clock stepped backwards: skip sweeps until real time catches up.

    The other direction — treating a future timestamp as 'long ago' — would
    sweep on every tick, i.e. a Steam login storm.
    """
    state = ScheduleState(utc_iso(10, day=7), None, None)

    assert interval_elapsed(state, local(11), 180) is False


def test_interval_survives_a_restart(tmp_path: Path) -> None:
    """The crash-recovery rule: a restart mid-window must not re-sweep.

    ``schedule_state`` lives in the database precisely so this holds; the two
    connections below stand in for two process lifetimes.
    """
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    first = get_connection(db_path)
    try:
        assert claim_sweep(first, local(10), interval_minutes=180) is True
    finally:
        first.close()

    # "Process restarts" — a brand-new connection, no in-memory state at all.
    second = get_connection(db_path)
    try:
        assert claim_sweep(second, local(10, 5), interval_minutes=180) is False
        assert claim_sweep(second, local(13, 0), interval_minutes=180) is True
    finally:
        second.close()


# ==========================================================================
# Target set (plan A8: installed IS the prefill set)
# ==========================================================================


def test_targets_are_the_union_of_every_fresh_clients_latest_report(
    conn: sqlite3.Connection,
) -> None:
    insert_report(conn, "gaming-pc", [440, 730], utc_iso(9))
    insert_report(conn, "steam-deck", [730, 1091500], utc_iso(9, 30))

    result = compute_targets(conn, local(10), stale_after_days=7)

    assert result.appids == [440, 730, 1091500]  # union, sorted, deduped
    assert result.included_clients == ["gaming-pc", "steam-deck"]
    assert result.excluded_clients == []


def test_only_the_latest_snapshot_of_a_client_counts(
    conn: sqlite3.Connection,
) -> None:
    """An uninstalled game drops out of the target set (ADR-0002 removals)."""
    insert_report(conn, "gaming-pc", [440, 730], utc_iso(8))
    insert_report(conn, "gaming-pc", [440], utc_iso(9))

    assert compute_targets(conn, local(10), stale_after_days=7).appids == [440]


def test_a_client_silent_for_longer_than_the_bound_is_excluded(
    conn: sqlite3.Connection,
) -> None:
    insert_report(conn, "gaming-pc", [440], utc_iso(9))
    insert_report(
        conn, "retired-pc", [999], utc_iso_of(local(9) - timedelta(days=8))
    )

    result = compute_targets(conn, local(10), stale_after_days=7)

    assert result.appids == [440]
    assert result.included_clients == ["gaming-pc"]
    assert [(c.client_id, c.reason) for c in result.excluded_clients] == [
        ("retired-pc", "stale")
    ]


def test_the_staleness_bound_is_configurable(conn: sqlite3.Connection) -> None:
    insert_report(conn, "steam-deck", [730], utc_iso(9, day=4))  # 2 days old

    assert compute_targets(conn, local(10), stale_after_days=7).appids == [730]
    assert compute_targets(conn, local(10), stale_after_days=1).appids == []


def test_a_corrupt_snapshot_excludes_that_client_only(
    conn: sqlite3.Connection,
) -> None:
    insert_report(conn, "gaming-pc", [440], utc_iso(9))
    insert_report(conn, "broken", "{not json", utc_iso(9))

    result = compute_targets(conn, local(10), stale_after_days=7)

    assert result.appids == [440]
    assert [(c.client_id, c.reason) for c in result.excluded_clients] == [
        ("broken", "unreadable-snapshot")
    ]


def test_an_unreadable_timestamp_excludes_rather_than_assumes_fresh(
    conn: sqlite3.Connection,
) -> None:
    """Fail-safe direction: prefill less, never on an unreadable value."""
    insert_report(conn, "weird-clock", [12345], "yesterday-ish")

    result = compute_targets(conn, local(10), stale_after_days=7)

    assert result.appids == []
    assert [(c.client_id, c.reason) for c in result.excluded_clients] == [
        ("weird-clock", "unreadable-timestamp")
    ]


def test_no_clients_means_no_targets(conn: sqlite3.Connection) -> None:
    result = compute_targets(conn, local(10), stale_after_days=7)

    assert (result.appids, result.included_clients, result.excluded_clients) == (
        [],
        [],
        [],
    )


# ==========================================================================
# maybe_sweep: the gates
# ==========================================================================


def test_no_window_configured_means_no_sweep_ever(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, window=None)
    insert_report(conn, "gaming-pc", [440], utc_iso(9))

    result = maybe_sweep(conn, settings, local(10))

    assert (result.swept, result.skipped_reason) == (False, "disabled")
    assert read_state(conn) == EMPTY_STATE
    assert jobs_queue.list_jobs(conn, 10) == []


@pytest.mark.parametrize("hour", [8, 17, 23, 3])
def test_outside_the_window_nothing_happens(
    conn: sqlite3.Connection, tmp_path: Path, hour: int
) -> None:
    settings = make_settings(tmp_path, window="09:00-17:00")
    insert_report(conn, "gaming-pc", [440], utc_iso(9))

    result = maybe_sweep(conn, settings, local(hour))

    assert (result.swept, result.skipped_reason) == (False, "outside-window")
    # Not even the timestamp is stamped — an out-of-window tick is a no-op.
    assert read_state(conn).last_sweep_at is None


def test_an_overnight_window_sweeps_after_midnight(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, window="22:00-06:00")
    insert_report(conn, "gaming-pc", [440], utc_iso(21))

    assert maybe_sweep(conn, settings, local(12)).skipped_reason == "outside-window"
    assert maybe_sweep(conn, settings, local(2)).swept is True


def test_inside_the_window_a_sweep_enqueues_every_target(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path)
    insert_report(conn, "gaming-pc", [440, 730], utc_iso(9))

    result = maybe_sweep(conn, settings, local(10))

    assert result.swept is True
    assert result.targets == (440, 730)
    assert result.enqueued == (440, 730)
    assert result.already_active == ()

    queued = [(job["appid"], job["type"], job["status"]) for job in jobs_queue.list_jobs(conn, 10)]
    assert sorted(queued) == [(440, "prefill", "queued"), (730, "prefill", "queued")]

    state = read_state(conn)
    assert (state.last_sweep_at, state.last_sweep_targets, state.last_sweep_enqueued) == (
        utc_iso(10),
        2,
        2,
    )


def test_a_second_sweep_inside_the_interval_is_refused(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path, interval_minutes=180)
    insert_report(conn, "gaming-pc", [440], utc_iso(9))

    assert maybe_sweep(conn, settings, local(9, 30)).swept is True
    assert maybe_sweep(conn, settings, local(12, 29)).skipped_reason == (
        "interval-not-elapsed"
    )
    assert maybe_sweep(conn, settings, local(12, 30)).swept is True


def test_apps_already_queued_are_not_enqueued_twice(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """The dedupe is the QUEUE's (WP 1.4), not a pre-filter — same result."""
    settings = make_settings(tmp_path)
    insert_report(conn, "gaming-pc", [440, 730], utc_iso(9))
    jobs_queue.enqueue_prefill(conn, 440)  # e.g. a button press in the app

    result = maybe_sweep(conn, settings, local(10))

    assert result.targets == (440, 730)
    assert result.enqueued == (730,)
    assert result.already_active == (440,)
    assert [job["appid"] for job in jobs_queue.list_jobs(conn, 10)] == [730, 440]
    # last_sweep_enqueued counts NEW jobs only.
    assert read_state(conn).last_sweep_enqueued == 1


def test_a_running_job_also_counts_as_already_active(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path)
    insert_report(conn, "gaming-pc", [440], utc_iso(9))
    jobs_queue.enqueue_prefill(conn, 440)
    jobs_queue.claim_next_job(conn)  # -> 'running'

    result = maybe_sweep(conn, settings, local(10))

    assert result.already_active == (440,)
    assert len(jobs_queue.list_jobs(conn, 10)) == 1


def test_a_sweep_with_no_targets_still_consumes_the_interval(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """Otherwise an installation with no agents would sweep on every tick."""
    settings = make_settings(tmp_path)

    result = maybe_sweep(conn, settings, local(10))

    assert (result.swept, result.targets) == (True, ())
    assert read_state(conn).last_sweep_at == utc_iso(10)
    assert maybe_sweep(conn, settings, local(10, 1)).skipped_reason == (
        "interval-not-elapsed"
    )


def test_shutdown_mid_sweep_stops_enqueuing_and_is_recorded(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path)
    insert_report(conn, "gaming-pc", [440, 730, 1091500], utc_iso(9))

    calls = {"n": 0}

    def should_abort() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # let the first app through, then "stop"

    result = maybe_sweep(conn, settings, local(10), should_abort=should_abort)

    assert result.aborted is True
    assert result.enqueued == (440,)
    assert [job["appid"] for job in jobs_queue.list_jobs(conn, 10)] == [440]
    # The claim stands: the next sweep is one interval away and recomputes the
    # whole target set, so nothing is "resumed" or lost.
    assert read_state(conn).last_sweep_enqueued == 1


def test_a_sweep_that_crashes_after_claiming_does_not_retry_immediately(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(tmp_path)
    insert_report(conn, "gaming-pc", [440], utc_iso(9))

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated crash mid-sweep")

    monkeypatch.setattr(scheduler_module, "compute_targets", boom)
    with pytest.raises(RuntimeError):
        maybe_sweep(conn, settings, local(10))
    monkeypatch.undo()

    # Claim-then-work: the timestamp is committed before any work, so the
    # failed sweep consumed its interval instead of hammering on every tick.
    state = read_state(conn)
    assert state.last_sweep_at == utc_iso(10)
    assert state.last_sweep_targets is None  # honest: that sweep never finished
    assert maybe_sweep(conn, settings, local(10, 1)).skipped_reason == (
        "interval-not-elapsed"
    )


# ==========================================================================
# next_eligible_at
# ==========================================================================


def test_next_eligible_is_now_when_a_sweep_is_due(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    assert next_eligible_at(settings, EMPTY_STATE, local(10)) == utc_iso(10)


def test_next_eligible_adds_the_interval_inside_the_window(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, interval_minutes=180)
    state = ScheduleState(utc_iso(10), 5, 2)

    assert next_eligible_at(settings, state, local(10, 5)) == utc_iso(13)


def test_next_eligible_skips_to_the_next_window_opening(tmp_path: Path) -> None:
    """Interval lands after the window closes -> tomorrow's opening."""
    settings = make_settings(tmp_path, window="09:00-17:00", interval_minutes=180)
    state = ScheduleState(utc_iso(16), 5, 2)

    assert next_eligible_at(settings, state, local(16, 5)) == utc_iso(9, day=7)


def test_next_eligible_is_null_when_disabled(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, window=None)

    assert next_eligible_at(settings, EMPTY_STATE, local(10)) is None


# ==========================================================================
# GET /v1/schedule
# ==========================================================================


def test_schedule_endpoint_requires_the_api_key(tmp_path: Path) -> None:
    client = TestClient(create_app(make_settings(tmp_path)))

    assert client.get("/v1/schedule").status_code == 401


def test_schedule_endpoint_reports_config_and_last_sweep(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, window="09:00-17:00", interval_minutes=180)
    app = create_app(settings)
    app.state.scheduler = PrefillScheduler(settings, clock=lambda: local(10, 5))

    conn = get_connection(settings.db_path)
    try:
        insert_report(conn, "gaming-pc", [440, 730], utc_iso(9))
        maybe_sweep(conn, settings, local(10))
    finally:
        conn.close()

    body = TestClient(app).get("/v1/schedule", headers=AUTH).json()

    assert body == {
        "enabled": True,
        "window": "09:00-17:00",
        "overnight": False,
        "interval_minutes": 180,
        "client_stale_days": 7,
        "server_timezone": "UTC+02:00",
        "last_sweep_at": utc_iso(10),
        "last_sweep_targets": 2,
        "last_sweep_enqueued": 2,
        "next_eligible_at": utc_iso(13),
    }


def test_schedule_endpoint_reports_a_disabled_scheduler(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, window=None)
    app = create_app(settings)
    app.state.scheduler = PrefillScheduler(settings, clock=lambda: local(10))

    body = TestClient(app).get("/v1/schedule", headers=AUTH).json()

    assert body["enabled"] is False
    assert body["window"] is None
    assert body["next_eligible_at"] is None
    # The defaults are still reported, so an operator can see what enabling
    # the window would do.
    assert (body["interval_minutes"], body["client_stale_days"]) == (180, 7)


def test_schedule_endpoint_marks_an_overnight_window(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, window="22:00-06:00")
    app = create_app(settings)
    app.state.scheduler = PrefillScheduler(settings, clock=lambda: local(23))

    body = TestClient(app).get("/v1/schedule", headers=AUTH).json()

    assert (body["window"], body["overnight"]) == ("22:00-06:00", True)


# ==========================================================================
# The thread, end to end (scheduler -> queue -> worker -> SteamPrefill stub)
# ==========================================================================

#: A real-shaped SteamPrefill summary for a non-forced, already-current run
#: (ADR-0006 decision 1: Updated=0 AND UpToDate>0 is the 'confirmed current'
#: outcome). Without it the worker's job-outcome rule would read 0/0 as
#: "app never considered" and fail the job — the WP 1.7 trap.
UP_TO_DATE_SUMMARY = (
    "Prefilled 1 apps totaling 0 b in 03.2491\n"
    "\n"
    " Updated | Up To Date\n"
    "---------+------------\n"
    "    0    |      1\n"
)


def wait_for(predicate, timeout: float = 30.0, what: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    pytest.fail(f"{what} did not happen within {timeout}s")


def test_scheduler_thread_sweeps_and_the_worker_drains_the_queue(
    tmp_path: Path,
) -> None:
    """The whole WP 3.5 path with a real second thread, no real waiting.

    The clock is FROZEN inside the window, which makes the run deterministic
    in both directions: the first tick sweeps (nothing has ever been swept),
    and no later tick can, because the frozen clock never advances past the
    interval.

    App 440 is pre-seeded as already filled (``needs_force = 0``), so this
    also proves the sweep produces a **non-forced** run — ADR-0006's ~3 s
    no-op staleness check, which is the entire reason a sweep over an
    unchanged library is cheap. A scheduler that forced every run would
    re-touch every chunk of every game every three hours.
    """
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(
        bindir,
        cache_root=str(cache_root),
        depots_by_app={440: [441]},
        summary_text=UP_TO_DATE_SUMMARY,
    )
    settings = make_settings(
        tmp_path, steamprefill_path=executable, cache_root=cache_root
    )

    app = create_app(settings)
    app.state.scheduler = PrefillScheduler(
        settings, clock=lambda: local(10), tick_seconds=0.02
    )

    conn = get_connection(settings.db_path)
    try:
        insert_report(conn, "gaming-pc", [440], utc_iso(9, 30))
        conn.execute(
            "INSERT INTO apps (appid, status, needs_force) VALUES (440, 'done', 0)"
        )
        conn.commit()
    finally:
        conn.close()

    with TestClient(app) as client:
        wait_for(
            lambda: any(
                job["status"] == "done"
                for job in client.get("/v1/jobs", headers=AUTH).json()
            ),
            what="a scheduled prefill job finishing",
        )

        finished = client.get("/v1/jobs", headers=AUTH).json()
        assert len(finished) == 1, finished
        assert (finished[0]["appid"], finished[0]["type"]) == (440, "prefill")
        assert finished[0]["updated"] == 0 and finished[0]["up_to_date"] == 1

        schedule = client.get("/v1/schedule", headers=AUTH).json()
        assert schedule["enabled"] is True
        assert schedule["last_sweep_at"] == utc_iso(10)
        assert (schedule["last_sweep_targets"], schedule["last_sweep_enqueued"]) == (1, 1)

    # ADR-0006 decision 2: a scheduled run of an already-filled app is NOT
    # forced. (--no-ansi is always passed; see prefill.py.)
    assert stub_prefill.read_argv(bindir) == ["prefill", "--no-ansi"]
    assert stub_prefill.read_selection(bindir) == [440]
    # The frozen clock guarantees the interval never elapses again.
    assert len(stub_prefill.read_runs(bindir)) == 1


def test_a_scheduled_first_fill_still_runs_forced(tmp_path: Path) -> None:
    """The scheduler goes through the SAME needs_force path as everything else.

    A never-filled app (``apps.needs_force`` defaults to 1) must still get
    ``--force`` when the sweep is what enqueued it — the flag belongs to the
    app, not to whoever pressed the button.
    """
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(
        bindir,
        cache_root=str(cache_root),
        depots_by_app={730: [731]},
        summary_text="Prefilled 1 apps totaling 75.97 MiB in 16.55\n\n"
        " Updated | Up To Date\n"
        "---------+------------\n"
        "    1    |      0\n",
    )
    settings = make_settings(
        tmp_path, steamprefill_path=executable, cache_root=cache_root
    )

    app = create_app(settings)
    app.state.scheduler = PrefillScheduler(
        settings, clock=lambda: local(10), tick_seconds=0.02
    )

    conn = get_connection(settings.db_path)
    try:
        insert_report(conn, "steam-deck", [730], utc_iso(9, 30))
    finally:
        conn.close()

    with TestClient(app) as client:
        wait_for(
            lambda: any(
                job["status"] == "done"
                for job in client.get("/v1/jobs", headers=AUTH).json()
            ),
            what="the scheduled first fill finishing",
        )

    assert stub_prefill.read_argv(bindir) == ["prefill", "--force", "--no-ansi"]


def test_the_scheduler_thread_does_not_start_when_no_window_is_configured(
    tmp_path: Path,
) -> None:
    """Disabled is the DEFAULT: no thread, no sweep, no jobs."""
    settings = make_settings(tmp_path, window=None)
    app = create_app(settings)
    app.state.scheduler = PrefillScheduler(
        settings, clock=lambda: local(10), tick_seconds=0.01
    )

    conn = get_connection(settings.db_path)
    try:
        insert_report(conn, "gaming-pc", [440], utc_iso(9, 30))
    finally:
        conn.close()

    with TestClient(app) as client:
        assert app.state.scheduler.enabled is False
        assert not any(
            thread.name == "vault-prefill-scheduler" and thread.is_alive()
            for thread in threading.enumerate()
        )
        # Plenty of tick intervals' worth of wall clock — nothing is queued.
        time.sleep(0.3)
        assert client.get("/v1/jobs", headers=AUTH).json() == []
        assert client.get("/v1/schedule", headers=AUTH).json()["last_sweep_at"] is None


def test_the_scheduler_thread_is_stopped_with_the_app(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    app.state.scheduler = PrefillScheduler(
        settings, clock=lambda: local(10), tick_seconds=0.02
    )

    with TestClient(app):
        wait_for(
            lambda: any(
                thread.name == "vault-prefill-scheduler" and thread.is_alive()
                for thread in threading.enumerate()
            ),
            what="the scheduler thread starting",
        )

    assert app.state.scheduler.stopping is True
    assert not any(
        thread.name == "vault-prefill-scheduler" and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_a_failing_tick_does_not_kill_the_thread(tmp_path: Path) -> None:
    """The last-resort net: a bug in a sweep must not silently end scheduling."""
    settings = make_settings(tmp_path)
    scheduler = PrefillScheduler(settings, clock=lambda: local(10), tick_seconds=0.01)
    init_db(settings.db_path)

    ticks = {"n": 0}
    original = scheduler_module.maybe_sweep

    def flaky(*args: object, **kwargs: object):
        ticks["n"] += 1
        if ticks["n"] <= 3:
            raise RuntimeError("simulated tick failure")
        return original(*args, **kwargs)  # type: ignore[arg-type]

    scheduler_module.maybe_sweep = flaky  # type: ignore[assignment]
    try:
        scheduler.start()
        wait_for(lambda: ticks["n"] > 5, what="the thread surviving failed ticks")
    finally:
        scheduler.stop()
        scheduler_module.maybe_sweep = original  # type: ignore[assignment]

    conn = get_connection(settings.db_path)
    try:
        assert read_state(conn).last_sweep_at == utc_iso(10)  # it recovered
    finally:
        conn.close()
