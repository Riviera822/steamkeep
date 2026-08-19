"""Queue-mode glue between vault-api's worker and the ``prefill_runner``
process (WP S-1, ADR-0012).

This module is imported by BOTH sides of the split and owns nothing that
either side could disagree about:

- ``encode_result``/``decode_result`` turn a :class:`vault_api.prefill.PrefillResult`
  into/out of the ``jobs.run_result_json`` TEXT column, so vault-api's worker
  can reconstruct one and run the exact same post-run branch logic
  (``worker.py``'s ``_finalize_prefill_result``) regardless of which process
  actually ran SteamPrefill.
- ``encode_signatures``/``decode_signatures`` do the same for the
  ``jobs.run_before_json`` depot-signature snapshot ``apply_observed_mapping``
  needs (see ``jobs.handoff_run``'s docstring for why this has to be
  persisted rather than kept in worker memory).
- ``await_run_result`` is vault-api's half of the hand-off: write the request,
  then poll for either a result or a dead runner. It is also what
  ``PrefillWorker._run``'s reattach path calls after a vault-api restart —
  the SAME function, because "wait for this job's runner" has exactly one
  meaning regardless of whether the wait started just now or resumes one
  from before a restart.

The runner side's use of ``prefill.run_prefill`` (unchanged) plus
``jobs.claim_run``/``record_run_heartbeat``/``record_run_result`` lives in
``vault_api/prefill_runner.py`` — that module is the CLI entrypoint and
signal-handling half, deliberately kept separate from this one so importing
this module (from ``worker.py``, in every install regardless of
``VAULT_PREFILL_MODE``) never pulls in argument parsing or signal handlers.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Callable

from vault_api import jobs
from vault_api.prefill import PrefillResult
from vault_api.sizes import DepotSignature

logger = logging.getLogger(__name__)

#: ``PrefillResult.failure_reason`` for "vault-api's worker judged the
#: claimed runner dead" (``jobs.run_is_stale``). Not one of
#: ``prefill.STOP_FAILURE_REASONS`` — this is not an operator request, it is
#: a genuine failure, and ``worker.py``'s existing generic failure branch
#: (mapping left untouched, ``apps.status`` -> 'error') is exactly the right
#: outcome for it with no new branch needed.
FAILURE_RUNNER_LOST = "runner_lost"

RUNNER_LOST_MESSAGE = (
    "[vault-api] The prefill_runner process that claimed this job stopped "
    "sending heartbeats and is presumed dead (crash, OOM kill, container "
    "restart). Whatever it had already written to the cache stays on disk "
    "and replays as local HITs on the next run — re-queue with "
    "POST /v1/prefill if you still want this app prefilled."
)


def encode_result(result: PrefillResult) -> str:
    """``PrefillResult`` -> the ``jobs.run_result_json`` TEXT column."""
    return json.dumps(
        {
            "success": result.success,
            "failure_reason": result.failure_reason,
            "exit_code": result.exit_code,
            "output": result.output,
        }
    )


def decode_result(raw: str) -> PrefillResult:
    """The inverse of :func:`encode_result`. Never raises on well-formed JSON
    from :func:`encode_result` itself; a corrupt/foreign value is the
    caller's problem to guard against (this project's DB is single-writer
    per row by construction — see ``claim_run``'s compare-and-swap)."""
    data = json.loads(raw)
    return PrefillResult(
        success=bool(data["success"]),
        failure_reason=data["failure_reason"],
        exit_code=data["exit_code"],
        output=str(data["output"]),
    )


def encode_signatures(signatures: dict[int, DepotSignature]) -> str:
    """``{depotid: (count, bytes, mtime)}`` -> the ``run_before_json`` column.

    JSON object keys must be strings — ``depotid`` round-trips back to ``int``
    in :func:`decode_signatures`, which is the only place that matters (the
    dict is used as ``dict[int, DepotSignature]`` by ``prefill.diff_depots``).
    """
    return json.dumps({str(depotid): list(sig) for depotid, sig in signatures.items()})


def decode_signatures(raw: str | None) -> dict[int, DepotSignature]:
    """The inverse of :func:`encode_signatures`. ``None``/blank -> ``{}``
    (an app handed off before this column existed, or a defensive default —
    an empty snapshot is the SAFE direction: ``diff_depots`` would then treat
    every depot signature present ``after`` as "new", which only means a
    depot mapping gets written that ``apply_observed_mapping`` would have
    written anyway; it never causes anything to be dropped)."""
    if not raw:
        return {}
    return {int(depotid): tuple(sig) for depotid, sig in json.loads(raw).items()}  # type: ignore[misc]


def await_run_result(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    lease_timeout_seconds: float,
    poll_seconds: float,
    should_abort: Callable[[], bool],
) -> tuple[PrefillResult, dict[int, DepotSignature]] | None:
    """Wait for ``job_id``'s runner to finish (or be declared dead).

    Returns ``(result, before_signatures)`` once there is an outcome — either
    a genuine completion reported by the runner, or a synthesized
    :data:`FAILURE_RUNNER_LOST` result once ``jobs.run_is_stale`` says the
    claimed runner (or the complete absence of one) has gone quiet for longer
    than ``lease_timeout_seconds``.

    Returns ``None`` if ``should_abort`` fires first — vault-api itself is
    shutting down. This is deliberately NOT a failure result: the job is left
    exactly as ``'running'`` with its hand-off intact, because the runner is a
    SEPARATE process that has not been asked to stop and may well finish
    normally while vault-api is down. ``PrefillWorker._run``'s reattach path
    (``jobs.find_active_run``) picks it back up — by calling this SAME
    function again — on the next startup, and this function's own staleness
    check is what eventually fails it honestly if the runner really did die
    too. See ADR-0012 §4 for why this is the chosen crash semantics
    over vault-api unilaterally failing a job it has no evidence is dead.

    Assumes ``jobs.handoff_run`` has already been called for this job (either
    just now, by the fresh-claim path, or in a previous process lifetime, for
    the reattach path) — this function only waits and reads, it never writes
    the hand-off itself.
    """
    while True:
        if should_abort():
            return None

        row = jobs.get_run_row(conn, job_id)
        if row is None:  # pragma: no cover - defensive: the job row is gone
            logger.error(
                "Job %s vanished while vault-api was waiting on its runner.",
                job_id,
            )
            return (
                PrefillResult(
                    False,
                    "setup",
                    None,
                    "[vault-api] Internal error: job row disappeared while "
                    "waiting for the runner.",
                ),
                {},
            )

        if row.get("run_completed_at"):
            result = decode_result(str(row["run_result_json"]))
            before = decode_signatures(
                row["run_before_json"] if isinstance(row.get("run_before_json"), str) else None
            )
            return result, before

        if jobs.run_is_stale(row, lease_timeout_seconds):
            before = decode_signatures(
                row["run_before_json"] if isinstance(row.get("run_before_json"), str) else None
            )
            logger.warning(
                "Job %s: runner %r presumed dead (no heartbeat within %.0fs); "
                "failing the job.",
                job_id, row.get("run_claimed_by"), lease_timeout_seconds,
            )
            return (
                PrefillResult(False, FAILURE_RUNNER_LOST, None, RUNNER_LOST_MESSAGE),
                before,
            )

        time.sleep(poll_seconds)
