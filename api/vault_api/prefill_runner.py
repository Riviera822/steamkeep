"""The queue-mode SteamPrefill runner (WP S-1, ADR-0012).

    python -m vault_api.prefill_runner

A slim, standalone process: it owns nothing but "poll the ``jobs`` table for
a handed-off prefill job, run SteamPrefill for it, report back" — the exact
piece of ``vault_api.worker.PrefillWorker`` that used to be a direct
``subprocess.Popen`` call inside vault-api itself. Job lifecycle (claiming
from 'queued', deciding ``--force``, applying the depot mapping, manifest
ingestion, webhooks, auto-GC) stays entirely in vault-api's worker — this
process never touches any of that, and imports nothing from ``worker.py``.

**Why this process needs broad network egress and vault-api should not
(the reason this split exists at all, EG-1's stop report — see
``docs/adr/0012-*.md``):** SteamPrefill talks to Steam's CM/CDN network
directly. Splitting it into its own process is what makes it possible for
EG-1 to lock vault-api's own container down to LAN-only egress without also
cutting off the one thing that legitimately needs the wider internet.

**The interactive login (ADR-0004 decision 1) now happens in THIS
container**, not vault-api's: SteamPrefill's ``Config/`` directory (the Steam
session) lives wherever this process's ``VAULT_STEAMPREFILL_PATH`` points,
so the one-time ``SteamPrefill select-apps`` login step is run via
``docker exec`` into the runner container once S-2 wires it up — see
api/README.md "Queue mode: the prefill_runner process" for the full
walkthrough. vault-api still never sees or stores Steam credentials; that
part of ADR-0004 is completely unaffected.

Started unconditionally by whatever launches it (a second command in
``compose.yaml``, S-2) — it is a genuine no-op, sleeping between polls, on an
install with nothing queued, and idles harmlessly if ``VAULT_PREFILL_MODE``
on the vault-api side is still ``subprocess`` (nothing ever gets handed off
for it to claim in that mode).
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import uuid

from vault_api import jobs, prefill, prefill_queue
from vault_api.config import Settings
from vault_api.db import get_connection

logger = logging.getLogger(__name__)


def make_runner_id() -> str:
    """A human-recognisable, effectively-unique id for this process instance.

    Purely for observability (``jobs.run_claimed_by``, logs) — nothing in the
    claim/heartbeat/result mechanics compares two runner ids against each
    other for correctness; ``jobs.claim_run``'s compare-and-swap is what
    actually guarantees exclusivity, this id just says who to blame in a log
    line. Hostname + pid identifies the container/process; the random suffix
    disambiguates two processes that crash-looped fast enough to reuse a pid.
    """
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class PrefillRunner:
    """The claim -> execute -> report loop. See the module docstring."""

    def __init__(self, settings: Settings, runner_id: str | None = None) -> None:
        self._settings = settings
        self._runner_id = runner_id or make_runner_id()
        self._stop = threading.Event()

    @property
    def runner_id(self) -> str:
        return self._runner_id

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def run_forever(self) -> None:
        """The main loop. Blocks until :meth:`stop` is called (typically from
        a signal handler — see :func:`main`)."""
        conn = get_connection(self._settings.db_path)
        logger.info(
            "prefill_runner %s starting (poll every %.1fs, heartbeat every "
            "%.1fs, SteamPrefill path %r).",
            self._runner_id,
            self._settings.runner_poll_seconds,
            self._settings.runner_heartbeat_seconds,
            self._settings.steamprefill_path,
        )
        try:
            while not self._stop.is_set():
                job = jobs.claim_run(conn, self._runner_id)
                if job is None:
                    self._stop.wait(self._settings.runner_poll_seconds)
                    continue
                self._execute(conn, job)
        finally:
            conn.close()
            logger.info("prefill_runner %s stopped.", self._runner_id)

    def _execute(self, conn, job: dict[str, object]) -> None:
        job_id = int(job["id"])  # type: ignore[arg-type]
        appid = int(job["appid"])  # type: ignore[arg-type]
        use_force = bool(job["run_use_force"])
        logger.info(
            "prefill_runner %s claimed job %s (appid %s, use_force=%s).",
            self._runner_id, job_id, appid, use_force,
        )

        last_heartbeat = time.monotonic()

        def stop_request_with_heartbeat() -> str | None:
            """Piggyback the heartbeat on ``run_prefill``'s existing 0.2s
            subprocess poll tick (see ``prefill.py``'s ``_wait_for_process``)
            instead of adding a second polling loop. Throttled to
            ``runner_heartbeat_seconds`` — every 0.2s tick would otherwise
            write to the shared database several times a second for no
            benefit (``jobs.run_is_stale``'s margin is measured in whole
            heartbeat intervals, not sub-second ticks).
            """
            nonlocal last_heartbeat
            now = time.monotonic()
            if now - last_heartbeat >= self._settings.runner_heartbeat_seconds:
                jobs.record_run_heartbeat(conn, job_id, self._runner_id)
                last_heartbeat = now
            return jobs.read_stop_request(conn, job_id)

        result = prefill.run_prefill(
            appid=appid,
            steamprefill_path=self._settings.steamprefill_path,
            timeout_seconds=self._settings.prefill_timeout_seconds,
            should_abort=self._stop.is_set,
            use_force=use_force,
            stop_request=stop_request_with_heartbeat,
        )

        applied = jobs.record_run_result(
            conn, job_id, self._runner_id, prefill_queue.encode_result(result)
        )
        if applied:
            logger.info(
                "prefill_runner %s: job %s (appid %s) finished, success=%s "
                "failure_reason=%r.",
                self._runner_id, job_id, appid, result.success, result.failure_reason,
            )
        else:
            # vault-api already declared this job's lease dead (staleness,
            # ADR-0012 §4) and failed it while we were still running
            # SteamPrefill — see jobs.record_run_result's docstring. The
            # bytes we wrote to the cache are still on disk; there is nothing
            # left for us to do with this outcome.
            logger.warning(
                "prefill_runner %s: job %s (appid %s) finished, but vault-api "
                "had already declared it dead (result discarded; the run's "
                "output is not lost, only this row's bookkeeping is).",
                self._runner_id, job_id, appid,
            )


def main() -> None:
    # require_api_key=False (WP S-1 round-2 review, S2; ADR-0012 §2/§5
    # addendum): this process never serves HTTP and never authenticates
    # anything, so it has no legitimate use for the LAN control-plane
    # secret vault-api itself requires -- see Settings.from_env's own
    # docstring for the full argument. VAULT_API_KEY does not need to be
    # injected into the one container this split exists to isolate.
    settings = Settings.from_env(require_api_key=False)
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

    runner = PrefillRunner(settings)

    def _handle_signal(signum: int, _frame: object) -> None:
        logger.info("prefill_runner received signal %s; shutting down.", signum)
        runner.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
    except (AttributeError, ValueError):  # pragma: no cover - SIGTERM is POSIX-only
        pass

    runner.run_forever()


if __name__ == "__main__":
    main()
