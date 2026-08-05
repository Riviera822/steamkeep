"""The SteamPrefill runner and depot attribution, without the worker/HTTP layers."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tests import stub_prefill
from vault_api import prefill
from vault_api.db import get_connection, init_db
from vault_api.mapping import upsert_mapping


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "vault.db")
    init_db(path)
    connection = get_connection(path)
    yield connection
    connection.close()


# -- configuration failures are per-job, never fatal -----------------------


def test_missing_path_is_a_clear_job_failure_not_an_exception() -> None:
    result = prefill.run_prefill(440, "", timeout_seconds=5)

    assert result.success is False
    assert result.failure_reason == "setup"
    assert "VAULT_STEAMPREFILL_PATH is not set" in result.output


def test_nonexistent_path_is_a_clear_job_failure(tmp_path: Path) -> None:
    bogus = str(tmp_path / "nope" / "SteamPrefill.exe")
    result = prefill.run_prefill(440, bogus, timeout_seconds=5)

    assert result.success is False
    assert result.failure_reason == "setup"
    assert "not an existing file" in result.output


# -- the verified CLI contract --------------------------------------------


def test_runner_selects_the_appid_via_the_state_file_and_uses_the_verified_argv(
    tmp_path: Path,
) -> None:
    """Pins the empirically verified invocation (see vault_api/prefill.py).

    v3.7.1 has no --app-ids option and no positional app id, so the only
    non-interactive selection mechanism is Config/selectedAppsToPrefill.json.
    """
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [441]}
    )

    result = prefill.run_prefill(440, executable, timeout_seconds=30)

    assert result.success is True, result.output
    assert result.exit_code == 0
    assert stub_prefill.read_selection(bindir) == [440]
    assert stub_prefill.read_argv(bindir) == ["prefill", "--force", "--no-ansi"]
    # The stub echoes what it read back, proving the selection reached it.
    assert "selected=[440]" in result.output


def test_subprocess_gets_a_closed_stdin(tmp_path: Path) -> None:
    # Without this, SteamPrefill's interactive credential prompt would block
    # the worker forever instead of failing fast.
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, cache_root=str(tmp_path / "cache"))

    result = prefill.run_prefill(440, executable, timeout_seconds=30)

    assert "stdin=eof" in result.output


def test_selection_file_is_rewritten_for_every_job(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, cache_root=str(tmp_path / "cache"))

    prefill.run_prefill(440, executable, timeout_seconds=30)
    assert stub_prefill.read_selection(bindir) == [440]

    prefill.run_prefill(730, executable, timeout_seconds=30)
    assert stub_prefill.read_selection(bindir) == [730]


# -- failure modes ---------------------------------------------------------


def test_nonzero_exit_is_reported_with_the_captured_output(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, mode="fail", exit_code=5)

    result = prefill.run_prefill(440, executable, timeout_seconds=30)

    assert result.success is False
    assert result.failure_reason == "exit_code"
    assert result.exit_code == 5
    # stderr is merged into the captured stream, so it shows up in the excerpt.
    assert "simulated depot download failure" in result.output
    assert "exited with code 5" in result.output


def test_not_logged_in_output_is_detected_and_gets_an_actionable_hint(
    tmp_path: Path,
) -> None:
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, mode="not_logged_in")

    result = prefill.run_prefill(440, executable, timeout_seconds=30)

    assert result.success is False
    assert result.failure_reason == "not_logged_in"
    assert "select-apps" in result.output
    assert "never sees or stores Steam credentials" in result.output


def test_a_hanging_subprocess_hits_the_timeout_and_is_killed(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, mode="hang")

    started = time.monotonic()
    result = prefill.run_prefill(440, executable, timeout_seconds=1)
    elapsed = time.monotonic() - started

    assert result.success is False
    assert result.failure_reason == "timeout"
    assert "exceeded the 1s time budget" in result.output
    # It must actually return promptly, not wait out the stub's own bound.
    assert elapsed < 9, elapsed


def test_should_abort_stops_the_run(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, mode="hang")

    result = prefill.run_prefill(
        440, executable, timeout_seconds=60, should_abort=lambda: True
    )

    assert result.success is False
    assert result.failure_reason == "aborted"
    assert "shutting down" in result.output


def test_output_is_ansi_stripped() -> None:
    assert prefill.strip_ansi("\x1b[38;5;9mred\x1b[0m text") == "red text"


def test_large_output_is_captured_without_deadlocking(tmp_path: Path) -> None:
    # Output goes to a temp file rather than a pipe precisely so a chatty run
    # can't fill an OS pipe buffer while the runner is polling for a timeout.
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, mode="chatty", cache_root=str(tmp_path / "c"))

    result = prefill.run_prefill(440, executable, timeout_seconds=60)

    assert result.success is True
    assert len(result.output) > 16000
    assert "Prefill complete!" in result.output


# -- depot scanning / diffing ---------------------------------------------


def _write_chunk(cache_root: Path, depotid: int, name: str, payload: bytes = b"x" * 64) -> None:
    chunk_dir = cache_root / "depot" / str(depotid) / "chunk"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    (chunk_dir / name).write_bytes(payload)


def test_scan_depots_on_a_missing_cache_root_is_empty(tmp_path: Path) -> None:
    assert prefill.scan_depots(str(tmp_path / "does-not-exist")) == {}


def test_scan_depots_ignores_non_numeric_and_empty_depot_dirs(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    _write_chunk(cache_root, 441, "a")
    (cache_root / "depot" / "manifest").mkdir(parents=True, exist_ok=True)
    (cache_root / "depot" / "999").mkdir(parents=True, exist_ok=True)  # empty

    signatures = prefill.scan_depots(str(cache_root))

    assert set(signatures) == {441}


def test_diff_depots_reports_new_and_changed_only(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    _write_chunk(cache_root, 441, "a")
    _write_chunk(cache_root, 500, "a")
    before = prefill.scan_depots(str(cache_root))

    _write_chunk(cache_root, 441, "b")   # changed (new file)
    _write_chunk(cache_root, 442, "a")   # new depot

    after = prefill.scan_depots(str(cache_root))

    assert prefill.diff_depots(before, after) == {441, 442}


def test_diff_depots_ignores_removed_depots(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    _write_chunk(cache_root, 441, "a")
    before = prefill.scan_depots(str(cache_root))
    os.remove(cache_root / "depot" / "441" / "chunk" / "a")
    after = prefill.scan_depots(str(cache_root))

    assert prefill.diff_depots(before, after) == set()


# -- mapping replace-semantics (ADR-0003 decision 3) ----------------------


def test_apply_observed_mapping_replaces_within_the_app(conn) -> None:
    upsert_mapping(conn, depotid=441, appid=440, name="TF2")
    upsert_mapping(conn, depotid=999, appid=440, name=None)  # will go stale

    change = prefill.apply_observed_mapping(conn, 440, {441, 442})

    depots = {
        row["depotid"]
        for row in conn.execute("SELECT depotid FROM depot_app_map WHERE appid = 440")
    }
    assert depots == {441, 442}
    assert change.removed == {999}
    assert change.added == {442}
    assert change.kept == {441}
    assert change.skipped_empty is False


def test_apply_observed_mapping_keeps_shared_depots_mapped_to_other_apps(conn) -> None:
    # Depot 300 is a shared/redistributable depot used by 440 and 730.
    upsert_mapping(conn, depotid=300, appid=440, name=None)
    upsert_mapping(conn, depotid=300, appid=730, name=None)
    upsert_mapping(conn, depotid=999, appid=440, name=None)

    prefill.apply_observed_mapping(conn, 440, {300, 301})

    pairs = {
        (row["depotid"], row["appid"])
        for row in conn.execute("SELECT depotid, appid FROM depot_app_map")
    }
    # Replace within 440 (999 gone, 301 added), additive across apps (730 kept).
    assert pairs == {(300, 440), (300, 730), (301, 440)}


def test_apply_observed_mapping_leaves_everything_alone_when_nothing_observed(conn) -> None:
    upsert_mapping(conn, depotid=441, appid=440, name="TF2")

    change = prefill.apply_observed_mapping(conn, 440, set())

    assert change.skipped_empty is True
    assert "left unchanged" in change.summary()
    depots = {
        row["depotid"]
        for row in conn.execute("SELECT depotid FROM depot_app_map WHERE appid = 440")
    }
    assert depots == {441}


def test_apply_observed_mapping_creates_the_app_row_for_a_new_title(conn) -> None:
    prefill.apply_observed_mapping(conn, 12345, {1, 2})

    row = conn.execute("SELECT appid FROM apps WHERE appid = 12345").fetchone()
    assert row is not None
