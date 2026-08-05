"""SteamPrefill runner: run one prefill, observe which depots it filled.

Two independent jobs live here:

1. ``run_prefill`` — invoke the SteamPrefill CLI for exactly one appid as a
   subprocess and report the outcome (never raises for a *prefill* failure;
   failures are data, so the job queue can record them).
2. ``scan_depots`` / ``diff_depots`` / ``apply_observed_mapping`` — attribute
   the depots that changed during the run to that appid and write the mapping
   with replace-semantics (ADR-0003 decision 3).

The verified SteamPrefill CLI contract (v3.7.1)
-----------------------------------------------
Checked empirically against ``poc/steamprefill/bin/SteamPrefill.exe`` while
writing this module — NOT assumed from docs:

- ``SteamPrefill.exe prefill --help`` offers ``--all``, ``--recent``,
  ``--recently-purchased``, ``--top``, ``-f|--force``, ``--os``, ``--verbose``,
  ``--unit``, ``--no-ansi``. **There is no ``--app-ids`` / ``--appid`` option
  and no positional app-id parameter**: ``SteamPrefill.exe prefill 480`` is
  rejected with "Unexpected parameter(s): <zzz>"-style output, and the binary
  contains no ``app-ids`` string at all.
- The app selection ``prefill`` consumes is a **state file**:
  ``<exe dir>/Config/selectedAppsToPrefill.json``, a plain JSON array of app
  ids (observed content: ``[3419430]``). ``select-apps`` is only the
  interactive TUI that writes it; ``select-apps status`` reads it back and
  listed exactly that one app, confirming the file is the selection store.
- So the non-interactive way to prefill a specific app id is: **write
  ``Config/selectedAppsToPrefill.json`` = ``[appid]``, then run
  ``SteamPrefill.exe prefill --force --no-ansi``.** That is what this module
  does. Consequence, documented in api/README.md: vault-api OWNS that file —
  a manual ``select-apps`` selection on the same SteamPrefill installation
  gets overwritten on the next job.
- ``--force`` is deliberate. Without it SteamPrefill skips apps its own
  ``Config/successfullyDownloadedDepots.json`` considers up to date — state
  that knows nothing about vault-api deleting an app from the cache
  (``DELETE /v1/cache/{appid}``, WP 1.6), so a non-forced run would silently
  refuse to re-fill a game we just deleted. Chunks still present on disk are
  re-requested and served by vault-core as local HITs, so the cost of
  ``--force`` is disk speed, not internet bandwidth (Phase 0 measured
  HIT ~120x faster than MISS, ADR-0001).
- ``--no-ansi`` is passed but is **not sufficient**: Spectre.Console's
  exception renderer still emits SGR escapes (observed). Captured output is
  therefore stripped of ANSI escapes here before being stored.
- Not logged in, stdin closed: verified against a fresh copy of the binary
  with an empty ``Config/``. It does **not** hang — it prints "A Steam account
  is required in order to prefill apps!" / "Please enter your Steam account
  name :" and dies with
  ``InvalidOperationException: Failed to read input in non-interactive mode.``,
  exit code 1, in about a second. Login happens *before* cache detection, so
  this is the first thing that fails on an unconfigured install. The timeout
  below is still enforced as a backstop (a Steam Guard prompt or a wedged
  network connection is not something this was able to prove fast-fails).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from typing import Callable

from vault_api.mapping import delete_mapping, upsert_mapping

logger = logging.getLogger(__name__)

#: Name of SteamPrefill's own selected-apps state file, relative to the
#: directory holding the executable.
SELECTED_APPS_RELPATH = os.path.join("Config", "selectedAppsToPrefill.json")

#: Substrings that identify the "no Steam session, cannot prompt" failure.
#: Taken verbatim from the observed run described in the module docstring.
NOT_LOGGED_IN_MARKERS = (
    "a steam account is required",
    "failed to read input in non-interactive mode",
    "please enter your steam account name",
)

NOT_LOGGED_IN_HINT = (
    "SteamPrefill has no usable Steam session, and vault-api runs it "
    "non-interactively (stdin closed), so it cannot prompt for credentials. "
    "Log in once by hand on the server: run 'SteamPrefill select-apps' in a "
    "terminal, enter your account name/password/Steam Guard code, then retry "
    "this job. The session is stored next to the executable in Config/ and is "
    "reused afterwards. vault-api never sees or stores Steam credentials."
)

#: How often the subprocess is polled for exit / timeout / abort.
_POLL_INTERVAL_SECONDS = 0.2

#: Grace period between terminate() and kill() when aborting a subprocess.
_TERMINATE_GRACE_SECONDS = 5.0

_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences (``--no-ansi`` does not cover all output)."""
    return _ANSI_ESCAPE_RE.sub("", text)


# --------------------------------------------------------------------------
# Running SteamPrefill
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PrefillResult:
    """Outcome of one SteamPrefill invocation. Failures are data, not exceptions."""

    success: bool
    #: 'not_logged_in' | 'timeout' | 'aborted' | 'exit_code' | 'setup' | None
    failure_reason: str | None
    exit_code: int | None
    #: ANSI-stripped combined stdout+stderr, plus any diagnostic vault-api adds.
    output: str


def resolve_executable(steamprefill_path: str) -> tuple[str | None, str | None]:
    """Validate the configured SteamPrefill path. Returns ``(path, error)``.

    Returning an error instead of raising is the point: a box without
    SteamPrefill set up must still serve the rest of the API (plan §6), so this
    turns into a per-job failure, never a startup failure.
    """
    if not steamprefill_path:
        return None, (
            "VAULT_STEAMPREFILL_PATH is not set, so vault-api has no SteamPrefill "
            "executable to run. Set it to the full path of SteamPrefill(.exe) and "
            "restart vault-api (see api/README.md)."
        )
    if not os.path.isfile(steamprefill_path):
        return None, (
            f"VAULT_STEAMPREFILL_PATH points at {steamprefill_path!r}, which is not "
            "an existing file. Fix the path and restart vault-api."
        )
    return steamprefill_path, None


def write_selected_apps(executable: str, appid: int) -> str | None:
    """Point SteamPrefill's selection at exactly ``[appid]``. Returns an error string or None.

    This is the verified non-interactive selection mechanism (see module
    docstring). vault-api overwrites the file wholesale on every job — one
    appid per job, one job at a time, so the file always describes the job
    that is about to run.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(executable)), SELECTED_APPS_RELPATH)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump([appid], handle)
    except OSError as exc:
        return (
            f"Could not write SteamPrefill's app selection to {path!r}: {exc}. "
            "vault-api needs write access to the Config/ directory next to the "
            "SteamPrefill executable — that file is how a specific app id is "
            "selected non-interactively."
        )
    return None


def run_prefill(
    appid: int,
    steamprefill_path: str,
    timeout_seconds: int,
    should_abort: Callable[[], bool] | None = None,
) -> PrefillResult:
    """Run SteamPrefill for one appid. Never raises for a prefill failure.

    ``should_abort`` is polled while waiting; when it returns True (vault-api is
    shutting down) the subprocess is terminated and the result is a failure with
    reason ``'aborted'``. Without that, ``docker stop`` would hang until the
    prefill finished or the runtime SIGKILLed the container.
    """
    executable, error = resolve_executable(steamprefill_path)
    if executable is None:
        return PrefillResult(False, "setup", None, error or "")

    error = write_selected_apps(executable, appid)
    if error is not None:
        return PrefillResult(False, "setup", None, error)

    command = [executable, "prefill", "--force", "--no-ansi"]
    workdir = os.path.dirname(os.path.abspath(executable))

    # Output goes to a temp FILE rather than a pipe on purpose: a prefill run
    # can emit a lot of progress output, and a PIPE that nobody drains while we
    # poll for the timeout/abort conditions would deadlock on a full OS pipe
    # buffer. A file also makes reading just the tail trivial.
    handle = tempfile.NamedTemporaryFile(
        mode="w+", encoding="utf-8", errors="replace", suffix=".log",
        prefix=f"vault-prefill-{appid}-", delete=False,
    )
    log_path = handle.name
    try:
        with handle:
            try:
                process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                    command,
                    cwd=workdir,
                    # stdin closed: SteamPrefill prompts interactively when it
                    # has no session; DEVNULL makes that fail fast instead of
                    # blocking forever on a prompt nobody can answer.
                    stdin=subprocess.DEVNULL,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                )
            except OSError as exc:
                return PrefillResult(
                    False, "setup", None,
                    f"Could not start {command[0]!r}: {exc}",
                )

            outcome, exit_code = _wait_for_process(
                process, timeout_seconds, should_abort
            )

        output = strip_ansi(_read_text(log_path))

        if outcome == "timeout":
            return PrefillResult(
                False, "timeout", exit_code,
                output
                + f"\n[vault-api] SteamPrefill exceeded the {timeout_seconds}s time "
                "budget (VAULT_PREFILL_TIMEOUT_SECONDS) and was killed.",
            )
        if outcome == "aborted":
            return PrefillResult(
                False, "aborted", exit_code,
                output + "\n[vault-api] Aborted: vault-api is shutting down.",
            )

        if _looks_not_logged_in(output):
            return PrefillResult(
                False, "not_logged_in", exit_code,
                output + "\n[vault-api] " + NOT_LOGGED_IN_HINT,
            )
        if exit_code != 0:
            return PrefillResult(
                False, "exit_code", exit_code,
                output + f"\n[vault-api] SteamPrefill exited with code {exit_code}.",
            )
        return PrefillResult(True, None, exit_code, output)
    finally:
        try:
            os.unlink(log_path)
        except OSError:  # pragma: no cover - best effort cleanup
            logger.warning("Could not remove temporary prefill log %s", log_path)


def _wait_for_process(
    process: "subprocess.Popen[str]",
    timeout_seconds: int,
    should_abort: Callable[[], bool] | None,
) -> tuple[str, int | None]:
    """Poll until exit / timeout / abort. Returns ``(outcome, exit_code)``."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        exit_code = process.poll()
        if exit_code is not None:
            return "exited", exit_code
        if should_abort is not None and should_abort():
            return "aborted", _stop_process(process)
        if time.monotonic() >= deadline:
            return "timeout", _stop_process(process)
        time.sleep(_POLL_INTERVAL_SECONDS)


def _stop_process(process: "subprocess.Popen[str]") -> int | None:
    """terminate(), then kill() if it doesn't go away. Returns the exit code."""
    process.terminate()
    try:
        return process.wait(timeout=_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            return process.wait(timeout=_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:  # pragma: no cover - unkillable process
            return None


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError as exc:  # pragma: no cover - defensive
        return f"[vault-api] Could not read captured output: {exc}"


def _looks_not_logged_in(output: str) -> bool:
    lowered = output.lower()
    return any(marker in lowered for marker in NOT_LOGGED_IN_MARKERS)


# --------------------------------------------------------------------------
# Depot attribution (ADR-0003 decision 3)
# --------------------------------------------------------------------------

#: (file_count, total_bytes, newest_mtime_ns) per depot id.
DepotSignature = tuple[int, int, int]


def scan_depots(cache_root: str) -> dict[int, DepotSignature]:
    """Signature per depot directory under ``<cache_root>/depot/<depotid>/``.

    The signature is deliberately an aggregate, not a per-file listing: a real
    cache holds hundreds of thousands of chunk files and this runs twice per
    job. ``(count, bytes, newest mtime)`` changes whenever a chunk is added,
    replaced with different content, or rewritten — which is all the diff needs.

    Depots with zero files are omitted: an empty directory means nothing was
    stored, so it must not be attributed to an app.
    """
    signatures: dict[int, DepotSignature] = {}
    depot_root = os.path.join(cache_root, "depot")
    try:
        entries = list(os.scandir(depot_root))
    except (FileNotFoundError, NotADirectoryError):
        return signatures
    except OSError as exc:  # pragma: no cover - defensive
        logger.warning("Could not scan %s: %s", depot_root, exc)
        return signatures

    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
        except OSError:  # pragma: no cover - defensive
            continue

        count = 0
        total_bytes = 0
        newest_mtime_ns = 0
        for root, _dirs, files in os.walk(entry.path):
            for name in files:
                try:
                    stat = os.stat(os.path.join(root, name))
                except OSError:
                    # Chunk vanished/locked mid-walk (nginx is writing into
                    # this tree concurrently) — skip it rather than fail.
                    continue
                count += 1
                total_bytes += stat.st_size
                newest_mtime_ns = max(newest_mtime_ns, stat.st_mtime_ns)

        if count:
            signatures[int(entry.name)] = (count, total_bytes, newest_mtime_ns)

    return signatures


def diff_depots(
    before: dict[int, DepotSignature], after: dict[int, DepotSignature]
) -> set[int]:
    """Depot ids that are new or changed between the two snapshots.

    Disappearing depots are ignored on purpose: this diff exists to attribute
    *filled* content to an app, and a depot removed during a prefill (only
    possible via a concurrent ``DELETE /v1/cache/...``, WP 1.6) was not filled
    by it.
    """
    return {depotid for depotid, signature in after.items() if before.get(depotid) != signature}


def apply_observed_mapping(
    conn: sqlite3.Connection, appid: int, observed: set[int]
) -> "MappingChange":
    """Replace ``appid``'s depot mapping with ``observed`` (ADR-0003 decision 3).

    Semantics, precisely:

    - **Replace within an app.** Depot rows currently mapped to ``appid`` that
      are NOT in ``observed`` are deleted, so a depot Steam reassigned away
      from this app stops being reported as its content (and stops blocking
      WP 1.6's deletion as a false "shared" depot).
    - **Additive across apps.** Only rows for *this* ``appid`` are touched. A
      depot in ``observed`` that another app also maps keeps that other
      mapping — that is plan §4's shared-depot case (redistributables), and
      both apps continue to report it as ``shared``.
    - **Empty observation changes nothing.** If the run filled no new bytes,
      ``observed`` is empty and the existing mapping is left completely alone.
      This is the common case for an already-fully-cached app: every chunk is
      served from disk as a HIT, nothing is written, so there is nothing to
      observe. Wiping the mapping there would delete correct data on the
      strength of no evidence at all, so it is explicitly not done. The
      trade-off is honest: a stale depot row only disappears once a prefill
      actually writes something for that app again (a game update), or via
      ``DELETE /v1/mapping/{depotid}/{appid}``.
    """
    if not observed:
        return MappingChange(added=set(), removed=set(), kept=set(), skipped_empty=True)

    existing = {
        int(row["depotid"])
        for row in conn.execute(
            "SELECT depotid FROM depot_app_map WHERE appid = ?", (appid,)
        ).fetchall()
    }

    stale = existing - observed
    for depotid in sorted(stale):
        delete_mapping(conn, depotid=depotid, appid=appid)

    for depotid in sorted(observed):
        upsert_mapping(conn, depotid=depotid, appid=appid, name=None)

    return MappingChange(
        added=observed - existing,
        removed=stale,
        kept=observed & existing,
        skipped_empty=False,
    )


@dataclass(frozen=True)
class MappingChange:
    """What ``apply_observed_mapping`` did, for the job log."""

    added: set[int] = field(default_factory=set)
    removed: set[int] = field(default_factory=set)
    kept: set[int] = field(default_factory=set)
    #: True when nothing was observed and the mapping was left untouched.
    skipped_empty: bool = False

    def summary(self) -> str:
        if self.skipped_empty:
            return (
                "[vault-api] No new or changed depot directories were observed during "
                "this run (everything requested was already cached), so the existing "
                "depot mapping for this app was left unchanged."
            )
        return (
            "[vault-api] Depot mapping updated (replace-semantics for this app): "
            f"added={sorted(self.added)} removed={sorted(self.removed)} "
            f"unchanged={sorted(self.kept)}"
        )
