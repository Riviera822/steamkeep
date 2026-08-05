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
