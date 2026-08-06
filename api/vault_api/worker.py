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

from vault_api import jobs, manifest_ingest, prefill, prefill_summary
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

            # WP 3.4 / ADR-0006 decision 2: read BEFORE running, right after
            # set_app_status above has already ensured the apps row exists.
            # --force is reserved for first fills and post-deletion refills
            # (needs_force set by the deletion path, see deletion.py); every
            # other run is a genuinely cheap non-forced staleness check.
            use_force = jobs.get_app_needs_force(conn, appid)

            before = prefill.scan_depots(self._settings.cache_root)
            result = prefill.run_prefill(
                appid=appid,
                steamprefill_path=self._settings.steamprefill_path,
                timeout_seconds=self._settings.prefill_timeout_seconds,
                should_abort=self._stop.is_set,
                use_force=use_force,
            )

            log_parts = [result.output]

            if result.success:
                # WP 3.3 / ADR-0006 decision 1, reordered ahead of the
                # mapping/manifest mutation below (review S2 fix): SteamPrefill
                # exiting 0 does NOT mean it did anything for this app -- parse
                # its own summary table and let THAT decide the job outcome
                # FIRST, before anything on disk gets attributed to it. Doing
                # this after apply_observed_mapping/ingest_after_prefill left
                # an error-outcome job's mapping/depot_manifests state mutated
                # from evidence that (by the unowned branch's own definition)
                # cannot belong to this app -- an app that was never
                # considered has no depots and no manifests to attribute,
                # planted-or-not. See prefill_summary.py's docstring for the
                # evidenced reason a table like this exists at all: an
                # unowned app exits 0 with a "Prefilled 0 apps" /
                # Updated=0, Up To Date=0 summary (the WP 1.7 job-outcome
                # trap this whole package closes).
                summary = prefill_summary.parse_summary(result.output)

                if not summary.parse_ok:
                    logger.warning(
                        "Could not parse SteamPrefill's summary table for "
                        "appid %s (job %s); job outcome falls back to the "
                        "exit-code rule (process exited 0 -> 'done').",
                        appid, job_id,
                    )
                    log_parts.append(
                        "[vault-api] Could not parse SteamPrefill's summary "
                        "table; job outcome follows the exit-code rule only "
                        "(see api/README.md's job-outcome table)."
                    )
                else:
                    log_parts.append(
                        "[vault-api] Prefill summary: updated="
                        f"{summary.updated} up_to_date={summary.up_to_date}"
                        + (
                            f" (totaling {summary.total_bytes_text})"
                            if summary.total_bytes_text
                            else ""
                        )
                    )

                unowned = (
                    summary.parse_ok
                    and summary.updated == 0
                    and summary.up_to_date == 0
                )

                if unowned:
                    # Updated==0 AND Up To Date==0 means SteamPrefill never
                    # actually considered this app — reporting that as a
                    # successful prefill is precisely the trap above. The job
                    # ends 'error'; apps.status follows it to 'error' too
                    # (deliberately NOT 'done' — this run accomplished
                    # nothing for the app; NOT left at 'running' or reset to
                    # 'idle' either, since a run did execute, it just found
                    # nothing to do, and 'error' is the status this file
                    # already uses elsewhere for "a run happened and produced
                    # no usable outcome"). Neither last_prefill_at nor
                    # last_manifest_check are touched — nothing was prefilled
                    # or confirmed current. The depot mapping AND manifest
                    # state (depot_manifests) are likewise deliberately left
                    # untouched — scan_depots/apply_observed_mapping/
                    # ingest_after_prefill below never run for this branch
                    # (review S2), even if a depot directory or a stray
                    # SteamPrefill .bin file happens to exist on disk: an app
                    # SteamPrefill never considered has nothing that can
                    # honestly be attributed to it.
                    log_parts.append(
                        "[vault-api] SteamPrefill did not consider this app "
                        "- is it owned by the logged-in account? Depot "
                        "mapping and manifest state were NOT touched."
                    )
                    jobs.set_app_status(conn, appid, jobs.STATUS_ERROR)
                    jobs.finish_job(
                        conn, job_id, jobs.STATUS_ERROR, "\n".join(log_parts),
                        updated=summary.updated,
                        up_to_date=summary.up_to_date,
                        summary_parse_ok=summary.parse_ok,
                    )
                    logger.warning(
                        "Prefill job %s for appid %s ended 'error': "
                        "SteamPrefill reported Updated=0 and Up To Date=0 "
                        "(app not considered).",
                        job_id, appid,
                    )
                    return

                after = prefill.scan_depots(self._settings.cache_root)
                observed = prefill.diff_depots(before, after)
                change = prefill.apply_observed_mapping(conn, appid, observed)
                log_parts.append(change.summary())

                # WP 3.2: learn what SteamPrefill just wrote (manifest state
                # for staleness/GC, ADR-0006/0007) from its temp-cache .bin
                # files. Wrapped in its own try/except, deliberately LOCAL to
                # this block: the job has already succeeded at this point,
                # and a bug in ingestion must never flip a genuinely
                # successful prefill to 'error' (the outer except below would
                # do exactly that if this were allowed to propagate).
                try:
                    ingest_result = manifest_ingest.ingest_after_prefill(
                        conn, appid=appid, settings=self._settings
                    )
                    log_parts.append(ingest_result.summary())
                except Exception:
                    logger.exception(
                        "Manifest ingestion crashed for appid %s (job %s); the "
                        "prefill job itself still succeeded.",
                        appid, job_id,
                    )
                    log_parts.append(
                        "[vault-api] Manifest ingestion crashed; see server logs "
                        "(the prefill job itself still succeeded)."
                    )

                now = jobs.utcnow_iso()
                # ADR-0006 "current as of <timestamp>" semantics: only this
                # exact shape — nothing changed, but SteamPrefill DID confirm
                # at least one already-current depot for this app — earns
                # last_manifest_check. A parse failure or the updated>0 case
                # both leave it untouched (None -> set_app_status skips it).
                last_manifest_check = (
                    now
                    if summary.parse_ok
                    and summary.updated == 0
                    and summary.up_to_date is not None
                    and summary.up_to_date > 0
                    else None
                )

                jobs.set_app_status(
                    conn, appid, jobs.STATUS_DONE,
                    last_prefill_at=now,
                    last_manifest_check=last_manifest_check,
                )

                # WP 3.4 / ADR-0006 decision 2, hardened against a concurrent
                # DELETE (reviewer-reproduced wedge, see
                # jobs.clear_needs_force_if_unchanged's docstring for the
                # full sequence): this is the ONE branch that reaches a
                # genuinely successful outcome (covers both the updated>0 and
                # the up-to-date-confirmed rows of the table above) --
                # --force has done its job for this app (or was never
                # needed), so the next run may safely go non-forced. But the
                # clear is a compare-and-swap against `use_force` -- the
                # value read at job-claim time, BEFORE this run started --
                # not an unconditional write: if a DELETE landed on this app
                # while the job was running and set needs_force=1 (cache
                # state changed underneath this run), the swap's WHERE
                # clause no longer matches and the clear is correctly a
                # no-op, leaving the NEXT run forced instead of wedging the
                # app at 'done' over an empty cache forever. The unowned
                # branch above and every failure branch below never call
                # this at all, leaving the flag exactly as the deletion path
                # (or the schema default) last set it.
                if not jobs.clear_needs_force_if_unchanged(conn, appid, use_force):
                    logger.info(
                        "Prefill job %s for appid %s: needs_force was changed "
                        "concurrently (likely a DELETE) while this job ran; "
                        "left as-is so the next run for this app is forced.",
                        job_id, appid,
                    )

                jobs.finish_job(
                    conn, job_id, jobs.STATUS_DONE, "\n".join(log_parts),
                    updated=summary.updated,
                    up_to_date=summary.up_to_date,
                    summary_parse_ok=summary.parse_ok,
                )

                # Disk content may have changed (plan §3: size calculation is
                # "cached" — explicit invalidation, not polling). Whether this
                # particular run was forced or not, invalidating unconditionally
                # rather than only when `observed` is non-empty is still
                # correct and cheap: a no-op recompute costs one disk walk.
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
