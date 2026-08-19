"""WP S-1 (ADR-0012), design constraint 1: "two writers, one SQLite file,
two containers, same host volume."

This is verified here with two REAL, SEPARATE OS PROCESSES (``multiprocessing``,
which uses ``spawn`` on Windows — a genuinely separate Python interpreter, not
a thread) writing to the SAME on-disk database file, each through its own
``vault_api.db.get_connection`` (WAL + ``busy_timeout=5000``, unchanged by
this work package). Two Docker containers on ONE host sharing a bind-mounted
or named volume reduce to exactly this: a named volume is a directory on the
host filesystem, and a bind mount is one by definition — "two containers,
same host volume" is filesystem-equivalent to "two processes, one file path,
one real local filesystem", which is what this test exercises directly,
without needing Docker to prove the SQLite-level claim.

**What this does NOT verify**: WAL over a NETWORK filesystem (NFS/SMB) is a
documented SQLite footgun (no reliable shared-memory mmap) — irrelevant here
because deploy/compose.yaml's volumes are host-local (named volume or a local
bind mount), never a network share, and changing that is out of this WP's
footprint (no deploy/ changes) and would be its own decision to flag.
"""

from __future__ import annotations

import multiprocessing
import sqlite3
import time

from vault_api.db import get_connection, init_db

#: Kept modest: this test's job is to prove correctness (no lost updates, no
#: corruption) and report real contention behaviour, not to benchmark SQLite.
_INCREMENTS_PER_PROCESS = 300


def _increment_loop(db_path: str, increments: int, result_queue: "multiprocessing.Queue") -> None:
    """Run in a SEPARATE OS process. Read-modify-write ``counter.value`` under
    ``BEGIN IMMEDIATE`` — the exact pattern ``vault_api.jobs.immediate_transaction``
    encodes — retrying on ``database is locked`` only as a belt-and-suspenders
    guard (``busy_timeout=5000`` is expected to absorb ordinary contention
    without ever raising it; a retry that actually fires is reported, not
    hidden, via the ``locked_retries`` counter placed on the result queue).
    """
    conn = get_connection(db_path)
    locked_retries = 0
    try:
        for _ in range(increments):
            while True:
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute("SELECT value FROM counter WHERE id = 1").fetchone()
                    conn.execute(
                        "UPDATE counter SET value = ? WHERE id = 1", (row[0] + 1,)
                    )
                    conn.execute("COMMIT")
                    break
                except sqlite3.OperationalError as exc:
                    conn.execute("ROLLBACK")
                    if "locked" not in str(exc).lower():
                        raise
                    locked_retries += 1
                    time.sleep(0.01)
        result_queue.put(("ok", locked_retries))
    except Exception as exc:  # pragma: no cover - failure path only
        result_queue.put(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        conn.close()


def test_two_real_processes_increment_the_same_sqlite_file_under_wal(tmp_path) -> None:
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    setup_conn = get_connection(db_path)
    try:
        setup_conn.execute(
            "CREATE TABLE counter (id INTEGER PRIMARY KEY CHECK (id = 1), value INTEGER NOT NULL)"
        )
        setup_conn.execute("INSERT INTO counter (id, value) VALUES (1, 0)")
        setup_conn.commit()

        journal_mode = setup_conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        setup_conn.close()

    # WP S-1 design constraint 1: confirm the journal mode actually in effect
    # on THIS filesystem before trusting the rest of the test — a silent WAL
    # fallback (e.g. a filesystem without shared-memory mmap support) would
    # make everything below still pass for the wrong reason.
    assert journal_mode.lower() == "wal", (
        f"expected WAL, got {journal_mode!r} — get_connection's PRAGMA did not "
        "take effect on this filesystem; the two-writer claim below would be "
        "resting on the wrong journal mode"
    )

    result_queue: "multiprocessing.Queue" = multiprocessing.Queue()
    processes = [
        multiprocessing.Process(
            target=_increment_loop,
            args=(db_path, _INCREMENTS_PER_PROCESS, result_queue),
        )
        for _ in range(2)
    ]

    started = time.monotonic()
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
    elapsed = time.monotonic() - started

    assert all(not process.is_alive() for process in processes), "a writer process hung"

    outcomes = [result_queue.get(timeout=5) for _ in processes]
    errors = [detail for status, detail in outcomes if status == "error"]
    assert not errors, f"a writer process raised: {errors}"

    total_locked_retries = sum(detail for status, detail in outcomes if status == "ok")

    verify_conn = get_connection(db_path)
    try:
        (final_value,) = verify_conn.execute("SELECT value FROM counter WHERE id = 1").fetchone()
        integrity = verify_conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        verify_conn.close()

    expected_total = 2 * _INCREMENTS_PER_PROCESS
    assert final_value == expected_total, (
        f"lost update under contention: expected {expected_total}, got {final_value} "
        f"(each process's own increments would only under-count if BEGIN IMMEDIATE's "
        f"write lock was not actually exclusive)"
    )
    assert integrity == "ok"

    # Not an assertion — a measured report per this package's brief ("measured
    # where possible, not asserted"). Printed so it shows up with `pytest -s`;
    # the correctness assertions above are what the test actually enforces.
    print(
        f"\n[WAL contention] {expected_total} increments across 2 real OS "
        f"processes in {elapsed:.2f}s; sqlite3.OperationalError('database is "
        f"locked') observed and retried {total_locked_retries} time(s) total "
        f"(busy_timeout=5000ms absorbed the rest silently)."
    )


if __name__ == "__main__":
    # Windows 'spawn' re-imports this module in the child process; importing
    # it must never itself start a second test run. pytest's own module
    # import does not execute this guard, so it is a no-op there — kept for
    # anyone running this file directly with `python -m`.
    pass
