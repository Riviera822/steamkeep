"""Job queue (plan §3: "prefill orchestration ... job queue", one job at a time).

Deliberately a table plus a handful of SQL statements — plan §9's simplicity
stance rules out Celery/RQ/APScheduler for a single-container homelab service
that runs at most one prefill at a time.

Also holds the ``apps.status`` transitions (idle -> running -> done/error),
because the job lifecycle is exactly what drives them (plan §3 "per-app status
tracking").

Concurrency model
-----------------
- N HTTP request threads may enqueue and read.
- Exactly ONE worker thread claims and executes.
- Every read-modify-write here (dedupe check + insert, claim) runs inside an
  ``BEGIN IMMEDIATE`` transaction, which takes SQLite's write lock up front,
  so two racing enqueues of the same appid cannot both insert and two claim
  attempts cannot both take the same job. Readers are unaffected (WAL).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

#: The only job type WP 1.4 creates. ``jobs.type`` exists because plan §6 also
#: lists a GC job (``POST /v1/cache/{appid}/gc``, Phase 3) that will share this
#: queue.
JOB_TYPE_PREFILL = "prefill"

#: ``apps.status`` only (never a job status): the schema default, and what
#: deleting a game from the cache resets an app to (plan §4, WP 1.6).
STATUS_IDLE = "idle"

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"

#: A job in one of these states is "in flight" for dedupe purposes.
ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_RUNNING)

#: Columns returned by the job read helpers. ``log_excerpt`` is intentionally
#: excluded from the *list* query (see ``list_jobs``). ``updated``/
#: ``up_to_date``/``summary_parse_ok`` (schema v4, WP 3.3) are SteamPrefill's
#: own summary-table counters (ADR-0006 decision 1) — small scalars, so unlike
#: ``log_excerpt`` they ARE included in the list query too.
_JOB_COLUMNS = (
    "id, appid, type, status, created_at, started_at, finished_at, log_excerpt, "
    "updated, up_to_date, summary_parse_ok"
)

#: Cap on stored log excerpts. 4 KiB is enough to show the tail of a
#: SteamPrefill run (the interesting part: the summary or the exception) while
#: keeping ``GET /v1/jobs/{id}`` responses small enough for the Android app.
LOG_EXCERPT_MAX_CHARS = 4096


def utcnow_iso() -> str:
    """Current UTC time as a second-precision ISO-8601 string ('...Z').

    All timestamps in this database are stored in this one format so plain
    string comparison sorts chronologically.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tail_excerpt(text: str, limit: int = LOG_EXCERPT_MAX_CHARS) -> str:
    """Keep the LAST ``limit`` characters — the tail is where the outcome is.

    Prefixes a marker when truncation happened so a reader isn't misled into
    thinking the run started mid-sentence.
    """
    if len(text) <= limit:
        return text
    return "[...truncated...]\n" + text[-limit:]


@contextmanager
def immediate_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Run a block inside ``BEGIN IMMEDIATE`` (write lock taken up front).

    sqlite3's default ``isolation_level=""`` opens a *deferred* transaction
    lazily on the first DML statement, which is not enough here: a deferred
    transaction that starts with a SELECT can have another writer commit
    underneath it between the SELECT and the UPDATE (the classic
    check-then-act race). ``BEGIN IMMEDIATE`` acquires the write lock before
    the SELECT, so competing writers serialize on it (and wait up to
    ``PRAGMA busy_timeout``, set to 5 s in ``db.get_connection``).

    Taking manual control requires ``isolation_level=None``; it is restored
    afterwards so the connection behaves normally for other callers (e.g.
    ``mapping.upsert_mapping``, which relies on ``conn.commit()``).
    """
    prior = conn.isolation_level
    # Assigning isolation_level commits an implicitly-open transaction; commit
    # first so that is explicit rather than a side effect.
    conn.commit()
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.isolation_level = prior


def _row_to_dict(row: sqlite3.Row) -> dict[str, object]:
    return {key: row[key] for key in row.keys()}


def ensure_app_row(conn: sqlite3.Connection, appid: int) -> None:
    """Make sure an ``apps`` row exists for ``appid`` (status 'idle').

    Called on enqueue so ``GET /v1/games/{appid}`` answers 200 the moment a
    prefill has been requested, even for an app that has never been mapped
    before (first prefill of a new title). Never touches an existing row.
    """
    conn.execute(
        "INSERT OR IGNORE INTO apps (appid, status) VALUES (?, 'idle')", (appid,)
    )


def set_app_status(
    conn: sqlite3.Connection,
    appid: int,
    status: str,
    last_prefill_at: str | None = None,
    last_manifest_check: str | None = None,
) -> None:
    """Set ``apps.status`` (plan §3: idle/running/done/error).

    ``last_prefill_at`` is only written when given — a failed run must not
    claim the app was prefilled. ``last_manifest_check`` (WP 3.3, ADR-0006
    "current as of <timestamp>" semantics) is likewise only written when
    given — the worker passes it exactly when SteamPrefill's summary reported
    ``Up To Date > 0 AND Updated == 0`` for this run, i.e. a non-forced-run
    style confirmation that the app is current as of now.

    Deliberately has **no** ``needs_force`` parameter (schema v5, WP 3.4,
    ADR-0006 decision 2) — see ``clear_needs_force_if_unchanged`` below for
    why an unconditional write here would be wrong, and its own docstring for
    the concurrent-deletion bug an earlier version of this function had.
    """
    ensure_app_row(conn, appid)
    fields = ["status = ?"]
    params: list[object] = [status]
    if last_prefill_at is not None:
        fields.append("last_prefill_at = ?")
        params.append(last_prefill_at)
    if last_manifest_check is not None:
        fields.append("last_manifest_check = ?")
        params.append(last_manifest_check)
    params.append(appid)
    conn.execute(f"UPDATE apps SET {', '.join(fields)} WHERE appid = ?", params)
    conn.commit()


def get_app_needs_force(conn: sqlite3.Connection, appid: int) -> bool:
    """Read ``apps.needs_force`` (schema v5, WP 3.4, ADR-0006 decision 2).

    Read by the worker at job start to decide whether this run must pass
    ``--force`` (see ``vault_api/prefill.py::run_prefill``'s ``use_force``
    parameter). Defaults to ``True`` when the app has no row at all — matching
    the column's own ``DEFAULT 1`` semantics ("never filled before, so force
    the first run") — even though in practice ``worker.py`` always calls this
    right after ``set_app_status`` has already ensured the row exists via
    ``ensure_app_row``; the default keeps this function safe for any other
    caller too.

    The value this returns is also what the worker must hand back to
    ``clear_needs_force_if_unchanged`` at the end of the job — see that
    function for why.
    """
    row = conn.execute(
        "SELECT needs_force FROM apps WHERE appid = ?", (appid,)
    ).fetchone()
    return True if row is None else bool(row["needs_force"])


def clear_needs_force_if_unchanged(
    conn: sqlite3.Connection, appid: int, expected_needs_force: bool
) -> bool:
    """Clear ``apps.needs_force`` to 0 — but ONLY as a compare-and-swap against
    the value read at job-claim time. Returns whether the clear applied.

    **The bug this closes (reviewer-reproduced end-to-end wedge, WP 3.4
    review).** The worker used to clear ``needs_force`` unconditionally on
    every successful job — an unconditional ``UPDATE apps SET needs_force =
    0 WHERE appid = ?``. That is a last-writer-wins race against
    ``DELETE /v1/cache/{appid}``, which is allowed to run concurrently with a
    *different* job than the one it 409-refused (the 409 guard only checks
    for an active job at the START of the DELETE request — plan §4's
    documented check-then-act window, see ``routers/cache.py``). The
    reproduced sequence:

    1. A job for this app is claimed; it reads ``needs_force`` (say ``0`` —
       not currently forced) and runs a non-forced check. SteamPrefill's own
       ``successfullyDownloadedDepots.json`` still says the depots are
       present, so it reports "up to date" — correctly, *for what was on
       disk when it started*.
    2. Concurrently, ``DELETE /v1/cache/{appid}`` removes the depot
       directories and, at the end, sets ``needs_force = 1`` (this request
       genuinely changed what is on disk — see
       ``deletion.reset_app_after_deletion``).
    3. The job finishes "successfully" (an up-to-date confirmation is a
       'done' outcome, ADR-0006 decision 1) and unconditionally clears
       ``needs_force`` back to ``0`` — clobbering step 2's ``1``.

    End state: ``apps.status = 'done'``, ``last_prefill_at`` set, the cache
    directory **empty**, and ``needs_force = 0``. There is no self-healing
    path from there: every future run goes non-forced, SteamPrefill's own
    bookkeeping keeps saying "up to date" forever (it was never told the
    depots were deleted), and the app is permanently wedged at a green badge
    over an empty cache.

    **The fix.** The worker remembers the ``needs_force`` value it read at
    claim time (``jobs.get_app_needs_force``, the same value it passed to
    ``prefill.run_prefill`` as ``use_force``) and hands it back here as
    ``expected_needs_force``. The clear is then a single atomic SQL
    statement — ``UPDATE ... WHERE appid = ? AND needs_force = ?`` — matched
    against the table's *current* value, not a value this function read
    itself: SQLite evaluates the ``WHERE`` clause and applies the write in
    one indivisible step under the write lock, so there is no separate
    read-then-write window for another connection to land in between. If
    step 2 above happened, the current value is ``1`` but
    ``expected_needs_force`` is ``0`` (what the job saw at claim time) — the
    ``WHERE`` clause matches zero rows, the clear is a no-op, and
    ``needs_force`` is correctly left at ``1``: the very next prefill for
    this app is forced, which is exactly the self-healing path that was
    missing. If nothing raced the job, the current value still equals
    ``expected_needs_force`` and the clear applies normally.

    Returns ``True`` when the clear applied (no concurrent write raced it),
    ``False`` when it was skipped because something changed the value in the
    meantime (the caller may want to log this — see ``worker.py``).
    """
    cursor = conn.execute(
        "UPDATE apps SET needs_force = 0 WHERE appid = ? AND needs_force = ?",
        (appid, int(expected_needs_force)),
    )
    conn.commit()
    return cursor.rowcount > 0


def enqueue_prefill(conn: sqlite3.Connection, appid: int) -> tuple[dict[str, object], bool]:
    """Queue a prefill job for ``appid``. Returns ``(job, created)``.

    Dedupe: if the app already has a ``queued`` or ``running`` job, that job is
    returned with ``created=False`` instead of stacking a second one. Rationale
    — the app's UI fires "prefill this game" on a button press and the Phase-3
    miss trigger (ADR-0001) will fire on cache misses, so duplicate requests
    for the same app are the normal case, not an error; running the same
    prefill twice back to back would only re-download nothing.
    """
    with immediate_transaction(conn):
        ensure_app_row(conn, appid)

        existing = conn.execute(
            f"""
            SELECT {_JOB_COLUMNS} FROM jobs
            WHERE appid = ? AND type = ? AND status IN (?, ?)
            ORDER BY id
            LIMIT 1
            """,
            (appid, JOB_TYPE_PREFILL, STATUS_QUEUED, STATUS_RUNNING),
        ).fetchone()
        if existing is not None:
            return _row_to_dict(existing), False

        cursor = conn.execute(
            """
            INSERT INTO jobs (appid, type, status, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (appid, JOB_TYPE_PREFILL, STATUS_QUEUED, utcnow_iso()),
        )
        job_id = int(cursor.lastrowid)

        row = conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return _row_to_dict(row), True


def get_job(conn: sqlite3.Connection, job_id: int) -> dict[str, object] | None:
    """One job incl. its log excerpt, or None if the id is unknown."""
    row = conn.execute(
        f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    return None if row is None else _row_to_dict(row)


def active_job_for_app(
    conn: sqlite3.Connection, appid: int
) -> dict[str, object] | None:
    """The app's oldest ``queued``/``running`` job, or None if it has none.

    Same "in flight" definition ``enqueue_prefill`` dedupes on
    (``ACTIVE_STATUSES``), exposed as a read for ``DELETE /v1/cache/{appid}``
    (WP 1.6): deleting depot directories while a prefill is writing into them
    would delete under an active download, so that endpoint refuses with 409.
    Served by ``idx_jobs_appid_status``.
    """
    row = conn.execute(
        f"""
        SELECT {_JOB_COLUMNS} FROM jobs
        WHERE appid = ? AND status IN (?, ?)
        ORDER BY id
        LIMIT 1
        """,
        (appid, STATUS_QUEUED, STATUS_RUNNING),
    ).fetchone()
    return None if row is None else _row_to_dict(row)


def list_jobs(conn: sqlite3.Connection, limit: int) -> list[dict[str, object]]:
    """Most recent jobs, newest first.

    Deliberately omits ``log_excerpt``: this endpoint is the app's polling
    surface, and 20 x 4 KiB of log text per poll is not what "small" means.
    ``GET /v1/jobs/{id}`` carries the excerpt. ``updated``/``up_to_date``/
    ``summary_parse_ok`` (schema v4, WP 3.3) ARE included here — they are
    small scalars, not multi-KB text, so the same size argument doesn't apply.
    """
    rows = conn.execute(
        """
        SELECT id, appid, type, status, created_at, started_at, finished_at,
               updated, up_to_date, summary_parse_ok
        FROM jobs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def claim_next_job(conn: sqlite3.Connection) -> dict[str, object] | None:
    """Atomically claim the oldest queued job (FIFO by id). None if empty.

    The claim is a conditional UPDATE (``WHERE id = ? AND status = 'queued'``)
    inside an immediate transaction, so it is safe even if a second worker ever
    existed: the loser's UPDATE matches zero rows and it sees ``None``.
    """
    with immediate_transaction(conn):
        row = conn.execute(
            """
            SELECT id FROM jobs
            WHERE status = ?
            ORDER BY id
            LIMIT 1
            """,
            (STATUS_QUEUED,),
        ).fetchone()
        if row is None:
            return None

        job_id = int(row["id"])
        started_at = utcnow_iso()
        cursor = conn.execute(
            """
            UPDATE jobs SET status = ?, started_at = ?
            WHERE id = ? AND status = ?
            """,
            (STATUS_RUNNING, started_at, job_id, STATUS_QUEUED),
        )
        if cursor.rowcount == 0:  # pragma: no cover - only reachable with 2 workers
            return None

        claimed = conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return _row_to_dict(claimed)


def finish_job(
    conn: sqlite3.Connection,
    job_id: int,
    status: str,
    log_excerpt: str,
    updated: int | None = None,
    up_to_date: int | None = None,
    summary_parse_ok: bool | None = None,
) -> None:
    """Mark a job ``done`` or ``error`` and store its (tail-truncated) log.

    ``updated``/``up_to_date``/``summary_parse_ok`` (schema v4, WP 3.3) are
    SteamPrefill's own summary-table counters (``vault_api.prefill_summary``,
    ADR-0006 decision 1) — left at their SQL-``NULL`` default (all three
    parameters default to ``None``) for any job that never reaches a parsed
    summary (a GC job, a failed/timed-out/aborted prefill, the crash-recovery
    path in ``recover_stale_jobs``). ``summary_parse_ok`` is stored as 0/1,
    SQLite having no native boolean.
    """
    conn.execute(
        """
        UPDATE jobs
        SET status = ?, finished_at = ?, log_excerpt = ?,
            updated = ?, up_to_date = ?, summary_parse_ok = ?
        WHERE id = ?
        """,
        (
            status,
            utcnow_iso(),
            tail_excerpt(log_excerpt),
            updated,
            up_to_date,
            None if summary_parse_ok is None else int(summary_parse_ok),
            job_id,
        ),
    )
    conn.commit()


#: Message stored on jobs recovered by ``recover_stale_jobs``.
STALE_JOB_MESSAGE = (
    "Job was still marked 'running' when vault-api started. The process that "
    "owned it died (crash, container kill, host reboot) — no worker is "
    "executing it any more, so it has been failed. Re-queue it with "
    "POST /v1/prefill if you still want that app prefilled."
)


def recover_stale_jobs(conn: sqlite3.Connection) -> int:
    """Fail every job left in ``running`` at startup. Returns how many.

    **The rule, stated plainly:** vault-api runs exactly ONE job worker inside
    exactly ONE process (plan §3). A ``running`` row can therefore only be
    owned by the current process's worker — and at startup that worker has not
    claimed anything yet. So any ``running`` row is by definition an orphan
    from a previous process that died mid-job, and is failed here rather than
    being left to sit "running" forever (which would also block the app's
    dedupe: ``POST /v1/prefill`` would keep handing out that dead job id).

    The consequence of that rule, honestly stated: **do not point two vault-api
    processes at the same database.** The second one's startup would fail the
    first one's genuinely-running job. Nothing detects that misconfiguration —
    single-process operation is the documented deployment model (one container,
    plan §3/§7), not something this code enforces. Also note a recovered job's
    SteamPrefill subprocess may briefly outlive the dead parent; it is
    harmless (it only writes into the cache) and exits on its own.

    ``apps.status`` is repaired for the same reason: an app left at 'running'
    would show a permanent yellow badge in the app.
    """
    with immediate_transaction(conn):
        rows = conn.execute(
            "SELECT id, appid FROM jobs WHERE status = ?", (STATUS_RUNNING,)
        ).fetchall()
        if not rows:
            return 0

        finished_at = utcnow_iso()
        for row in rows:
            conn.execute(
                """
                UPDATE jobs SET status = ?, finished_at = ?, log_excerpt = ?
                WHERE id = ?
                """,
                (STATUS_ERROR, finished_at, STALE_JOB_MESSAGE, int(row["id"])),
            )
            conn.execute(
                "UPDATE apps SET status = ? WHERE appid = ? AND status = ?",
                (STATUS_ERROR, int(row["appid"]), STATUS_RUNNING),
            )
        return len(rows)
