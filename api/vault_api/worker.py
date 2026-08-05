"""The single background job worker (plan §3: "one job at a time").

One ``threading.Thread`` started by the FastAPI lifespan, polling the ``jobs``
table. No Celery, no APScheduler, no second process — plan §9's simplicity
stance, and the queue only ever executes one job at a time anyway.

Lifecycle
---------
startup  ->  ``jobs.recover_stale_jobs`` (fail orphaned 'running' rows from a
             process that died mid-job — see its docstring for the rule)
         ->  thread starts, loops: claim -> execute -> repeat; sleeps
             ``VAULT_WORKER_POLL_SECONDS`` when the queue is empty
shutdown ->  stop event set; the loop exits before claiming another job, and a
             prefill subprocess currently in flight is terminated (otherwise
             ``docker stop`` would block for as long as the download takes).
             The aborted job is recorded as 'error' with a clear reason.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import traceback

from vault_api import jobs, prefill
from vault_api.config import Settings
from vault_api.db import get_connection
from vault_api.sizes import SizeCache

logger = logging.getLogger(__name__)

#: How long ``stop()`` waits for the thread to wind down. The abort path
#: terminates the subprocess, so this only needs to cover that plus the
#: terminate->kill grace period.
SHUTDOWN_JOIN_TIMEOUT_SECONDS = 30.0


class PrefillWorker:
    """Runs queued prefill jobs, strictly one at a time, in FIFO order."""

    def __init__(self, settings: Settings, size_cache: SizeCache | None = None) -> None:
        self._settings = settings
        #: Invalidated after a successful prefill job (WP 1.5: plan §3's "du
        #: over depot folders, cached" needs an explicit invalidation hook,
        #: not polling). None in tests that don't care about size caching.
        self._size_cache = size_cache
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:  # pragma: no cover - guarded by lifespan
            raise RuntimeError("worker already started")
        # daemon=True so a SIGKILLed/aborted shutdown can never wedge the
        # interpreter; stop() is still the orderly path.
        self._thread = threading.Thread(
            target=self._run, name="vault-prefill-worker", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = SHUTDOWN_JOIN_TIMEOUT_SECONDS) -> None:
        self._stop.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout=timeout)
        if thread.is_alive():  # pragma: no cover - only on an unkillable subprocess
            logger.warning(
                "Prefill worker did not stop within %.0fs; leaving it as a daemon "
                "thread. Any job still marked 'running' will be recovered to "
                "'error' on the next startup.",
                timeout,
            )

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    # -- loop --------------------------------------------------------------

    def _run(self) -> None:
        conn = get_connection(self._settings.db_path)
        try:
            while not self._stop.is_set():
                try:
                    job = jobs.claim_next_job(conn)
                except sqlite3.Error:
                    # A locked/failed claim must not kill the worker; the job
                    # stays 'queued' and the next tick retries.
                    logger.exception("Failed to claim a job; retrying after a poll")
                    self._stop.wait(self._settings.worker_poll_seconds)
                    continue

                if job is None:
                    self._stop.wait(self._settings.worker_poll_seconds)
                    continue

                self._execute(conn, job)
        finally:
            conn.close()

    def _execute(self, conn: sqlite3.Connection, job: dict[str, object]) -> None:
        job_id = int(job["id"])  # type: ignore[arg-type]
        appid = int(job["appid"])  # type: ignore[arg-type]
        logger.info("Starting prefill job %s for appid %s", job_id, appid)

        try:
            jobs.set_app_status(conn, appid, jobs.STATUS_RUNNING)

            before = prefill.scan_depots(self._settings.cache_root)
            result = prefill.run_prefill(
                appid=appid,
                steamprefill_path=self._settings.steamprefill_path,
                timeout_seconds=self._settings.prefill_timeout_seconds,
                should_abort=self._stop.is_set,
            )

            log_parts = [result.output]

            if result.success:
                after = prefill.scan_depots(self._settings.cache_root)
                observed = prefill.diff_depots(before, after)
                change = prefill.apply_observed_mapping(conn, appid, observed)
                log_parts.append(change.summary())

                jobs.set_app_status(
                    conn, appid, jobs.STATUS_DONE, last_prefill_at=jobs.utcnow_iso()
                )
                jobs.finish_job(conn, job_id, jobs.STATUS_DONE, "\n".join(log_parts))

                # Disk content just changed (plan §3: size calculation is
                # "cached" — explicit invalidation, not polling). A prefill
                # that observed nothing still ran --force and may have
                # rewritten existing chunks, so invalidate unconditionally
                # rather than only when `observed` is non-empty.
                if self._size_cache is not None:
                    self._size_cache.invalidate()

                logger.info(
                    "Prefill job %s for appid %s done (%d depots observed)",
                    job_id, appid, len(observed),
                )
                return

            # Failure: the mapping is deliberately NOT touched. A partial or
            # aborted run is no evidence about which depots belong to the app,
            # and replace-semantics on bad evidence would delete good rows.
            log_parts.append(
                "[vault-api] Prefill failed "
                f"(reason={result.failure_reason}); the depot mapping for this app "
                "was left unchanged."
            )
            jobs.set_app_status(conn, appid, jobs.STATUS_ERROR)
            jobs.finish_job(conn, job_id, jobs.STATUS_ERROR, "\n".join(log_parts))
            logger.warning(
                "Prefill job %s for appid %s failed (%s)",
                job_id, appid, result.failure_reason,
            )
        except Exception:
            # Last-resort net: the worker thread must survive any bug in the
            # job body, otherwise the queue silently stops draining.
            logger.exception("Prefill job %s crashed", job_id)
            message = (
                "[vault-api] Internal error while running this job:\n"
                + traceback.format_exc()
            )
            try:
                jobs.set_app_status(conn, appid, jobs.STATUS_ERROR)
                jobs.finish_job(conn, job_id, jobs.STATUS_ERROR, message)
            except Exception:  # pragma: no cover - DB itself is broken
                logger.exception("Could not even record the failure of job %s", job_id)
