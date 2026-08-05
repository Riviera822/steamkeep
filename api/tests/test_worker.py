"""End-to-end: HTTP enqueue -> background worker -> job/app/mapping state.

Uses the fake SteamPrefill from tests/stub_prefill.py — no Steam login, no
network. ``TestClient`` is used as a context manager here so the FastAPI
lifespan (stale-job recovery + worker thread) actually runs.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests import stub_prefill
from tests.conftest import TEST_API_KEY
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}


@pytest.fixture
def bindir(tmp_path: Path) -> Path:
    return tmp_path / "bin"


@pytest.fixture
def cache_root(tmp_path: Path) -> Path:
    return tmp_path / "cache"


def make_settings(
    tmp_path: Path,
    cache_root: Path,
    steamprefill_path: str = "",
    prefill_timeout_seconds: int = 60,
    steamprefill_cache_dir: str | None = None,
    manifest_archive_dir: str | None = None,
    manifest_keep: int = 3,
) -> Settings:
    return Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(cache_root),
        log_level="INFO",
        steamprefill_path=steamprefill_path,
        prefill_timeout_seconds=prefill_timeout_seconds,
        # Fast polling so the tests don't sit around waiting for a tick.
        worker_poll_seconds=0.02,
        # WP 3.2: unless a test wires up a real manifest temp-cache dir, this
        # deliberately points at a directory that never exists -- manifest
        # ingestion then safely no-ops (cache_dir_unavailable), which is
        # exactly the production behavior on a host that hasn't set
        # VAULT_STEAMPREFILL_CACHE_DIR up yet, and keeps every pre-3.2 test
        # in this module unaffected by ingestion.
        steamprefill_cache_dir=steamprefill_cache_dir
        or str(tmp_path / "unused-steamprefill-cache"),
        manifest_archive_dir=manifest_archive_dir or str(tmp_path / "manifest-archive"),
        manifest_keep=manifest_keep,
    )


def wait_for_job(client: TestClient, job_id: int, timeout: float = 30.0) -> dict:
    """Poll GET /v1/jobs/{id} until the job reaches a terminal status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.05)
    pytest.fail(f"job {job_id} did not finish within {timeout}s: {job}")


def enqueue(client: TestClient, *appids: int) -> list[int]:
    response = client.post("/v1/prefill", json={"appids": list(appids)}, headers=AUTH)
    assert response.status_code == 202, response.text
    return [entry["job_id"] for entry in response.json()]


# -- happy path ------------------------------------------------------------


def test_successful_job_updates_job_app_status_and_mapping(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [441, 442]}
    )
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)

        assert job["status"] == "done", job["log_excerpt"]
        assert job["started_at"] is not None
        assert job["finished_at"] is not None
        assert "Prefill complete!" in job["log_excerpt"]
        assert "Depot mapping updated" in job["log_excerpt"]

        game = client.get("/v1/games/440", headers=AUTH).json()
        assert game["status"] == "done"
        assert game["last_prefill_at"] is not None
        assert {depot["depotid"] for depot in game["depots"]} == {441, 442}

    # Selection file proves the verified non-interactive mechanism was used.
    assert stub_prefill.read_selection(bindir) == [440]


def test_worker_replaces_stale_mapping_but_keeps_shared_depots(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """ADR-0003 decision 3, through the full stack."""
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [300, 301]}
    )
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        # Pre-existing mapping: 300 shared with 730, 999 about to go stale.
        client.put("/v1/mapping/300", json={"appid": 440}, headers=AUTH)
        client.put("/v1/mapping/300", json={"appid": 730}, headers=AUTH)
        client.put("/v1/mapping/999", json={"appid": 440}, headers=AUTH)

        (job_id,) = enqueue(client, 440)
        assert wait_for_job(client, job_id)["status"] == "done"

        pairs = {
            (entry["depotid"], entry["appid"])
            for entry in client.get("/v1/mapping", headers=AUTH).json()
        }
        assert pairs == {(300, 440), (300, 730), (301, 440)}

        # 300 is still reported as shared by both apps.
        for appid in (440, 730):
            detail = client.get(f"/v1/games/{appid}", headers=AUTH).json()
            shared = {d["depotid"] for d in detail["depots"] if d["shared"]}
            assert 300 in shared


def test_nothing_new_observed_leaves_the_mapping_untouched(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """The already-fully-cached case: no writes, so no evidence, so no change."""
    executable = stub_prefill.make_stub(bindir, mode="noop", cache_root=str(cache_root))
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        client.put("/v1/mapping/441", json={"appid": 440}, headers=AUTH)

        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)

        assert job["status"] == "done"
        assert "left unchanged" in job["log_excerpt"]
        detail = client.get("/v1/games/440", headers=AUTH).json()
        assert [d["depotid"] for d in detail["depots"]] == [441]
        assert detail["status"] == "done"


def test_successful_job_invalidates_the_size_cache(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """WP 1.5: plan §3's size calculation is "cached" — a successful prefill
    must invalidate it, or GET /v1/games would keep reporting a game's
    pre-prefill (null/stale) size for up to VAULT_SIZE_CACHE_TTL seconds.

    Uses the default 60s TTL (make_settings doesn't override it) so this only
    passes if the worker actively invalidates — a passive TTL expiry could
    never make it through in the test's runtime.
    """
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [441]}
    )
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        client.put("/v1/mapping/441", json={"appid": 440}, headers=AUTH)

        # Populate the size cache with a "nothing on disk yet" snapshot,
        # exactly like a real app poll would before the first prefill.
        before = client.get("/v1/games/440", headers=AUTH).json()
        assert before["size_bytes"] is None

        (job_id,) = enqueue(client, 440)
        assert wait_for_job(client, job_id)["status"] == "done"

        after = client.get("/v1/games/440", headers=AUTH).json()
        assert after["size_bytes"] is not None
        assert after["size_bytes"] > 0


# -- manifest ingestion (WP 3.2) --------------------------------------------


def test_successful_job_ingests_a_dropped_manifest_bin_end_to_end(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """Full stack: HTTP enqueue -> worker runs the stub -> stub drops a
    synthetic SteamPrefill manifest .bin into a fake temp-cache dir -> the
    worker's post-success ingestion step parses it, records depot_manifests,
    and archives it -- exactly what a real SteamPrefill run would trigger."""
    from tests.test_manifests import _bin_manifest_bytes, _chunk_id
    from vault_api.db import get_connection
    from vault_api.depot_manifests import get_depot_manifest

    steamprefill_cache_dir = tmp_path / "steamprefill-cache"
    manifest_archive_dir = tmp_path / "manifest-archive"

    manifest_bytes = _bin_manifest_bytes(
        depot_id=441, manifest_id=555, files=[[(_chunk_id(1), 1000)]]
    )
    executable = stub_prefill.make_stub(
        bindir,
        cache_root=str(cache_root),
        depots_by_app={440: [441]},
        manifest_bins=[
            {
                "dir": str(steamprefill_cache_dir),
                "filename": "440_440_441_555.bin",
                "data": manifest_bytes,
            }
        ],
    )
    settings = make_settings(
        tmp_path,
        cache_root,
        executable,
        steamprefill_cache_dir=str(steamprefill_cache_dir),
        manifest_archive_dir=str(manifest_archive_dir),
    )
    app = create_app(settings)

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)

        assert job["status"] == "done"
        assert "Manifest ingestion: recorded 1 manifest" in job["log_excerpt"]

    conn = get_connection(settings.db_path)
    try:
        row = get_depot_manifest(conn, appid=440, depotid=441)
    finally:
        conn.close()

    assert row is not None
    assert row["manifestid"] == "555"
    assert row["chunk_count"] == 1
    assert (manifest_archive_dir / "441_555.bin").exists()


def test_ingestion_failure_never_flips_a_successful_job_to_error(
    tmp_path: Path, bindir: Path, cache_root: Path, monkeypatch
) -> None:
    """A crash inside manifest ingestion must not undo a successful prefill
    (worker.py's local try/except around ingest_after_prefill)."""
    import vault_api.worker as worker_module

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated ingestion bug")

    monkeypatch.setattr(worker_module.manifest_ingest, "ingest_after_prefill", boom)

    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [441]}
    )
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)

        assert job["status"] == "done"
        assert "Manifest ingestion crashed" in job["log_excerpt"]
        assert client.get("/v1/games/440", headers=AUTH).json()["status"] == "done"


# -- one at a time ---------------------------------------------------------


def test_jobs_run_strictly_one_at_a_time_in_fifo_order(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    executable = stub_prefill.make_stub(
        bindir,
        cache_root=str(cache_root),
        depots_by_app={10: [110], 20: [120], 30: [130]},
        sleep_seconds=0.3,
    )
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        job_ids = enqueue(client, 10, 20, 30)
        for job_id in job_ids:
            assert wait_for_job(client, job_id, timeout=60)["status"] == "done"

    runs = stub_prefill.read_runs(bindir)
    assert [run["selected"] for run in runs] == [[10], [20], [30]]

    # No two runs overlap in wall-clock time -> exactly one job at a time.
    for earlier, later in zip(runs, runs[1:]):
        assert earlier["finished"] <= later["started"], runs


# -- failure paths ---------------------------------------------------------


def test_failed_job_sets_error_status_and_does_not_touch_the_mapping(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    executable = stub_prefill.make_stub(bindir, mode="fail", exit_code=4)
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        client.put("/v1/mapping/441", json={"appid": 440}, headers=AUTH)

        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)

        assert job["status"] == "error"
        assert "simulated depot download failure" in job["log_excerpt"]
        assert "exited with code 4" in job["log_excerpt"]
        assert "left unchanged" in job["log_excerpt"]

        detail = client.get("/v1/games/440", headers=AUTH).json()
        assert detail["status"] == "error"
        assert detail["last_prefill_at"] is None
        assert [d["depotid"] for d in detail["depots"]] == [441]


def test_not_logged_in_job_fails_with_the_interactive_login_instruction(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    executable = stub_prefill.make_stub(bindir, mode="not_logged_in")
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)

        assert job["status"] == "error"
        assert "select-apps" in job["log_excerpt"]
        assert "Steam Guard" in job["log_excerpt"]


def test_hanging_prefill_is_timed_out_and_the_queue_keeps_draining(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    executable = stub_prefill.make_stub(bindir, mode="hang")
    app = create_app(make_settings(tmp_path, cache_root, executable, prefill_timeout_seconds=1))

    with TestClient(app) as client:
        (first, second) = enqueue(client, 440, 730)

        assert wait_for_job(client, first, timeout=30)["status"] == "error"
        job = client.get(f"/v1/jobs/{first}", headers=AUTH).json()
        assert "time budget" in job["log_excerpt"]

        # A wedged job must not stall the queue forever.
        assert wait_for_job(client, second, timeout=30)["status"] == "error"


def test_missing_steamprefill_path_fails_the_job_but_not_the_app(
    tmp_path: Path, cache_root: Path
) -> None:
    app = create_app(make_settings(tmp_path, cache_root, steamprefill_path=""))

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)

        assert job["status"] == "error"
        assert "VAULT_STEAMPREFILL_PATH is not set" in job["log_excerpt"]

        # The rest of the API is unaffected — that's the whole point.
        assert client.get("/v1/health").status_code == 200
        assert client.get("/v1/games", headers=AUTH).status_code == 200
        assert client.get("/v1/games/440", headers=AUTH).json()["status"] == "error"


# -- lifecycle -------------------------------------------------------------


def test_startup_recovers_a_job_left_running_by_a_dead_process(
    tmp_path: Path, cache_root: Path
) -> None:
    settings = make_settings(tmp_path, cache_root)
    init_db(settings.db_path)

    conn = get_connection(settings.db_path)
    try:
        conn.execute(
            """
            INSERT INTO jobs (id, appid, type, status, created_at, started_at)
            VALUES (77, 440, 'prefill', 'running', '2026-08-05T10:00:00Z', '2026-08-05T10:00:01Z')
            """
        )
        conn.execute("INSERT INTO apps (appid, status) VALUES (440, 'running')")
        conn.commit()
    finally:
        conn.close()

    with TestClient(create_app(settings)) as client:
        job = client.get("/v1/jobs/77", headers=AUTH).json()
        assert job["status"] == "error"
        assert job["finished_at"] is not None
        assert "died" in job["log_excerpt"]

        assert client.get("/v1/games/440", headers=AUTH).json()["status"] == "error"

        # The dead job no longer blocks a fresh request for the same app.
        (new_job_id,) = enqueue(client, 440)
        assert new_job_id != 77


def test_shutdown_aborts_a_running_job_instead_of_hanging(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    executable = stub_prefill.make_stub(bindir, mode="hang")
    settings = make_settings(tmp_path, cache_root, executable, prefill_timeout_seconds=600)
    app = create_app(settings)

    started = time.monotonic()
    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        # Wait until the worker has actually claimed and started it.
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()["status"] == "running":
                break
            time.sleep(0.05)
        else:  # pragma: no cover
            pytest.fail("worker never claimed the job")
    elapsed = time.monotonic() - started

    # Leaving the context manager runs shutdown; it must not wait out the
    # 600s prefill timeout.
    assert elapsed < 30, elapsed

    conn = get_connection(settings.db_path)
    try:
        row = conn.execute("SELECT status, log_excerpt FROM jobs WHERE id = ?", (job_id,)).fetchone()
    finally:
        conn.close()
    assert row["status"] == "error"
    assert "shutting down" in row["log_excerpt"]


def test_worker_only_runs_when_the_lifespan_runs(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """Guards the other test modules: no ``with`` -> no worker -> stable state."""
    executable = stub_prefill.make_stub(bindir, cache_root=str(cache_root))
    client = TestClient(create_app(make_settings(tmp_path, cache_root, executable)))

    (job_id,) = enqueue(client, 440)
    time.sleep(0.3)

    assert client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()["status"] == "queued"
    assert stub_prefill.read_runs(bindir) == []
