"""Queue mechanics at the DB level: claiming, transitions, crash recovery."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from vault_api import jobs
from vault_api.db import get_connection, init_db


@pytest.fixture
def db_path(tmp_path) -> str:
    path = str(tmp_path / "vault.db")
    init_db(path)
    return path


@pytest.fixture
def conn(db_path: str) -> sqlite3.Connection:
    connection = get_connection(db_path)
    yield connection
    connection.close()


def test_enqueue_then_claim_marks_running_with_started_at(conn) -> None:
    job, created = jobs.enqueue_prefill(conn, 440)
    assert created is True
    assert job["status"] == jobs.STATUS_QUEUED
    assert job["type"] == jobs.JOB_TYPE_PREFILL
    assert job["started_at"] is None

    claimed = jobs.claim_next_job(conn)
    assert claimed is not None
    assert claimed["id"] == job["id"]
    assert claimed["status"] == jobs.STATUS_RUNNING
    assert claimed["started_at"] is not None


def test_claim_is_fifo_and_returns_none_when_drained(conn) -> None:
    first, _ = jobs.enqueue_prefill(conn, 10)
    second, _ = jobs.enqueue_prefill(conn, 20)

    assert jobs.claim_next_job(conn)["id"] == first["id"]
    assert jobs.claim_next_job(conn)["id"] == second["id"]
    assert jobs.claim_next_job(conn) is None


def test_enqueue_dedupes_against_a_running_job(conn) -> None:
    job, _ = jobs.enqueue_prefill(conn, 440)
    jobs.claim_next_job(conn)

    same, created = jobs.enqueue_prefill(conn, 440)
    assert created is False
    assert same["id"] == job["id"]
    assert same["status"] == jobs.STATUS_RUNNING


def test_finish_job_records_status_timestamp_and_log(conn) -> None:
    job, _ = jobs.enqueue_prefill(conn, 440)
    jobs.claim_next_job(conn)
    jobs.finish_job(conn, int(job["id"]), jobs.STATUS_DONE, "all good")

    stored = jobs.get_job(conn, int(job["id"]))
    assert stored["status"] == jobs.STATUS_DONE
    assert stored["finished_at"] is not None
    assert stored["log_excerpt"] == "all good"
    # WP 3.3 additive columns default to SQL NULL when not passed.
    assert stored["updated"] is None
    assert stored["up_to_date"] is None
    assert stored["summary_parse_ok"] is None


def test_finish_job_stores_the_summary_counters(conn) -> None:
    job, _ = jobs.enqueue_prefill(conn, 440)
    jobs.claim_next_job(conn)
    jobs.finish_job(
        conn, int(job["id"]), jobs.STATUS_DONE, "ok",
        updated=3, up_to_date=5, summary_parse_ok=True,
    )

    stored = jobs.get_job(conn, int(job["id"]))
    assert stored["updated"] == 3
    assert stored["up_to_date"] == 5
    # Stored/read back as 0/1 (no native SQLite boolean) but the row-to-dict
    # helper does not coerce it — the API layer's Pydantic model does that.
    assert stored["summary_parse_ok"] == 1


def test_finish_job_stores_summary_parse_ok_false_distinctly_from_null(conn) -> None:
    """False (parse attempted, failed) must not collapse into None (never
    attempted) -- distinct on-disk values."""
    job, _ = jobs.enqueue_prefill(conn, 440)
    jobs.claim_next_job(conn)
    jobs.finish_job(
        conn, int(job["id"]), jobs.STATUS_DONE, "ok",
        summary_parse_ok=False,
    )

    stored = jobs.get_job(conn, int(job["id"]))
    assert stored["summary_parse_ok"] == 0
    assert stored["summary_parse_ok"] is not None


def test_set_app_status_writes_last_manifest_check_only_when_given(conn) -> None:
    jobs.set_app_status(conn, 440, jobs.STATUS_DONE, last_manifest_check="2026-08-06T00:00:00Z")
    row = conn.execute("SELECT last_manifest_check FROM apps WHERE appid = 440").fetchone()
    assert row["last_manifest_check"] == "2026-08-06T00:00:00Z"

    # A later call that doesn't pass it must leave the previous value alone.
    jobs.set_app_status(conn, 440, jobs.STATUS_RUNNING)
    row = conn.execute("SELECT last_manifest_check FROM apps WHERE appid = 440").fetchone()
    assert row["last_manifest_check"] == "2026-08-06T00:00:00Z"


def test_set_app_status_can_write_both_timestamps_together(conn) -> None:
    jobs.set_app_status(
        conn, 440, jobs.STATUS_DONE,
        last_prefill_at="2026-08-06T01:00:00Z",
        last_manifest_check="2026-08-06T01:00:00Z",
    )
    row = conn.execute(
        "SELECT last_prefill_at, last_manifest_check FROM apps WHERE appid = 440"
    ).fetchone()
    assert row["last_prefill_at"] == "2026-08-06T01:00:00Z"
    assert row["last_manifest_check"] == "2026-08-06T01:00:00Z"


def test_set_app_status_has_no_needs_force_parameter(conn) -> None:
    # WP 3.4 review fix: an unconditional needs_force write here was the
    # concurrent-deletion wedge -- set_app_status must not offer that footgun
    # at all. clear_needs_force_if_unchanged (CAS) is the only writer that
    # clears it; deletion.py's raw UPDATE is the only writer that sets it.
    jobs.set_app_status(conn, 440, jobs.STATUS_RUNNING)
    row = conn.execute("SELECT needs_force FROM apps WHERE appid = 440").fetchone()
    # A brand-new app defaults to needs_force=1 (schema default, WP 3.4);
    # set_app_status must not have touched it either way.
    assert row["needs_force"] == 1

    import inspect

    assert "needs_force" not in inspect.signature(jobs.set_app_status).parameters


def test_get_app_needs_force_defaults_true_for_a_never_seen_app(conn) -> None:
    # No apps row at all yet -- matches the schema column's own DEFAULT 1.
    assert jobs.get_app_needs_force(conn, 999) is True


def test_get_app_needs_force_reflects_the_stored_value(conn) -> None:
    jobs.ensure_app_row(conn, 440)
    conn.commit()
    assert jobs.get_app_needs_force(conn, 440) is True

    assert jobs.clear_needs_force_if_unchanged(conn, 440, expected_needs_force=True)
    assert jobs.get_app_needs_force(conn, 440) is False


# -- clear_needs_force_if_unchanged (WP 3.4 review fix: CAS, not last-writer-wins) --


def test_clear_needs_force_if_unchanged_applies_when_nothing_raced_it(conn) -> None:
    jobs.ensure_app_row(conn, 440)
    conn.commit()
    assert jobs.get_app_needs_force(conn, 440) is True

    applied = jobs.clear_needs_force_if_unchanged(conn, 440, expected_needs_force=True)

    assert applied is True
    assert jobs.get_app_needs_force(conn, 440) is False


def test_clear_needs_force_if_unchanged_is_a_noop_when_the_value_changed(conn) -> None:
    """The exact reviewer-reproduced sequence: a job read needs_force=0 at
    claim time (use_force=False), and -- while the job ran -- something else
    (a DELETE, in production) set it to 1. The job's end-of-run clear must
    NOT clobber that 1 back to 0, or the app wedges at 'done' over a cache
    that was actually just emptied, with no self-healing path (every future
    run stays non-forced forever)."""
    jobs.ensure_app_row(conn, 440)
    conn.commit()
    # Simulate: needs_force was 0 when the (simulated) job claimed it...
    conn.execute("UPDATE apps SET needs_force = 0 WHERE appid = 440")
    conn.commit()
    use_force_at_claim_time = jobs.get_app_needs_force(conn, 440)
    assert use_force_at_claim_time is False

    # ...then, while the job is "running", a concurrent DELETE sets it to 1.
    conn.execute("UPDATE apps SET needs_force = 1 WHERE appid = 440")
    conn.commit()

    # The job now finishes "successfully" and tries to clear the flag using
    # the STALE value it read at claim time.
    applied = jobs.clear_needs_force_if_unchanged(
        conn, 440, expected_needs_force=use_force_at_claim_time
    )

    assert applied is False
    # The DELETE's 1 must survive -- this is the whole fix.
    assert jobs.get_app_needs_force(conn, 440) is True


def test_clear_needs_force_if_unchanged_is_atomic_against_a_racing_writer(
    conn, db_path
) -> None:
    """The same scenario as the sequential test above, but through two real
    connections hitting the database concurrently rather than same-connection
    UPDATEs one after another -- proves the compare-and-swap really is a
    single atomic SQL statement (SQLite's own write lock serializes the two
    connections; there is no Python-level read-then-write window for the
    other connection to land in), not something that merely happens to work
    because nothing else touched the row in the sequential test above.

    Deterministic regardless of which thread's statement SQLite serializes
    first: the racing writer's `UPDATE ... SET needs_force = 1` is
    unconditional, so needs_force ends at 1 either way -- if it runs BEFORE
    the CAS, the CAS's `WHERE needs_force = 0` no longer matches (a correct
    no-op); if it runs AFTER, it simply overwrites the CAS's 0. What must
    NEVER happen is the CAS reporting `applied=True` while a 1 the racing
    writer set is silently still there un-detected, or any exception/deadlock
    from the two connections colliding.
    """
    jobs.ensure_app_row(conn, 550)
    conn.execute("UPDATE apps SET needs_force = 0 WHERE appid = 550")
    conn.commit()

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def racing_delete() -> None:
        delete_conn = get_connection(db_path)
        try:
            barrier.wait(timeout=10)
            # The "DELETE sets needs_force=1" side of the race.
            delete_conn.execute(
                "UPDATE apps SET needs_force = 1 WHERE appid = 550"
            )
            delete_conn.commit()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            delete_conn.close()

    def racing_clear() -> None:
        worker_conn = get_connection(db_path)
        try:
            barrier.wait(timeout=10)
            jobs.clear_needs_force_if_unchanged(
                worker_conn, 550, expected_needs_force=False
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            worker_conn.close()

    threads = [threading.Thread(target=racing_delete), threading.Thread(target=racing_clear)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, errors
    # Deterministic outcome regardless of interleaving (see docstring): the
    # racing writer's unconditional 1 always ends up as the final value.
    final = conn.execute("SELECT needs_force FROM apps WHERE appid = 550").fetchone()
    assert final["needs_force"] == 1


def test_log_excerpt_keeps_the_tail_and_is_capped(conn) -> None:
    job, _ = jobs.enqueue_prefill(conn, 440)
    long_log = "".join(f"line {index}\n" for index in range(5000))
    assert len(long_log) > jobs.LOG_EXCERPT_MAX_CHARS

    jobs.finish_job(conn, int(job["id"]), jobs.STATUS_ERROR, long_log + "THE-END")

    stored = jobs.get_job(conn, int(job["id"]))
    excerpt = stored["log_excerpt"]
    assert len(excerpt) <= jobs.LOG_EXCERPT_MAX_CHARS + len("[...truncated...]\n")
    assert excerpt.endswith("THE-END")  # tail, not head
    assert excerpt.startswith("[...truncated...]")
    assert "line 0\n" not in excerpt


def test_list_jobs_is_newest_first_and_omits_the_log(conn) -> None:
    for appid in (10, 20, 30):
        jobs.enqueue_prefill(conn, appid)

    listed = jobs.list_jobs(conn, limit=2)
    assert [row["appid"] for row in listed] == [30, 20]
    assert "log_excerpt" not in listed[0]


# -- crash recovery --------------------------------------------------------


def test_recover_stale_jobs_fails_running_jobs_and_repairs_app_status(conn) -> None:
    stale, _ = jobs.enqueue_prefill(conn, 440)
    jobs.claim_next_job(conn)
    jobs.set_app_status(conn, 440, jobs.STATUS_RUNNING)
    queued, _ = jobs.enqueue_prefill(conn, 730)  # must be left alone

    assert jobs.recover_stale_jobs(conn) == 1

    recovered = jobs.get_job(conn, int(stale["id"]))
    assert recovered["status"] == jobs.STATUS_ERROR
    assert recovered["finished_at"] is not None
    assert "still marked 'running'" in recovered["log_excerpt"]

    app_status = conn.execute(
        "SELECT status FROM apps WHERE appid = 440"
    ).fetchone()["status"]
    assert app_status == jobs.STATUS_ERROR

    assert jobs.get_job(conn, int(queued["id"]))["status"] == jobs.STATUS_QUEUED


def test_recover_stale_jobs_is_a_noop_on_a_clean_queue(conn) -> None:
    jobs.enqueue_prefill(conn, 440)
    assert jobs.recover_stale_jobs(conn) == 0


def test_recovered_job_no_longer_blocks_a_new_enqueue(conn) -> None:
    # The point of recovery: a dead 'running' row would otherwise keep the
    # dedupe rule handing out a job id nothing is executing.
    old, _ = jobs.enqueue_prefill(conn, 440)
    jobs.claim_next_job(conn)
    jobs.recover_stale_jobs(conn)

    fresh, created = jobs.enqueue_prefill(conn, 440)
    assert created is True
    assert fresh["id"] != old["id"]


# -- concurrency -----------------------------------------------------------


def test_parallel_claims_never_hand_out_the_same_job_twice(db_path: str) -> None:
    """The claim must be atomic even with several threads racing for it.

    Each thread gets its own connection (as the real worker would) and claims
    until the queue is empty; every job id must be claimed exactly once.
    """
    writer = get_connection(db_path)
    try:
        for appid in range(1, 41):
            jobs.enqueue_prefill(writer, appid)
    finally:
        writer.close()

    claimed: list[int] = []
    lock = threading.Lock()
    errors: list[BaseException] = []
    start = threading.Barrier(6)

    def worker() -> None:
        connection = get_connection(db_path)
        try:
            start.wait(timeout=10)
            while True:
                job = jobs.claim_next_job(connection)
                if job is None:
                    return
                with lock:
                    claimed.append(int(job["id"]))
        except BaseException as exc:  # noqa: BLE001 - surfaced via assert below
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert not any(thread.is_alive() for thread in threads)
    assert len(claimed) == 40
    assert len(set(claimed)) == 40


def test_parallel_enqueues_of_one_app_create_exactly_one_job(db_path: str) -> None:
    """Dedupe must hold under a race, not just sequentially."""
    results: list[int] = []
    lock = threading.Lock()
    errors: list[BaseException] = []
    start = threading.Barrier(8)

    def enqueue() -> None:
        connection = get_connection(db_path)
        try:
            start.wait(timeout=10)
            job, _created = jobs.enqueue_prefill(connection, 440)
            with lock:
                results.append(int(job["id"]))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=enqueue) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert len(results) == 8
    assert len(set(results)) == 1, results

    conn = get_connection(db_path)
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
    finally:
        conn.close()
    assert count == 1


def test_immediate_transaction_rolls_back_and_restores_isolation_level(conn) -> None:
    prior = conn.isolation_level

    with pytest.raises(RuntimeError):
        with jobs.immediate_transaction(conn):
            conn.execute(
                "INSERT INTO jobs (appid, type, status, created_at) VALUES (1, 'prefill', 'queued', 'x')"
            )
            raise RuntimeError("boom")

    assert conn.isolation_level == prior
    (count,) = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
    assert count == 0
