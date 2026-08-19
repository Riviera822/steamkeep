"""WP S-1 (ADR-0012): ``vault_api.prefill_queue`` — the encode/decode helpers
and ``await_run_result``, vault-api's half of the queue-mode wait loop.

No real SteamPrefill subprocess and no separate runner PROCESS here (that is
``tests/test_prefill_runner_process.py``'s job) — a background THREAD plays
the runner's role by calling the exact same ``jobs`` functions a real
``prefill_runner`` would, so these tests stay fast while still exercising a
real wait/poll race rather than a canned return value.
"""

from __future__ import annotations

import threading
import time

from vault_api import jobs, prefill_queue
from vault_api.db import get_connection, init_db
from vault_api.prefill import PrefillResult


def _make_running_prefill_job(conn, appid: int = 440) -> int:
    jobs.enqueue_prefill(conn, appid)
    claimed = jobs.claim_next_job(conn)
    assert claimed is not None
    return int(claimed["id"])


# -- encode/decode round trips ----------------------------------------------


def test_result_round_trips_a_success() -> None:
    result = PrefillResult(True, None, 0, "Prefill complete!\nUpdated=0 Up To Date=1")

    decoded = prefill_queue.decode_result(prefill_queue.encode_result(result))

    assert decoded == result


def test_result_round_trips_a_failure_with_no_exit_code() -> None:
    result = PrefillResult(False, "timeout", None, "[vault-api] exceeded the time budget")

    decoded = prefill_queue.decode_result(prefill_queue.encode_result(result))

    assert decoded == result


def test_signatures_round_trip_int_keys_and_tuple_values() -> None:
    signatures = {441: (3, 12345, 999), 730: (0, 0, 0)}

    decoded = prefill_queue.decode_signatures(prefill_queue.encode_signatures(signatures))

    assert decoded == signatures
    assert all(isinstance(depotid, int) for depotid in decoded)


def test_signatures_decode_none_and_blank_as_empty() -> None:
    assert prefill_queue.decode_signatures(None) == {}
    assert prefill_queue.decode_signatures("") == {}


# -- await_run_result ---------------------------------------------------------


def test_await_run_result_returns_immediately_once_already_completed(tmp_path) -> None:
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        job_id = _make_running_prefill_job(conn)
        jobs.handoff_run(conn, job_id, True, "{}")
        jobs.claim_run(conn, "runner-a")
        jobs.record_run_result(
            conn, job_id, "runner-a", prefill_queue.encode_result(PrefillResult(True, None, 0, "ok"))
        )

        outcome = prefill_queue.await_run_result(
            conn, job_id,
            lease_timeout_seconds=30, poll_seconds=0.05, should_abort=lambda: False,
        )
    finally:
        conn.close()

    assert outcome is not None
    result, before = outcome
    assert result.success is True
    assert before == {}


def test_await_run_result_picks_up_a_result_written_concurrently_by_another_connection(
    tmp_path,
) -> None:
    """A real race: a background thread (standing in for prefill_runner, on
    its OWN connection) writes the result ~150ms after the wait starts;
    the waiting call — polling on a SEPARATE connection every 20ms — must
    observe it well within the test's own timeout."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    setup_conn = get_connection(db_path)
    try:
        job_id = _make_running_prefill_job(setup_conn)
        jobs.handoff_run(setup_conn, job_id, True, "{}")
        jobs.claim_run(setup_conn, "runner-a")
    finally:
        setup_conn.close()

    def deliver_late() -> None:
        time.sleep(0.15)
        writer_conn = get_connection(db_path)
        try:
            jobs.record_run_result(
                writer_conn, job_id, "runner-a",
                prefill_queue.encode_result(PrefillResult(True, None, 0, "late result")),
            )
        finally:
            writer_conn.close()

    thread = threading.Thread(target=deliver_late)
    thread.start()

    reader_conn = get_connection(db_path)
    try:
        started = time.monotonic()
        outcome = prefill_queue.await_run_result(
            reader_conn, job_id,
            lease_timeout_seconds=30, poll_seconds=0.02, should_abort=lambda: False,
        )
        elapsed = time.monotonic() - started
    finally:
        reader_conn.close()
        thread.join(timeout=5)

    assert outcome is not None
    result, _before = outcome
    assert result.output == "late result"
    assert elapsed < 5, elapsed


def test_await_run_result_detects_a_stale_lease_and_returns_runner_lost(tmp_path) -> None:
    """WP S-1 round-2 review, S5: this test's own correctness depends on
    ``run_is_stale`` eventually returning ``True``. A broken (fail-open)
    version of it makes ``await_run_result``'s ``while True`` loop spin
    forever — measured, at module scope: an earlier version of this test
    (a bare, unbounded call) hung for the FULL 5-minute default pytest
    timeout under exactly that mutation; at full-suite scope, the same
    mutation froze the ENTIRE run with no summary line at all, mid
    progress-dots, until an external 300s timeout killed it — a hang, not a
    red build. So the call is wrapped in its OWN background thread with a
    hard ``join(timeout=...)`` ceiling: the fail-open direction now fails
    THIS test loudly and quickly instead of wedging the process.
    """
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    setup_conn = get_connection(db_path)
    try:
        job_id = _make_running_prefill_job(setup_conn)
        jobs.handoff_run(setup_conn, job_id, True, "{}")
        jobs.claim_run(setup_conn, "runner-a")
        # Force the claim into the past instead of sleeping past a real
        # lease window — deterministic and fast.
        setup_conn.execute(
            "UPDATE jobs SET run_claimed_at = ?, run_heartbeat_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00Z", "2000-01-01T00:00:00Z", job_id),
        )
        setup_conn.commit()
    finally:
        setup_conn.close()

    outcome_box: list[object] = []

    def call() -> None:
        # A fresh connection, not the setup one above: sqlite3 connections
        # are thread-confined in this codebase (db.get_connection's own
        # docstring), and this call runs on a DIFFERENT thread than setup.
        wait_conn = get_connection(db_path)
        try:
            outcome_box.append(
                prefill_queue.await_run_result(
                    wait_conn, job_id,
                    lease_timeout_seconds=1, poll_seconds=0.02, should_abort=lambda: False,
                )
            )
        finally:
            wait_conn.close()

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive(), (
        "await_run_result did not return within 5s under a genuinely stale "
        "lease -- the fail-open direction of a broken staleness check must "
        "FAIL this test loudly, not hang it (WP S-1 round-2 S5)."
    )
    assert outcome_box, "the wait thread exited without recording an outcome"
    outcome = outcome_box[0]

    assert outcome is not None
    result, _before = outcome
    assert result.success is False
    assert result.failure_reason == prefill_queue.FAILURE_RUNNER_LOST


def test_await_run_result_returns_none_when_should_abort_fires_first(tmp_path) -> None:
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        job_id = _make_running_prefill_job(conn)
        jobs.handoff_run(conn, job_id, True, "{}")
        # Deliberately never claimed/completed: should_abort must win before
        # any staleness check would even have a chance to fire.

        outcome = prefill_queue.await_run_result(
            conn, job_id,
            lease_timeout_seconds=9999, poll_seconds=0.02, should_abort=lambda: True,
        )

        assert outcome is None
        # The job itself must be untouched — still 'running', still handed
        # off — so a future reattach (jobs.find_active_run) can resume
        # waiting on it.
        row = jobs.get_job(conn, job_id)
        assert row is not None and row["status"] == jobs.STATUS_RUNNING
        run_row = jobs.get_run_row(conn, job_id)
        assert run_row is not None and run_row["run_use_force"] == 1
    finally:
        conn.close()
