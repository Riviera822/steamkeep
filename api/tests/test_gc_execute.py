"""GC execution (``vault_api/gc_execute.py`` + ``POST /v1/cache/{appid}/gc``, WP 3.8).

This is the deletion half of garbage collection, so nothing here is mocked:
every test builds a real cache tree (real depot directories, real chunk files
with real byte sizes, real ZIP/protobuf manifests — the builders are imported
from ``tests/test_gc.py``, which imports its encoders from
``tests/test_manifests.py``, so the suite still has exactly ONE manifest
encoder and it stays independent of the reader under test) and then checks the
filesystem afterwards.

The three things these tests are built around:

- **Byte-exact accounting.** Freed bytes are asserted against the real sizes
  written to disk, and the *whole tree* is snapshotted before and after so
  "deleted exactly the planned set and nothing else" is a statement about every
  file under the cache root, not just about the ones a test remembered to look
  at.
- **The fail-closed directions.** Dry-run-by-default, "only a ``planned`` depot
  is touched", "only a name the planner produced is deleted", "a link is never
  unlinked", "the last copy of a manifest is never removed" — each has a test
  that dies when the branch is flipped (the mutation list is in the work
  package report).
- **Racing removals.** A concurrent ``DELETE`` really does run against the same
  tree here (threads + a barrier, looped), because full-suite green means
  nothing for timing bugs (docs/LEARNINGS.md, WP 1.6).
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY, make_dir_link
from tests.test_gc import (
    _cid,
    depot_dir,
    touch_newer,
    write_archive_bin,
    write_cache_manifest,
    write_chunks,
)
from vault_api import deletion, gc, gc_execute, jobs
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app
from vault_api.sizes import SizeCache

AUTH = {"X-Api-Key": TEST_API_KEY}

#: The standard world every scenario below starts from: depot 441 of app 440,
#: two chunks the current manifest still needs and two left over from an
#: earlier version.
KEEP_A, KEEP_B = _cid(0xA1), _cid(0xA2)
ORPHAN_A, ORPHAN_B = _cid(0xB1), _cid(0xB2)
KEEP_SIZES = {KEEP_A: 100, KEEP_B: 200}
ORPHAN_SIZES = {ORPHAN_A: 300, ORPHAN_B: 400}
CURRENT_MANIFEST = "900"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        worker_poll_seconds=0.02,
        manifest_archive_dir=str(tmp_path / "manifests"),
        # Points at a directory that never exists: manifest ingestion is not
        # part of this work package and must not run.
        steamprefill_cache_dir=str(tmp_path / "unused-steamprefill-cache"),
    )


def cache_root(settings: Settings) -> Path:
    return Path(settings.cache_root)


def archive_dir(settings: Settings) -> Path:
    return Path(settings.manifest_archive_dir)


def open_db(settings: Settings) -> sqlite3.Connection:
    init_db(settings.db_path)
    return get_connection(settings.db_path)


def seed_app(conn: sqlite3.Connection, appid: int, *, needs_force: int = 0) -> None:
    conn.execute(
        "INSERT INTO apps (appid, status, needs_force) VALUES (?, 'idle', ?) "
        "ON CONFLICT(appid) DO UPDATE SET needs_force = excluded.needs_force",
        (appid, needs_force),
    )
    conn.commit()


def seed_mapping(conn: sqlite3.Connection, depotid: int, appid: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO depot_app_map (depotid, appid) VALUES (?, ?)",
        (depotid, appid),
    )
    conn.commit()


def seed_recorded_manifest(
    conn: sqlite3.Connection, *, appid: int, depotid: int, manifestid: str
) -> None:
    conn.execute(
        """
        INSERT INTO depot_manifests
            (appid, containing_appid, depotid, manifestid, chunk_count,
             total_bytes, recorded_at, source)
        VALUES (?, NULL, ?, ?, 0, 0, '2026-08-09T00:00:00Z', 'steamprefill_bin')
        ON CONFLICT(appid, depotid) DO UPDATE SET manifestid = excluded.manifestid
        """,
        (appid, depotid, manifestid),
    )
    conn.commit()


def needs_force_of(conn: sqlite3.Connection, appid: int) -> int | None:
    row = conn.execute(
        "SELECT needs_force FROM apps WHERE appid = ?", (appid,)
    ).fetchone()
    return None if row is None else int(row["needs_force"])


def snapshot(root: Path) -> dict[str, int]:
    """``{path relative to root: byte size}`` for every file under ``root``.

    The whole point of taking it over the entire cache root (and the archive)
    is that "nothing else was touched" then covers files no test thought to
    name — kept chunks, stored manifests, other depots, unrelated junk.
    """
    found: dict[str, int] = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = Path(dirpath) / name
            found[str(full.relative_to(root))] = full.stat().st_size
    return found


def build_world(tmp_path: Path) -> Settings:
    """The standard scenario: app 440 -> depot 441, two kept + two orphan chunks.

    Also drops an unrelated, unmapped depot 999 into the tree: every "nothing
    else was touched" assertion then has something to be about that GC has no
    business knowing at all.
    """
    settings = make_settings(tmp_path)
    root = cache_root(settings)
    (root / "depot").mkdir(parents=True)

    write_chunks(root, 441, {**KEEP_SIZES, **ORPHAN_SIZES})
    write_archive_bin(
        archive_dir(settings),
        depotid=441,
        manifestid=CURRENT_MANIFEST,
        chunks=KEEP_SIZES,
    )
    write_chunks(root, 999, {_cid(0xF1): 55})

    conn = open_db(settings)
    try:
        seed_app(conn, 440)
        seed_mapping(conn, 441, 440)
        seed_recorded_manifest(conn, appid=440, depotid=441, manifestid=CURRENT_MANIFEST)
    finally:
        conn.close()
    return settings


def run(settings: Settings, appid: int = 440, *, execute: bool) -> gc_execute.GcRunReport:
    conn = open_db(settings)
    try:
        return gc_execute.run_gc(
            conn,
            appid,
            cache_root=settings.cache_root,
            archive_dir=settings.manifest_archive_dir,
            execute=execute,
        )
    finally:
        conn.close()


def chunk_path(settings: Settings, depotid: int, chunk_id: str) -> Path:
    return depot_dir(cache_root(settings), depotid) / "chunk" / chunk_id


# ==========================================================================
# Dry run: the default, and it deletes nothing
# ==========================================================================


def test_dry_run_deletes_nothing_on_disk(tmp_path: Path) -> None:
    settings = build_world(tmp_path)
    before = snapshot(cache_root(settings)) | snapshot(archive_dir(settings))

    report = run(settings, execute=False)

    assert report.requested_execute is False
    assert report.executed is False
    assert report.ok is True
    # It still SAW the orphans — a dry run that reports nothing would pass a
    # "deleted nothing" assertion trivially.
    assert report.planned_orphan_count == 2
    assert report.planned_orphan_bytes == 700
    assert report.removed_count == 0
    assert report.bytes_freed == 0
    assert snapshot(cache_root(settings)) | snapshot(archive_dir(settings)) == before
    assert "DRY RUN" in report.log_text()
    assert "NOTHING was deleted" in report.log_text()


def test_dry_run_never_sets_needs_force(tmp_path: Path) -> None:
    """The state-bookkeeping decision, dry-run half: a report changes nothing.

    Mutation target: making ``run_gc`` flag apps regardless of ``execute``
    kills this test.
    """
    settings = build_world(tmp_path)

    report = run(settings, execute=False)

    assert report.flagged_appids == []
    conn = open_db(settings)
    try:
        assert needs_force_of(conn, 440) == 0
    finally:
        conn.close()


# ==========================================================================
# Execute: exactly the planned set, byte-exact, and nothing else
# ==========================================================================


def test_execute_removes_exactly_the_planned_orphans_and_nothing_else(
    tmp_path: Path,
) -> None:
    settings = build_world(tmp_path)
    before = snapshot(cache_root(settings)) | snapshot(archive_dir(settings))

    # Plan first, so the executed numbers can be compared against the dry
    # run's own prediction as well as against the disk.
    plan_report = run(settings, execute=False)
    assert plan_report.planned_orphan_count == 2

    report = run(settings, execute=True)

    assert report.executed is True
    assert report.ok is True
    assert report.removed_count == 2
    # Byte-exact against the sizes actually written, and against the plan's own
    # numbers from the dry run a moment ago.
    assert report.removed_bytes == 700 == plan_report.planned_orphan_bytes
    assert report.bytes_freed == 700
    assert report.already_gone_count == 0
    assert report.problems == []

    after = snapshot(cache_root(settings)) | snapshot(archive_dir(settings))
    removed_paths = set(before) - set(after)
    assert removed_paths == {
        str(Path("depot") / "441" / "chunk" / ORPHAN_A),
        str(Path("depot") / "441" / "chunk" / ORPHAN_B),
    }
    # Nothing else changed AT ALL — not the kept chunks, not the unmapped
    # depot 999, not the archive.
    assert {path: size for path, size in after.items()} == {
        path: size for path, size in before.items() if path not in removed_paths
    }
    assert sum(before[path] for path in removed_paths) == report.removed_bytes


def test_execute_keeps_every_chunk_the_current_manifest_still_needs(
    tmp_path: Path,
) -> None:
    settings = build_world(tmp_path)

    run(settings, execute=True)

    for chunk_id, size in KEEP_SIZES.items():
        path = chunk_path(settings, 441, chunk_id)
        assert path.exists(), f"kept chunk {chunk_id} was deleted"
        assert path.stat().st_size == size


def test_second_execute_is_a_clean_no_op(tmp_path: Path) -> None:
    settings = build_world(tmp_path)
    assert run(settings, execute=True).removed_count == 2

    report = run(settings, execute=True)

    assert report.ok is True
    assert report.removed_count == 0
    assert report.bytes_freed == 0
    assert report.planned_orphan_count == 0


def test_shared_depot_keeps_a_chunk_the_other_app_still_needs(tmp_path: Path) -> None:
    """The union rule, executed: a chunk that is an orphan for app 440 but part
    of app 730's current manifest survives."""
    settings = make_settings(tmp_path)
    root = cache_root(settings)
    (root / "depot").mkdir(parents=True)

    shared = _cid(0xC1)
    write_chunks(root, 900, {**KEEP_SIZES, shared: 512, ORPHAN_A: 300})
    write_archive_bin(
        archive_dir(settings), depotid=900, manifestid="900", chunks=KEEP_SIZES
    )
    write_archive_bin(
        archive_dir(settings), depotid=900, manifestid="901", chunks={shared: 512}
    )

    conn = open_db(settings)
    try:
        seed_app(conn, 440)
        seed_app(conn, 730)
        seed_mapping(conn, 900, 440)
        seed_mapping(conn, 900, 730)
        seed_recorded_manifest(conn, appid=440, depotid=900, manifestid="900")
        seed_recorded_manifest(conn, appid=730, depotid=900, manifestid="901")
    finally:
        conn.close()

    report = run(settings, 440, execute=True)

    assert report.removed_count == 1
    assert report.removed_bytes == 300
    assert (depot_dir(root, 900) / "chunk" / shared).exists()
    assert not (depot_dir(root, 900) / "chunk" / ORPHAN_A).exists()


# ==========================================================================
# Skipped depots lose nothing — of any kind
# ==========================================================================


def test_a_skipped_depot_loses_no_file_of_any_kind(tmp_path: Path) -> None:
    """Depot 441 is skipped (its recorded manifest resolves from nowhere), and
    it also holds THREE copies of one stored manifest — i.e. a dedupe
    opportunity. Neither the chunks nor the redundant manifest copies may be
    touched: "GC never touches a depot it refused to plan" has no exceptions.

    Mutation target: dropping ``execute_depot``'s status gate, or moving the
    dedupe call above it, kills this test.
    """
    settings = make_settings(tmp_path)
    root = cache_root(settings)
    (root / "depot").mkdir(parents=True)

    write_chunks(root, 441, {**KEEP_SIZES, **ORPHAN_SIZES})
    for index, code in enumerate(("codeA", "codeB", "codeC")):
        path = write_cache_manifest(
            root, depotid=441, manifestid="777", chunks=KEEP_SIZES, request_code=code
        )
        touch_newer(path, seconds=index)
    # A recorded manifest id that exists nowhere on disk -> readiness gate.
    conn = open_db(settings)
    try:
        seed_app(conn, 440)
        seed_mapping(conn, 441, 440)
        seed_recorded_manifest(conn, appid=440, depotid=441, manifestid="123456")
    finally:
        conn.close()

    before = snapshot(root)
    report = run(settings, execute=True)

    assert [d.status for d in report.depots] == [gc.STATUS_NO_MANIFEST]
    assert report.depots[0].executed is False
    assert report.ok is True, "a reported skip is not a failure"
    assert report.removed_count == 0
    assert report.dedupe_removed_count == 0
    assert snapshot(root) == before


def test_a_depot_with_an_unreadable_co_owner_loses_nothing(tmp_path: Path) -> None:
    settings = build_world(tmp_path)
    conn = open_db(settings)
    try:
        # A poisoned mapping row: SQLite's INTEGER affinity does not stop it.
        conn.execute(
            "INSERT INTO depot_app_map (depotid, appid) VALUES (441, 'not-an-appid')"
        )
        conn.commit()
    finally:
        conn.close()

    before = snapshot(cache_root(settings))
    report = run(settings, execute=True)

    assert [d.status for d in report.depots] == [gc.STATUS_UNREADABLE_OWNER]
    assert report.removed_count == 0
    assert snapshot(cache_root(settings)) == before


def test_orphans_to_delete_refuses_a_non_planned_plan() -> None:
    """The second gate, independent of the planner: even a (impossible) plan
    that carries orphans under a skip status deletes nothing.

    Mutation target: removing ``orphans_to_delete``'s status check kills this.
    """
    poisoned = gc.DepotGcPlan(
        depotid=441,
        status=gc.STATUS_NO_MANIFEST,
        orphan_chunks={ORPHAN_A: 300},
    )

    to_delete, held_back = gc_execute.orphans_to_delete(poisoned)

    assert to_delete == {}
    assert held_back == {}


def test_execute_depot_refuses_a_non_planned_plan_carrying_orphans(
    tmp_path: Path,
) -> None:
    """The same guarantee one layer up, against a real file on disk."""
    settings = build_world(tmp_path)
    depot_root = deletion.resolve_depot_root(settings.cache_root)
    poisoned = gc.DepotGcPlan(
        depotid=441,
        status=gc.STATUS_UNMAPPED,
        orphan_chunks={ORPHAN_A: 300},
    )

    result = gc_execute.execute_depot(poisoned, depot_root=depot_root)

    assert result.executed is False
    assert result.removed_count == 0
    assert chunk_path(settings, 441, ORPHAN_A).exists()


# ==========================================================================
# Only a name the planner produced is ever deleted
# ==========================================================================


@pytest.mark.parametrize(
    "poisoned_name",
    [
        "../../../../etc/passwd",
        "..",
        ORPHAN_A.upper(),
        ORPHAN_A[:-1],
        "chunk.tmp",
        "",
    ],
)
def test_a_chunk_the_planner_did_not_name_is_never_deleted(
    tmp_path: Path, poisoned_name: str
) -> None:
    """A hand-built plan carrying a name ``plan_gc`` could never produce is
    refused by the executor itself — the guarantee does not depend on the
    planner staying careful.

    Mutation target: removing ``_remove_planned_chunk``'s ``is_chunk_filename``
    re-validation kills this test (the traversal case reaches
    ``safe_child_path``, but the uppercase/truncated cases would silently
    become deletions of real files if such files existed).
    """
    settings = build_world(tmp_path)
    depot_root = deletion.resolve_depot_root(settings.cache_root)
    before = snapshot(cache_root(settings))

    result = gc_execute.execute_depot(
        gc.DepotGcPlan(
            depotid=441,
            status=gc.STATUS_PLANNED,
            orphan_chunks={poisoned_name: 1},
        ),
        depot_root=depot_root,
    )

    assert result.removed_count == 0
    assert [p.outcome for p in result.problems] == [gc_execute.REFUSED_UNSAFE_NAME]
    assert result.ok is False
    assert snapshot(cache_root(settings)) == before


def test_a_chunk_that_became_a_link_is_refused_not_unlinked(tmp_path: Path) -> None:
    """A name that is a link is not the file the plan named. It is refused —
    and the link (plus, obviously, its target) survives.

    Mutation target: dropping ``remove_one_file``'s link check kills this.
    """
    settings = make_settings(tmp_path)
    root = cache_root(settings)
    (root / "depot").mkdir(parents=True)
    write_chunks(root, 441, KEEP_SIZES)

    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "keepme.bin").write_bytes(b"x" * 64)
    link = depot_dir(root, 441) / "chunk" / ORPHAN_A
    make_dir_link(link, outside)

    depot_root = deletion.resolve_depot_root(settings.cache_root)
    result = gc_execute.execute_depot(
        gc.DepotGcPlan(
            depotid=441, status=gc.STATUS_PLANNED, orphan_chunks={ORPHAN_A: 1}
        ),
        depot_root=depot_root,
    )

    assert [p.outcome for p in result.problems] == [gc_execute.REFUSED_LINK]
    assert result.removed_count == 0
    assert os.path.lexists(link), "the link itself was removed"
    assert (outside / "keepme.bin").read_bytes() == b"x" * 64


def test_a_directory_named_like_a_chunk_is_refused(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    root = cache_root(settings)
    (root / "depot").mkdir(parents=True)
    write_chunks(root, 441, KEEP_SIZES)
    trap = depot_dir(root, 441) / "chunk" / ORPHAN_A
    trap.mkdir()
    (trap / "inside.bin").write_bytes(b"y" * 8)

    result = gc_execute.execute_depot(
        gc.DepotGcPlan(
            depotid=441, status=gc.STATUS_PLANNED, orphan_chunks={ORPHAN_A: 1}
        ),
        depot_root=deletion.resolve_depot_root(settings.cache_root),
    )

    assert [p.outcome for p in result.problems] == [gc_execute.REFUSED_NOT_A_FILE]
    assert (trap / "inside.bin").exists()


def test_the_manifest_subtree_is_never_touched(tmp_path: Path) -> None:
    """``manifest/`` filenames are per-request codes, not chunk ids — a GC that
    walked the depot directory would classify all of them as orphans."""
    settings = build_world(tmp_path)
    stored = write_cache_manifest(
        cache_root(settings), depotid=441, manifestid=CURRENT_MANIFEST,
        chunks=KEEP_SIZES, request_code="requestcode0001",
    )

    report = run(settings, execute=True)

    assert report.removed_count == 2
    assert stored.exists()


# ==========================================================================
# TOCTOU: the plan is built at execution time, never at enqueue time
# ==========================================================================


def test_the_plan_is_recomputed_at_execution_not_at_enqueue(tmp_path: Path) -> None:
    """End to end through the queue: the world changes between the 202 and the
    worker running the job, and the FRESH plan governs.

    At enqueue time ORPHAN_A/ORPHAN_B are orphans. Before the job runs, the
    game "updates": a new manifest is archived and recorded that needs
    ORPHAN_A, while KEEP_B drops out of the current manifest. The executed run
    must therefore delete KEEP_B and keep ORPHAN_A — the exact opposite of the
    enqueue-time picture for both files.
    """
    settings = build_world(tmp_path)
    client = TestClient(create_app(settings))  # no lifespan -> no worker running

    queued = client.post("/v1/cache/440/gc", json={"execute": True}, headers=AUTH)
    assert queued.status_code == 202, queued.text

    # --- the world moves on while the job sits in the queue ---
    new_manifest = "901"
    write_archive_bin(
        archive_dir(settings),
        depotid=441,
        manifestid=new_manifest,
        chunks={KEEP_A: 100, ORPHAN_A: 300},
    )
    conn = open_db(settings)
    try:
        seed_recorded_manifest(
            conn, appid=440, depotid=441, manifestid=new_manifest
        )
        job = jobs.claim_next_job(conn)
        assert job is not None and job["type"] == "gc"
        gc_execute.run_gc_job(conn, job, settings=settings)
        finished = jobs.get_job(conn, int(job["id"]))
    finally:
        conn.close()

    assert finished["status"] == "done", finished["log_excerpt"]
    assert chunk_path(settings, 441, KEEP_A).exists()
    assert chunk_path(settings, 441, ORPHAN_A).exists(), (
        "the enqueue-time plan was executed: a chunk the CURRENT manifest needs "
        "was deleted"
    )
    assert not chunk_path(settings, 441, KEEP_B).exists()
    assert not chunk_path(settings, 441, ORPHAN_B).exists()


def test_run_gc_has_no_way_to_be_handed_a_plan() -> None:
    """Structural, not behavioural: ``run_gc`` takes an app id and builds the
    plan itself, so "executed a stale plan" is not a bug this code can have.
    A future parameter that reintroduced it would fail here."""
    import inspect

    parameters = set(inspect.signature(gc_execute.run_gc).parameters)
    assert "plan" not in parameters
    assert parameters == {
        "conn", "appid", "cache_root", "archive_dir", "execute", "exclusions",
    }


# ==========================================================================
# Racing removals
# ==========================================================================


@pytest.mark.parametrize("iteration", range(10))
def test_a_concurrent_delete_never_makes_gc_lie_or_fail(
    tmp_path: Path, iteration: int
) -> None:
    """A ``DELETE /v1/cache/{appid}``-style ``rmtree`` runs against the very
    depot GC is unlinking chunks out of, released from a barrier so the two
    really overlap.

    What must hold, every time: no exception escapes, the run is still
    reported ``ok`` (a racing deleter is not a failure), and the bytes claimed
    freed never exceed the bytes that actually existed — two actors must never
    both claim the same space.

    Parametrised 10x here and flake-hunted by running this module isolated
    30x (docs/LEARNINGS.md: full-suite green means nothing for timing bugs).
    """
    settings = build_world(tmp_path)
    depot = depot_dir(cache_root(settings), 441)
    total_bytes = sum(f.stat().st_size for f in depot.rglob("*") if f.is_file())

    barrier = threading.Barrier(2)
    racer_error: list[BaseException] = []

    def racing_delete() -> None:
        try:
            barrier.wait(timeout=10)
            deletion.remove_depot_dir_settling(str(depot))
        except BaseException as exc:  # noqa: BLE001 - recorded, not raised here
            racer_error.append(exc)

    thread = threading.Thread(target=racing_delete)
    thread.start()
    try:
        barrier.wait(timeout=10)
        report = run(settings, execute=True)
    finally:
        thread.join(timeout=10)

    assert not racer_error, racer_error
    assert report.ok is True, [p.describe() for p in report.problems]
    assert report.bytes_freed <= total_bytes
    # Whatever the interleaving, the orphans are gone afterwards.
    assert not chunk_path(settings, 441, ORPHAN_A).exists()
    assert not chunk_path(settings, 441, ORPHAN_B).exists()


def test_a_chunk_removed_by_someone_else_frees_zero_bytes(tmp_path: Path) -> None:
    """The deterministic half of the race: a planned chunk that vanishes
    between the plan and the unlink is ``already_gone`` and contributes 0
    bytes, so a second actor cannot claim the same space.

    Mutation target: attributing ``bytes_freed`` on the ``already_gone`` path
    kills this test.
    """
    settings = build_world(tmp_path)
    depot_root = deletion.resolve_depot_root(settings.cache_root)
    plan = gc.plan_gc(
        440,
        gc.GcInputs(
            mapping_rows=[(441, 440)],
            content_states={440: True},
            recorded_manifests={(440, 441): CURRENT_MANIFEST},
        ),
        depot_root=depot_root,
        archive_dir=settings.manifest_archive_dir,
    )
    # Somebody else removes one of the planned orphans first.
    chunk_path(settings, 441, ORPHAN_A).unlink()

    result = gc_execute.execute_depot(plan.depots[0], depot_root=depot_root)

    assert result.planned_orphan_count == 2
    assert result.removed_count == 1
    assert result.removed_bytes == 400, "only the chunk this run removed counts"
    assert result.already_gone_count == 1
    assert result.ok is True
    assert result.chunks_gone == 2


# ==========================================================================
# Partial-failure honesty
# ==========================================================================


def test_a_failed_removal_is_reported_exactly_and_never_as_done(
    tmp_path: Path,
) -> None:
    """One orphan is undeletable (an open handle on Windows / a
    ``remove_file_settling`` failure); the other is removed. The run must
    report BOTH facts: what really went, and that it did not finish.

    Mutation target: making ``GcRunReport.ok`` ignore problems kills this.
    """
    settings = build_world(tmp_path)
    stuck = chunk_path(settings, 441, ORPHAN_A)
    real_remove = deletion.remove_file_settling

    def refuse_one(path: str):
        if os.path.normpath(path) == os.path.normpath(str(stuck)):
            return False, PermissionError(13, "held by another process")
        return real_remove(path)

    conn = open_db(settings)
    try:
        original = gc_execute.deletion.remove_file_settling
        gc_execute.deletion.remove_file_settling = refuse_one  # type: ignore[assignment]
        try:
            report = gc_execute.run_gc(
                conn, 440,
                cache_root=settings.cache_root,
                archive_dir=settings.manifest_archive_dir,
                execute=True,
            )
        finally:
            gc_execute.deletion.remove_file_settling = original  # type: ignore[assignment]
    finally:
        conn.close()

    assert report.ok is False
    assert report.removed_count == 1
    assert report.removed_bytes == 400, "the bytes reported freed really were freed"
    assert [p.outcome for p in report.problems] == [gc_execute.FAILED]
    assert stuck.exists(), "reported failed but actually deleted"
    assert not chunk_path(settings, 441, ORPHAN_B).exists()

    text = report.log_text()
    assert "chunks_removed=1" in text
    assert "bytes_freed=400" in text
    assert "did NOT remove everything it planned" in text


def test_a_failed_run_still_flags_the_depot_it_did_change(tmp_path: Path) -> None:
    """Partial work is still work: the depot lost a chunk, so its apps are
    flagged even though the job ends 'error'."""
    settings = build_world(tmp_path)
    stuck = chunk_path(settings, 441, ORPHAN_A)
    real_remove = deletion.remove_file_settling

    def refuse_one(path: str):
        if os.path.normpath(path) == os.path.normpath(str(stuck)):
            return False, PermissionError(13, "held by another process")
        return real_remove(path)

    conn = open_db(settings)
    try:
        gc_execute.deletion.remove_file_settling = refuse_one  # type: ignore[assignment]
        try:
            report = gc_execute.run_gc(
                conn, 440,
                cache_root=settings.cache_root,
                archive_dir=settings.manifest_archive_dir,
                execute=True,
            )
        finally:
            gc_execute.deletion.remove_file_settling = real_remove  # type: ignore[assignment]
        assert report.ok is False
        assert needs_force_of(conn, 440) == 1
    finally:
        conn.close()


def test_an_unusable_cache_root_deletes_nothing_and_reports_error(
    tmp_path: Path,
) -> None:
    settings = build_world(tmp_path)
    broken = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=settings.db_path,
        cache_root="",  # verbatim: abspath('') would be the CWD
        log_level="INFO",
        manifest_archive_dir=settings.manifest_archive_dir,
    )
    before = snapshot(cache_root(settings))

    conn = open_db(settings)
    try:
        report = gc_execute.run_gc(
            conn, 440,
            cache_root=broken.cache_root,
            archive_dir=broken.manifest_archive_dir,
            execute=True,
        )
    finally:
        conn.close()

    assert report.plan_ok is False
    assert report.ok is False
    assert report.executed is False
    assert "VAULT_CACHE_ROOT is empty" in report.plan_error
    assert "NOTHING was planned, inspected or deleted" in report.log_text()
    assert snapshot(cache_root(settings)) == before


# ==========================================================================
# Dedupe execution
# ==========================================================================


def _three_copies(settings: Settings) -> list[Path]:
    root = cache_root(settings)
    paths = []
    for index, code in enumerate(("codeA", "codeB", "codeC")):
        path = write_cache_manifest(
            root,
            depotid=441,
            manifestid=CURRENT_MANIFEST,
            chunks=KEEP_SIZES,
            request_code=code,
        )
        touch_newer(path, seconds=index)
        paths.append(path)
    return paths


def test_dedupe_keeps_the_newest_copy_and_removes_the_identical_rest(
    tmp_path: Path,
) -> None:
    settings = build_world(tmp_path)
    copy_a, copy_b, copy_c = _three_copies(settings)  # codeC is the newest
    duplicate_bytes = copy_a.stat().st_size + copy_b.stat().st_size

    report = run(settings, execute=True)

    assert report.dedupe_removed_count == 2
    assert report.dedupe_removed_bytes == duplicate_bytes
    assert report.bytes_freed == 700 + duplicate_bytes
    assert copy_c.exists(), "the newest copy must survive"
    assert not copy_a.exists()
    assert not copy_b.exists()
    assert report.ok is True


def test_dedupe_keeps_a_duplicate_whose_bytes_differ(tmp_path: Path) -> None:
    """Same manifest id, different bytes: one of them is corrupt/truncated, and
    since dedupe keeps the NEWEST copy, blindly removing the others could throw
    away the only good one.

    Mutation target: removing the ``files_are_identical`` check kills this.
    """
    settings = build_world(tmp_path)
    copy_a, copy_b, copy_c = _three_copies(settings)
    copy_a.write_bytes(copy_a.read_bytes()[:-5])  # a truncated store-on-miss write

    report = run(settings, execute=True)

    assert copy_a.exists(), "a differing copy was deleted"
    assert not copy_b.exists()
    assert copy_c.exists()
    assert report.dedupe_removed_count == 1
    assert [d.outcome for d in report.declined] == [gc_execute.KEPT_DIFFERING]
    # A finding, not a failure: GC declined to reclaim, nothing went wrong.
    assert report.ok is True
    assert "declined=1" in report.log_text()


def test_dedupe_never_removes_the_last_copy(tmp_path: Path) -> None:
    """If the copy that would be KEPT is gone by execute time, its duplicates
    are left alone rather than leaving the depot with zero copies.

    Reported as a *decline*, not a failure: its realistic cause is a concurrent
    ``DELETE`` taking the depot away mid-run, and a racing deleter must not
    drag an otherwise-clean run to 'error' (the WP 1.6 rule).

    Mutation target: dropping the keeper verification kills this.
    """
    settings = build_world(tmp_path)
    copy_a, copy_b, copy_c = _three_copies(settings)

    depot_root = deletion.resolve_depot_root(settings.cache_root)
    conn = open_db(settings)
    try:
        plan = gc.plan_gc(
            440,
            gc.load_gc_inputs(conn, 440),
            depot_root=depot_root,
            archive_dir=settings.manifest_archive_dir,
        )
    finally:
        conn.close()
    # The plan was made while all three copies existed; codeC is the newest,
    # so it is the one marked "keep" — and it disappears before execution.
    assert plan.depots[0].dedupe[0].keep.path == str(copy_c)
    copy_c.unlink()

    result = gc_execute.execute_depot(plan.depots[0], depot_root=depot_root)

    assert copy_a.exists()
    assert copy_b.exists()
    assert result.dedupe_removed_count == 0
    assert [d.outcome for d in result.dedupe_declined] == [
        gc_execute.REFUSED_NO_KEEPER
    ]
    assert result.dedupe_problems == []
    assert result.ok is True


def test_a_single_stored_copy_is_never_a_dedupe_candidate(tmp_path: Path) -> None:
    settings = build_world(tmp_path)
    only = write_cache_manifest(
        cache_root(settings),
        depotid=441,
        manifestid=CURRENT_MANIFEST,
        chunks=KEEP_SIZES,
    )

    report = run(settings, execute=True)

    assert report.dedupe_removed_count == 0
    assert only.exists()


# ==========================================================================
# State bookkeeping (the needs_force / status / size-cache decision)
# ==========================================================================


def test_execute_sets_needs_force_for_every_app_mapped_to_a_touched_depot(
    tmp_path: Path,
) -> None:
    """The decision, positive half: a depot that actually lost chunks flags ALL
    of its co-owners, not just the requesting app — the bytes changed under
    every one of them (ADR-0003 addendum's reasoning)."""
    settings = build_world(tmp_path)
    conn = open_db(settings)
    try:
        seed_app(conn, 730)
        seed_mapping(conn, 441, 730)
        seed_recorded_manifest(conn, appid=730, depotid=441, manifestid=CURRENT_MANIFEST)
    finally:
        conn.close()

    report = run(settings, execute=True)

    assert report.removed_count == 2
    assert report.flagged_appids == [440, 730]
    conn = open_db(settings)
    try:
        assert needs_force_of(conn, 440) == 1
        assert needs_force_of(conn, 730) == 1
    finally:
        conn.close()


def test_an_execute_run_that_removed_nothing_sets_no_needs_force(
    tmp_path: Path,
) -> None:
    """The decision, minimal half: nothing was reclaimed, so nothing about the
    app's on-disk state became uncertain."""
    settings = build_world(tmp_path)
    # Remove the orphans first so the second run has nothing to do.
    run(settings, execute=True)
    conn = open_db(settings)
    try:
        seed_app(conn, 440, needs_force=0)
    finally:
        conn.close()

    report = run(settings, execute=True)

    assert report.removed_count == 0
    assert report.flagged_appids == []
    conn = open_db(settings)
    try:
        assert needs_force_of(conn, 440) == 0
    finally:
        conn.close()


def test_a_dedupe_only_run_sets_no_needs_force(tmp_path: Path) -> None:
    """A redundant manifest copy is not chunk content: removing it changes
    nothing about whether the depot is completely downloaded, so SteamPrefill's
    own bookkeeping stays honest and the next run may stay non-forced."""
    settings = build_world(tmp_path)
    run(settings, execute=True)  # clear the orphans
    _three_copies(settings)
    conn = open_db(settings)
    try:
        seed_app(conn, 440, needs_force=0)
    finally:
        conn.close()

    report = run(settings, execute=True)

    assert report.dedupe_removed_count == 2
    assert report.removed_count == 0
    assert report.flagged_appids == []
    conn = open_db(settings)
    try:
        assert needs_force_of(conn, 440) == 0
    finally:
        conn.close()


def test_gc_never_touches_apps_status_or_last_prefill_at(tmp_path: Path) -> None:
    """GC reclaims bytes that are by construction not part of any current
    manifest, so the app's prefill lifecycle is unchanged by it."""
    settings = build_world(tmp_path)
    conn = open_db(settings)
    try:
        conn.execute(
            "UPDATE apps SET status = 'done', last_prefill_at = ? WHERE appid = 440",
            ("2026-08-09T10:00:00Z",),
        )
        conn.commit()
    finally:
        conn.close()

    run(settings, execute=True)

    conn = open_db(settings)
    try:
        row = conn.execute(
            "SELECT status, last_prefill_at FROM apps WHERE appid = 440"
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "done"
    assert row["last_prefill_at"] == "2026-08-09T10:00:00Z"


def test_execute_invalidates_the_size_cache_but_a_dry_run_does_not(
    tmp_path: Path,
) -> None:
    settings = build_world(tmp_path)
    calls: list[int] = []

    class CountingCache(SizeCache):
        def invalidate(self) -> None:
            calls.append(1)
            super().invalidate()

    cache = CountingCache()
    conn = open_db(settings)
    try:
        jobs.enqueue_gc(conn, 440, execute=False)
        job = jobs.claim_next_job(conn)
        gc_execute.run_gc_job(conn, job, settings=settings, size_cache=cache)
        assert calls == [], "a dry run changed nothing, so nothing to invalidate"

        jobs.enqueue_gc(conn, 440, execute=True)
        job = jobs.claim_next_job(conn)
        gc_execute.run_gc_job(conn, job, settings=settings, size_cache=cache)
        assert calls == [1]
    finally:
        conn.close()


# ==========================================================================
# The job record and the worker
# ==========================================================================


def test_job_deletes_reads_a_missing_or_null_mode_as_dry_run() -> None:
    """Fail-closed: only an unambiguous GC-job-with-execute means "delete".

    Mutation target: making ``job_deletes`` default a NULL ``gc_execute`` to
    True kills this.
    """
    assert jobs.job_deletes({"type": "gc", "gc_execute": 1}) is True
    assert jobs.job_deletes({"type": "gc", "gc_execute": 0}) is False
    assert jobs.job_deletes({"type": "gc", "gc_execute": None}) is False
    assert jobs.job_deletes({"type": "gc"}) is False
    assert jobs.job_deletes({"type": "prefill", "gc_execute": 1}) is False


def test_the_job_row_records_which_mode_it_ran_in(tmp_path: Path) -> None:
    settings = build_world(tmp_path)
    client = TestClient(create_app(settings))

    dry = client.post("/v1/cache/440/gc", headers=AUTH).json()
    wet = client.post("/v1/cache/440/gc", json={"execute": True}, headers=AUTH).json()

    assert dry["mode"] == "dry-run" and dry["execute"] is False
    assert wet["mode"] == "execute" and wet["execute"] is True
    assert dry["job_id"] != wet["job_id"], (
        "a dry run and an execute run must never dedupe into each other"
    )

    listed = {job["id"]: job for job in client.get("/v1/jobs", headers=AUTH).json()}
    assert listed[dry["job_id"]]["gc_execute"] is False
    assert listed[wet["job_id"]]["gc_execute"] is True
    assert listed[dry["job_id"]]["type"] == "gc"


def test_a_prefill_job_reports_a_null_gc_mode(tmp_path: Path) -> None:
    settings = build_world(tmp_path)
    client = TestClient(create_app(settings))
    client.post("/v1/prefill", json={"appids": [440]}, headers=AUTH)

    (job,) = client.get("/v1/jobs", headers=AUTH).json()

    assert job["type"] == "prefill"
    assert job["gc_execute"] is None, "'not applicable' is not 'dry run'"


def test_the_worker_runs_a_queued_gc_job_end_to_end(tmp_path: Path) -> None:
    settings = build_world(tmp_path)
    client = TestClient(create_app(settings))
    queued = client.post("/v1/cache/440/gc", json={"execute": True}, headers=AUTH)
    job_id = queued.json()["job_id"]

    from vault_api.worker import PrefillWorker

    worker = PrefillWorker(settings)
    conn = open_db(settings)
    try:
        job = jobs.claim_next_job(conn)
        worker._execute(conn, job)
        finished = jobs.get_job(conn, job_id)
    finally:
        conn.close()

    assert finished["status"] == "done", finished["log_excerpt"]
    assert "GC totals (EXECUTED)" in finished["log_excerpt"]
    assert "chunks_removed=2" in finished["log_excerpt"]
    assert "bytes_freed=700" in finished["log_excerpt"]
    assert not chunk_path(settings, 441, ORPHAN_A).exists()


def test_the_worker_fails_an_unknown_job_type_rather_than_guessing(
    tmp_path: Path,
) -> None:
    settings = build_world(tmp_path)
    from vault_api.worker import PrefillWorker

    conn = open_db(settings)
    try:
        conn.execute(
            "INSERT INTO jobs (appid, type, status, created_at) "
            "VALUES (440, 'defrag', 'queued', '2026-08-09T00:00:00Z')"
        )
        conn.commit()
        job = jobs.claim_next_job(conn)
        PrefillWorker(settings)._execute(conn, job)
        finished = jobs.get_job(conn, int(job["id"]))
    finally:
        conn.close()

    assert finished["status"] == "error"
    assert "Unknown job type" in finished["log_excerpt"]
    # Nothing ran: the orphans are still there.
    assert chunk_path(settings, 441, ORPHAN_A).exists()


def test_a_crash_inside_the_run_is_recorded_as_error(tmp_path: Path) -> None:
    settings = build_world(tmp_path)
    conn = open_db(settings)
    try:
        jobs.enqueue_gc(conn, 440, execute=True)
        job = jobs.claim_next_job(conn)
        original = gc_execute.run_gc
        gc_execute.run_gc = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            gc_execute.run_gc_job(conn, job, settings=settings)
        finally:
            gc_execute.run_gc = original
        finished = jobs.get_job(conn, int(job["id"]))
    finally:
        conn.close()

    assert finished["status"] == "error"
    assert "Internal error" in finished["log_excerpt"]
    assert "boom" in finished["log_excerpt"]


# ==========================================================================
# The endpoint
# ==========================================================================


def test_gc_endpoint_defaults_to_dry_run(tmp_path: Path) -> None:
    """ADR-0007's core promise, pinned from the outside: a request with no
    body, an empty body, and an explicit false all queue a job that deletes
    nothing — and the files are still there afterwards.

    Mutation target: flipping ``GcRequest.execute``'s default to ``True`` (or
    the endpoint's ``if body is not None else False``) kills this test.
    """
    settings = build_world(tmp_path)
    client = TestClient(create_app(settings))
    before = snapshot(cache_root(settings))

    for payload in (None, {}, {"execute": False}):
        response = (
            client.post("/v1/cache/440/gc", headers=AUTH)
            if payload is None
            else client.post("/v1/cache/440/gc", json=payload, headers=AUTH)
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["execute"] is False
        assert body["mode"] == "dry-run"

        conn = open_db(settings)
        try:
            job = jobs.claim_next_job(conn)
            assert job["gc_execute"] == 0
            gc_execute.run_gc_job(conn, job, settings=settings)
        finally:
            conn.close()

    assert snapshot(cache_root(settings)) == before


@pytest.mark.parametrize("value", ["true", "yes", 1, "1", "on"])
def test_execute_must_be_a_literal_json_true(tmp_path: Path, value: object) -> None:
    """StrictBool: the one flag that turns a report into a deletion is not
    something a client gets to spell loosely (docs/LEARNINGS.md — pydantic's
    lax mode would accept every value in this list)."""
    settings = build_world(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post("/v1/cache/440/gc", json={"execute": value}, headers=AUTH)

    assert response.status_code == 422, response.text


def test_a_typo_in_the_body_is_422_not_a_silent_dry_run(tmp_path: Path) -> None:
    settings = build_world(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post("/v1/cache/440/gc", json={"exceute": True}, headers=AUTH)

    assert response.status_code == 422


def test_gc_endpoint_requires_the_api_key(tmp_path: Path) -> None:
    settings = build_world(tmp_path)
    client = TestClient(create_app(settings))

    assert client.post("/v1/cache/440/gc").status_code == 401
    assert client.post("/v1/cache/440/gc", json={"execute": True}).status_code == 401
    assert (
        client.post("/v1/cache/440/gc", headers={"X-Api-Key": "wrong"}).status_code
        == 401
    )
    # And nothing was queued by any of them.
    conn = open_db(settings)
    try:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        conn.close()


def test_gc_endpoint_404s_for_an_unknown_app_and_for_an_unmapped_one(
    tmp_path: Path,
) -> None:
    settings = build_world(tmp_path)
    client = TestClient(create_app(settings))

    assert client.post("/v1/cache/999999/gc", headers=AUTH).status_code == 404

    conn = open_db(settings)
    try:
        seed_app(conn, 730)
    finally:
        conn.close()
    unmapped = client.post("/v1/cache/730/gc", headers=AUTH)
    assert unmapped.status_code == 404
    assert "no depot mappings" in unmapped.json()["detail"]


def test_gc_endpoint_rejects_a_non_positive_appid(tmp_path: Path) -> None:
    settings = build_world(tmp_path)
    client = TestClient(create_app(settings))

    assert client.post("/v1/cache/0/gc", headers=AUTH).status_code == 422


def test_gc_requests_in_the_same_mode_dedupe(tmp_path: Path) -> None:
    settings = build_world(tmp_path)
    client = TestClient(create_app(settings))

    first = client.post("/v1/cache/440/gc", json={"execute": True}, headers=AUTH).json()
    second = client.post("/v1/cache/440/gc", json={"execute": True}, headers=AUTH).json()

    assert first["deduplicated"] is False
    assert second["deduplicated"] is True
    assert first["job_id"] == second["job_id"]


def test_a_queued_gc_job_makes_delete_answer_409_with_the_right_label(
    tmp_path: Path,
) -> None:
    settings = build_world(tmp_path)
    client = TestClient(create_app(settings))
    client.post("/v1/cache/440/gc", json={"execute": True}, headers=AUTH)

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 409
    assert response.json()["detail"].startswith("GC job")
    assert chunk_path(settings, 441, KEEP_A).exists()


# ==========================================================================
# The exclusion hook (the pending beta-chunk decision plugs in here)
# ==========================================================================


def test_an_exclusion_predicate_can_only_shrink_what_gets_deleted(
    tmp_path: Path,
) -> None:
    """Not a feature of this work package — a structural check that the pending
    "protect recently-stored chunks" rule needs a predicate, not surgery."""
    settings = build_world(tmp_path)
    depot_root = deletion.resolve_depot_root(settings.cache_root)
    conn = open_db(settings)
    try:
        plan = gc.plan_gc(
            440,
            gc.load_gc_inputs(conn, 440),
            depot_root=depot_root,
            archive_dir=settings.manifest_archive_dir,
        )
    finally:
        conn.close()

    def protect_orphan_a(chunk_id: str, _depot: gc.DepotGcPlan) -> str | None:
        return "pinned by a test predicate" if chunk_id == ORPHAN_A else None

    result = gc_execute.execute_depot(
        plan.depots[0], depot_root=depot_root, exclusions=[protect_orphan_a]
    )

    assert result.removed_count == 1
    assert list(result.held_back) == [ORPHAN_A]
    assert chunk_path(settings, 441, ORPHAN_A).exists()
    assert not chunk_path(settings, 441, ORPHAN_B).exists()
    assert result.ok is True


# ==========================================================================
# Guards borrowed from WP 1.6, exercised for files
# ==========================================================================


@pytest.mark.parametrize(
    "name",
    ["..", ".", "", "a/b", "a\\b", os.path.join("sub", "file"), "/etc/passwd"],
)
def test_safe_child_path_refuses_anything_that_is_not_a_direct_child(
    tmp_path: Path, name: str
) -> None:
    with pytest.raises(deletion.UnsafeDepotTargetError):
        deletion.safe_child_path(str(tmp_path), name)


def test_safe_child_path_builds_a_direct_child(tmp_path: Path) -> None:
    assert deletion.safe_child_path(str(tmp_path), "441") == os.path.join(
        str(tmp_path), "441"
    )


def test_remove_file_settling_reports_an_already_absent_file_as_not_ours(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "gone.bin"

    removed, error = deletion.remove_file_settling(str(missing))

    assert removed is False
    assert error is None


def test_remove_file_settling_removes_a_real_file(tmp_path: Path) -> None:
    target = tmp_path / "chunk.bin"
    target.write_bytes(b"x" * 10)

    removed, error = deletion.remove_file_settling(str(target))

    assert removed is True and error is None
    assert not target.exists()


def test_remove_file_settling_never_follows_a_link(tmp_path: Path) -> None:
    """``os.unlink`` on a link removes the link, not its target — proven here
    rather than assumed, because the caller's refusal to unlink links at all
    depends on knowing what the syscall would have done."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.bin").write_bytes(b"z" * 32)
    link = tmp_path / "link"
    make_dir_link(link, outside)

    deletion.remove_file_settling(str(link))

    assert (outside / "precious.bin").read_bytes() == b"z" * 32


# ==========================================================================
# WP 3.7 review carry-over: parse_bin_payload's cross-check is not optional
# ==========================================================================


def test_parse_bin_payload_requires_both_expected_ids() -> None:
    """The reviewer nitpick, pinned: no caller can skip the corruption
    cross-check by simply not typing the arguments."""
    from vault_api.manifests import parse_bin_payload

    with pytest.raises(TypeError):
        parse_bin_payload("irrelevant.bin")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        parse_bin_payload("irrelevant.bin", filename_depot_id=441)  # type: ignore[call-arg]


def test_parse_bin_payload_cross_check_fires_on_a_mismatch(tmp_path: Path) -> None:
    from vault_api.manifests import ManifestParseError, parse_bin_payload

    path = write_archive_bin(
        tmp_path, depotid=441, manifestid="900", chunks={KEEP_A: 100}
    )

    with pytest.raises(ManifestParseError, match="corruption signal"):
        parse_bin_payload(
            str(path), filename_depot_id=442, filename_manifest_id=900
        )
