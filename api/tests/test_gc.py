"""Garbage-collection core (``vault_api/gc.py``, WP 3.7) — planning only.

Every fixture is a **synthetic** cache tree built in this file: real depot
directories, real chunk files with real byte sizes, real ZIP/protobuf
manifests. The protobuf/ZIP encoders are imported from ``test_manifests`` (WP
3.1) rather than re-written, so there is exactly one encoder in the suite and
it stays deliberately independent of ``vault_api/manifests.py``'s own reader —
a bug in the reader cannot hide behind a matching bug in the writer.

Two things these tests are especially careful about, because WP 3.8 will
delete exactly what this module plans:

- **byte-exactness** — orphan sizes are asserted against the real file sizes
  written to disk, not against the manifest's declared sizes;
- **the fail-closed directions** — every "unknown ⇒ do not GC" branch has a
  test that dies if the branch is flipped (see the mutation list in the work
  package report).
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import pytest

from tests.test_manifests import (
    _bin_manifest_bytes,
    _sectioned_stream,
    _write_cache_manifest_zip,
)
from vault_api import gc


# --------------------------------------------------------------------------
# Fixture builders
# --------------------------------------------------------------------------


def _cid(index: int) -> str:
    """A distinct, syntactically valid 40-hex lowercase chunk id."""
    return f"{index:040x}"


def _sha20(chunk_id: str) -> bytes:
    """The same chunk id as the 20 raw bytes a cache-stored manifest carries,
    so both manifest formats can express the identical chunk set."""
    return bytes.fromhex(chunk_id)


def depot_dir(root: Path, depotid: int) -> Path:
    return root / "depot" / str(depotid)


def write_chunks(root: Path, depotid: int, chunks: dict[str, int]) -> None:
    """Write real chunk files of the given byte sizes into ``depot/<id>/chunk/``."""
    chunk_dir = depot_dir(root, depotid) / "chunk"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    for chunk_id, size in chunks.items():
        (chunk_dir / chunk_id).write_bytes(b"\xab" * size)


def write_archive_bin(
    archive_dir: Path, *, depotid: int, manifestid: str, chunks: dict[str, int]
) -> Path:
    """A manifest archive entry: ``{depotid}_{manifestid}.bin`` in SteamPrefill's
    payload format but the archive's own two-segment filename."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    data = _bin_manifest_bytes(
        depot_id=depotid,
        manifest_id=int(manifestid),
        files=[[(cid, size) for cid, size in chunks.items()]],
    )
    path = archive_dir / f"{depotid}_{manifestid}.bin"
    path.write_bytes(data)
    return path


def write_cache_manifest(
    root: Path,
    *,
    depotid: int,
    manifestid: str,
    chunks: dict[str, int],
    request_code: str = "requestcode0001",
    payload_depot_id: int | None = None,
    payload_manifest_id: int | None = None,
) -> Path:
    """A cache-stored client manifest at
    ``depot/<id>/manifest/<manifestid>/5/<requestcode>``.

    ``payload_*`` override what the protobuf claims (for the corruption tests)
    without moving where the file is stored.
    """
    target = depot_dir(root, depotid) / "manifest" / manifestid / "5"
    target.mkdir(parents=True, exist_ok=True)
    stream = _sectioned_stream(
        depot_id=depotid if payload_depot_id is None else payload_depot_id,
        manifest_id=int(manifestid) if payload_manifest_id is None else payload_manifest_id,
        file_mappings=[[(_sha20(cid), size) for cid, size in chunks.items()]],
    )
    return _write_cache_manifest_zip(target, request_code, stream)


def write_garbage_manifest(
    root: Path, *, depotid: int, manifestid: str, request_code: str = "requestcode0001"
) -> Path:
    """A stored manifest that is not a valid ZIP at all."""
    target = depot_dir(root, depotid) / "manifest" / manifestid / "5"
    target.mkdir(parents=True, exist_ok=True)
    path = target / request_code
    path.write_bytes(b"not a zip file at all")
    return path


def touch_newer(path: Path, *, seconds: int) -> None:
    """Move a file's mtime forward deterministically (no sleeping)."""
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + seconds * 1_000_000_000))


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    """``<cache_root>`` with an existing (empty) ``depot/``."""
    (tmp_path / "cache" / "depot").mkdir(parents=True)
    return tmp_path / "cache"


@pytest.fixture
def depot_root(cache: Path) -> str:
    return str((cache / "depot").resolve())


@pytest.fixture
def archive_dir(tmp_path: Path) -> Path:
    path = tmp_path / "manifests"
    path.mkdir()
    return path


def inputs(
    *,
    mapping_rows: list[tuple[object, object]],
    content_states: dict[int, bool] | None = None,
    recorded: dict[tuple[int, int], object] | None = None,
) -> gc.GcInputs:
    return gc.GcInputs(
        mapping_rows=mapping_rows,
        content_states=content_states or {},
        recorded_manifests=recorded or {},
    )


def only_depot(plan: gc.GcPlan) -> gc.DepotGcPlan:
    assert len(plan.depots) == 1, plan.depots
    return plan.depots[0]


# ==========================================================================
# Validators
# ==========================================================================


@pytest.mark.parametrize(
    "name",
    [
        _cid(1),
        "a" * 40,
        "0123456789abcdef0123456789abcdef01234567",
    ],
)
def test_is_chunk_filename_accepts_40_hex_lowercase(name: str) -> None:
    assert gc.is_chunk_filename(name)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "a" * 39,
        "a" * 41,
        ("a" * 39) + "A",  # uppercase hex: never matched, never deleted
        ("a" * 39) + "g",  # not hex
        ("a" * 39) + ".",
        "chunk.tmp",
        "..",
    ],
)
def test_is_chunk_filename_rejects_everything_else(name: str) -> None:
    assert not gc.is_chunk_filename(name)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("123456789", "123456789"),
        (123456789, "123456789"),
        ("18446744073709551615", "18446744073709551615"),  # u64 max, 20 digits
    ],
)
def test_valid_manifest_id_accepts_plain_decimals(raw: object, expected: str) -> None:
    assert gc.valid_manifest_id(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        True,  # bool is not an id even though True == 1
        False,
        0,
        -5,
        "",
        "0",
        "0123",  # leading zero would not match what was archived
        " 123 ",
        "+123",
        "1_0",
        "٤٤١",  # Arabic-Indic digits: isdigit() but not ASCII
        "../../etc/passwd",
        "1" * 21,  # longer than any u64
        b"123",
    ],
)
def test_valid_manifest_id_rejects_anything_that_could_become_a_bad_path(
    raw: object,
) -> None:
    assert gc.valid_manifest_id(raw) is None


# ==========================================================================
# scan_depot_chunks — only chunk/ is ever a deletion candidate
# ==========================================================================


def test_scan_depot_chunks_reports_exact_on_disk_sizes(cache: Path) -> None:
    write_chunks(cache, 441, {_cid(1): 10, _cid(2): 2048})

    scan = gc.scan_depot_chunks(str(depot_dir(cache, 441)))

    assert scan.chunk_dir_exists
    assert scan.chunks == {_cid(1): 10, _cid(2): 2048}
    assert scan.unrecognized == []


def test_scan_depot_chunks_never_looks_at_the_manifest_subtree(cache: Path) -> None:
    """The single most dangerous possible bug: a walk of the whole depot
    directory would classify every stored manifest as an orphan chunk."""
    write_chunks(cache, 441, {_cid(1): 10})
    write_cache_manifest(cache, depotid=441, manifestid="900", chunks={_cid(1): 10})

    scan = gc.scan_depot_chunks(str(depot_dir(cache, 441)))

    assert scan.chunks == {_cid(1): 10}
    assert scan.unrecognized == []


def test_scan_depot_chunks_flags_non_chunk_names_as_unrecognized(cache: Path) -> None:
    write_chunks(cache, 441, {_cid(1): 10})
    chunk_dir = depot_dir(cache, 441) / "chunk"
    (chunk_dir / "README.txt").write_bytes(b"x")
    (chunk_dir / (("a" * 39) + "A")).write_bytes(b"x" * 5)  # uppercase hex
    (chunk_dir / "subdir").mkdir()

    scan = gc.scan_depot_chunks(str(depot_dir(cache, 441)))

    assert scan.chunks == {_cid(1): 10}
    assert sorted(scan.unrecognized) == sorted(
        ["README.txt", ("a" * 39) + "A", "subdir"]
    )


def test_scan_depot_chunks_missing_directory_is_empty_not_an_error(cache: Path) -> None:
    depot_dir(cache, 441).mkdir(parents=True)

    scan = gc.scan_depot_chunks(str(depot_dir(cache, 441)))

    assert scan.chunks == {}
    assert scan.chunk_dir_exists is False


def test_scan_depot_chunks_skips_link_like_entries(cache: Path, tmp_path: Path) -> None:
    from tests.conftest import make_dir_link

    write_chunks(cache, 441, {_cid(1): 10})
    outside = tmp_path / "outside"
    outside.mkdir()
    make_dir_link(depot_dir(cache, 441) / "chunk" / _cid(2), outside)

    scan = gc.scan_depot_chunks(str(depot_dir(cache, 441)))

    assert scan.chunks == {_cid(1): 10}
    assert scan.unrecognized == [_cid(2)]


# ==========================================================================
# scan_stored_manifests / dedupe candidates
# ==========================================================================


def test_scan_stored_manifests_groups_copies_newest_first(cache: Path) -> None:
    write_cache_manifest(
        cache, depotid=441, manifestid="900", chunks={_cid(1): 10}, request_code="aaa"
    )
    newer = write_cache_manifest(
        cache, depotid=441, manifestid="900", chunks={_cid(1): 10}, request_code="bbb"
    )
    touch_newer(newer, seconds=60)

    stored = gc.scan_stored_manifests(str(depot_dir(cache, 441)))

    assert list(stored) == ["900"]
    assert [os.path.basename(c.path) for c in stored["900"]] == ["bbb", "aaa"]


def test_scan_stored_manifests_ignores_non_numeric_directory_names(cache: Path) -> None:
    write_cache_manifest(cache, depotid=441, manifestid="900", chunks={_cid(1): 10})
    bogus = depot_dir(cache, 441) / "manifest" / "not-a-manifest-id" / "5"
    bogus.mkdir(parents=True)
    (bogus / "x").write_bytes(b"x")

    assert list(gc.scan_stored_manifests(str(depot_dir(cache, 441)))) == ["900"]


def test_dedupe_candidates_keep_newest_and_report_the_rest(cache: Path) -> None:
    write_cache_manifest(
        cache, depotid=441, manifestid="900", chunks={_cid(1): 10}, request_code="aaa"
    )
    write_cache_manifest(
        cache, depotid=441, manifestid="900", chunks={_cid(1): 10}, request_code="bbb"
    )
    newest = write_cache_manifest(
        cache, depotid=441, manifestid="900", chunks={_cid(1): 10}, request_code="ccc"
    )
    touch_newer(newest, seconds=120)

    stored = gc.scan_stored_manifests(str(depot_dir(cache, 441)))
    candidates = gc.dedupe_candidates(stored)

    assert len(candidates) == 1
    assert os.path.basename(candidates[0].keep.path) == "ccc"
    assert sorted(os.path.basename(d.path) for d in candidates[0].duplicates) == [
        "aaa",
        "bbb",
    ]
    assert candidates[0].reclaimable_bytes == sum(
        d.size_bytes for d in candidates[0].duplicates
    )


def test_dedupe_candidates_ignores_single_copies(cache: Path) -> None:
    write_cache_manifest(cache, depotid=441, manifestid="900", chunks={_cid(1): 10})

    stored = gc.scan_stored_manifests(str(depot_dir(cache, 441)))

    assert gc.dedupe_candidates(stored) == []


# ==========================================================================
# resolve_depot_chunkset — the source chain
# ==========================================================================


def test_resolve_prefers_the_archived_bin_for_the_recorded_manifest(
    cache: Path, archive_dir: Path
) -> None:
    write_archive_bin(
        archive_dir, depotid=441, manifestid="900", chunks={_cid(1): 10, _cid(2): 20}
    )
    # A DIFFERENT chunk set stored in the cache manifest for the same id: if
    # the archive is really preferred, these chunks must not appear.
    write_cache_manifest(cache, depotid=441, manifestid="900", chunks={_cid(9): 99})

    resolution = gc.resolve_depot_chunkset(
        441,
        [gc.MappedApp(appid=440, has_content=True, recorded_manifestid="900")],
        archive_dir=str(archive_dir),
        stored_manifests=gc.scan_stored_manifests(str(depot_dir(cache, 441))),
    )

    assert resolution.status == gc.STATUS_PLANNED
    assert set(resolution.keep) == {_cid(1), _cid(2)}
    assert resolution.apps[0].source == gc.SOURCE_ARCHIVE


def test_resolve_falls_back_to_the_cache_stored_copy_of_the_same_manifest(
    cache: Path, archive_dir: Path
) -> None:
    write_cache_manifest(
        cache, depotid=441, manifestid="900", chunks={_cid(1): 10, _cid(2): 20}
    )

    resolution = gc.resolve_depot_chunkset(
        441,
        [gc.MappedApp(appid=440, has_content=True, recorded_manifestid="900")],
        archive_dir=str(archive_dir),  # empty: no archived copy exists
        stored_manifests=gc.scan_stored_manifests(str(depot_dir(cache, 441))),
    )

    assert resolution.status == gc.STATUS_PLANNED
    assert set(resolution.keep) == {_cid(1), _cid(2)}
    assert resolution.apps[0].source == "cache_manifest"
    # The archive miss is still reported, even though resolution succeeded.
    assert any("archive" in err for err in resolution.apps[0].errors)


def test_resolve_uses_the_newest_stored_manifest_when_nothing_was_recorded(
    cache: Path, archive_dir: Path
) -> None:
    write_cache_manifest(cache, depotid=441, manifestid="800", chunks={_cid(1): 10})
    newer = write_cache_manifest(
        cache, depotid=441, manifestid="900", chunks={_cid(2): 20}
    )
    touch_newer(newer, seconds=600)

    resolution = gc.resolve_depot_chunkset(
        441,
        [gc.MappedApp(appid=440, has_content=True, recorded_manifestid=None)],
        archive_dir=str(archive_dir),
        stored_manifests=gc.scan_stored_manifests(str(depot_dir(cache, 441))),
    )

    assert resolution.status == gc.STATUS_PLANNED
    assert set(resolution.keep) == {_cid(2)}
    assert resolution.apps[0].manifestid == "900"


def test_resolve_never_falls_back_from_an_unreadable_newer_manifest_to_an_older_one(
    cache: Path, archive_dir: Path
) -> None:
    """The defining GC failure mode: keeping an OLD manifest's chunk set would
    plan the deletion of exactly the chunks the current version needs."""
    write_cache_manifest(cache, depotid=441, manifestid="800", chunks={_cid(1): 10})
    newer = write_garbage_manifest(cache, depotid=441, manifestid="900")
    touch_newer(newer, seconds=600)

    resolution = gc.resolve_depot_chunkset(
        441,
        [gc.MappedApp(appid=440, has_content=True, recorded_manifestid=None)],
        archive_dir=str(archive_dir),
        stored_manifests=gc.scan_stored_manifests(str(depot_dir(cache, 441))),
    )

    assert resolution.status == gc.STATUS_NO_MANIFEST
    assert resolution.keep == {}


def test_resolve_tries_the_next_copy_of_the_same_manifest_id(
    cache: Path, archive_dir: Path
) -> None:
    write_cache_manifest(
        cache, depotid=441, manifestid="900", chunks={_cid(1): 10}, request_code="good"
    )
    broken = write_garbage_manifest(
        cache, depotid=441, manifestid="900", request_code="broken"
    )
    touch_newer(broken, seconds=60)

    resolution = gc.resolve_depot_chunkset(
        441,
        [gc.MappedApp(appid=440, has_content=True, recorded_manifestid="900")],
        archive_dir=str(archive_dir),
        stored_manifests=gc.scan_stored_manifests(str(depot_dir(cache, 441))),
    )

    assert resolution.status == gc.STATUS_PLANNED
    assert set(resolution.keep) == {_cid(1)}


def test_resolve_rejects_a_stored_manifest_whose_payload_ids_disagree_with_its_path(
    cache: Path, archive_dir: Path
) -> None:
    write_cache_manifest(
        cache,
        depotid=441,
        manifestid="900",
        chunks={_cid(1): 10},
        payload_depot_id=999,  # stored under depot 441, claims depot 999
    )

    resolution = gc.resolve_depot_chunkset(
        441,
        [gc.MappedApp(appid=440, has_content=True, recorded_manifestid="900")],
        archive_dir=str(archive_dir),
        stored_manifests=gc.scan_stored_manifests(str(depot_dir(cache, 441))),
    )

    assert resolution.status == gc.STATUS_NO_MANIFEST
    assert any("corruption signal" in err for err in resolution.apps[0].errors)


def test_resolve_rejects_an_archived_bin_whose_payload_ids_disagree(
    cache: Path, archive_dir: Path
) -> None:
    data = _bin_manifest_bytes(
        depot_id=999, manifest_id=900, files=[[(_cid(1), 10)]]
    )
    (archive_dir / "441_900.bin").write_bytes(data)

    resolution = gc.resolve_depot_chunkset(
        441,
        [gc.MappedApp(appid=440, has_content=True, recorded_manifestid="900")],
        archive_dir=str(archive_dir),
        stored_manifests={},
    )

    assert resolution.status == gc.STATUS_NO_MANIFEST
    assert any("depot id" in err for err in resolution.apps[0].errors)


def test_resolve_with_a_poisoned_recorded_manifest_id_never_guesses_another(
    cache: Path, archive_dir: Path
) -> None:
    """A row exists but is unreadable ⇒ vault-api HAS a claim it cannot read.
    Falling through to "newest stored manifest" would be a guess about the
    wrong manifest, so the gate fires instead."""
    write_cache_manifest(cache, depotid=441, manifestid="900", chunks={_cid(1): 10})

    resolution = gc.resolve_depot_chunkset(
        441,
        [gc.MappedApp(appid=440, has_content=True, recorded_manifestid="../../evil")],
        archive_dir=str(archive_dir),
        stored_manifests=gc.scan_stored_manifests(str(depot_dir(cache, 441))),
    )

    assert resolution.status == gc.STATUS_NO_MANIFEST
    assert resolution.keep == {}
    assert any("unusable manifest id" in err for err in resolution.apps[0].errors)


# ==========================================================================
# resolve_depot_chunkset — the gate and the uncached-app decision
# ==========================================================================


def test_shared_depot_keep_set_is_the_union_of_both_apps_manifests(
    cache: Path, archive_dir: Path
) -> None:
    write_archive_bin(archive_dir, depotid=441, manifestid="900", chunks={_cid(1): 10})
    write_archive_bin(archive_dir, depotid=441, manifestid="901", chunks={_cid(2): 20})

    resolution = gc.resolve_depot_chunkset(
        441,
        [
            gc.MappedApp(appid=440, has_content=True, recorded_manifestid="900"),
            gc.MappedApp(appid=730, has_content=True, recorded_manifestid="901"),
        ],
        archive_dir=str(archive_dir),
        stored_manifests={},
    )

    assert resolution.status == gc.STATUS_PLANNED
    assert set(resolution.keep) == {_cid(1), _cid(2)}


def test_cached_app_with_no_resolvable_manifest_skips_the_whole_depot(
    cache: Path, archive_dir: Path
) -> None:
    """ADR-0007's readiness gate: one resolvable app is not enough."""
    write_archive_bin(archive_dir, depotid=441, manifestid="900", chunks={_cid(1): 10})

    resolution = gc.resolve_depot_chunkset(
        441,
        [
            gc.MappedApp(appid=440, has_content=True, recorded_manifestid="900"),
            # cached, but nothing recorded and nothing stored to fall back to
            gc.MappedApp(appid=730, has_content=True, recorded_manifestid=None),
        ],
        archive_dir=str(archive_dir),
        stored_manifests={},
    )

    assert resolution.status == gc.STATUS_NO_MANIFEST
    assert resolution.keep == {}
    assert "730" in resolution.note


def test_uncached_mapped_app_is_excluded_and_does_not_block_the_gate(
    cache: Path, archive_dir: Path
) -> None:
    """The ADR-0003-addendum-consistent rule, pinned.

    App 730 is verifiably idle/never-prefilled/job-free and has no
    ``depot_manifests`` row. It must neither contribute chunks nor stop app
    440's depot from being planned.
    """
    write_archive_bin(archive_dir, depotid=441, manifestid="900", chunks={_cid(1): 10})

    resolution = gc.resolve_depot_chunkset(
        441,
        [
            gc.MappedApp(appid=440, has_content=True, recorded_manifestid="900"),
            gc.MappedApp(appid=730, has_content=False, recorded_manifestid=None),
        ],
        archive_dir=str(archive_dir),
        stored_manifests={},
    )

    assert resolution.status == gc.STATUS_PLANNED
    assert set(resolution.keep) == {_cid(1)}
    excluded = [app for app in resolution.apps if not app.counting]
    assert [app.appid for app in excluded] == [730]
    assert "ADR-0003" in (excluded[0].excluded_reason or "")


def test_uncached_app_WITH_a_recorded_manifest_still_counts(
    cache: Path, archive_dir: Path
) -> None:
    """The other half of the decision: a recorded manifest is a claim
    vault-api itself wrote down, so an unreadable one fires the gate even
    though the app has no cache content."""
    write_archive_bin(archive_dir, depotid=441, manifestid="900", chunks={_cid(1): 10})

    resolution = gc.resolve_depot_chunkset(
        441,
        [
            gc.MappedApp(appid=440, has_content=True, recorded_manifestid="900"),
            gc.MappedApp(appid=730, has_content=False, recorded_manifestid="777"),
        ],
        archive_dir=str(archive_dir),
        stored_manifests={},
    )

    assert resolution.status == gc.STATUS_NO_MANIFEST
    assert resolution.keep == {}


def test_all_apps_uncached_is_skipped_not_an_empty_keep_set(
    cache: Path, archive_dir: Path
) -> None:
    """The sub-decision: an empty union must never mean "delete everything"."""
    resolution = gc.resolve_depot_chunkset(
        441,
        [
            gc.MappedApp(appid=440, has_content=False, recorded_manifestid=None),
            gc.MappedApp(appid=730, has_content=False, recorded_manifestid=None),
        ],
        archive_dir=str(archive_dir),
        stored_manifests={},
    )

    assert resolution.status == gc.STATUS_NO_COUNTING_APPS
    assert resolution.keep == {}


def test_unreadable_mapping_row_protects_the_depot_unconditionally(
    archive_dir: Path,
) -> None:
    resolution = gc.resolve_depot_chunkset(
        441,
        [
            gc.MappedApp(appid=440, has_content=True, recorded_manifestid="900"),
            gc.MappedApp(appid=0, has_content=True, recorded_manifestid=None),
        ],
        archive_dir=str(archive_dir),
        stored_manifests={},
    )

    assert resolution.status == gc.STATUS_UNREADABLE_OWNER
    assert resolution.keep == {}


def test_no_mapping_rows_at_all_is_unmapped(archive_dir: Path) -> None:
    resolution = gc.resolve_depot_chunkset(
        441, [], archive_dir=str(archive_dir), stored_manifests={}
    )

    assert resolution.status == gc.STATUS_UNMAPPED
    assert resolution.keep == {}


# ==========================================================================
# plan_gc — orphan identification, byte-exact
# ==========================================================================


def test_plan_gc_identifies_orphans_byte_exactly(
    cache: Path, depot_root: str, archive_dir: Path
) -> None:
    # Current manifest keeps chunks 1 and 2; chunks 3 and 4 are last version's.
    write_archive_bin(
        archive_dir, depotid=441, manifestid="900", chunks={_cid(1): 100, _cid(2): 200}
    )
    write_chunks(
        cache, 441, {_cid(1): 100, _cid(2): 200, _cid(3): 333, _cid(4): 4444}
    )

    plan = gc.plan_gc(
        440,
        inputs(
            mapping_rows=[(441, 440)],
            content_states={440: True},
            recorded={(440, 441): "900"},
        ),
        depot_root=depot_root,
        archive_dir=str(archive_dir),
    )

    depot = only_depot(plan)
    assert depot.status == gc.STATUS_PLANNED
    assert depot.orphan_chunks == {_cid(3): 333, _cid(4): 4444}
    assert depot.orphan_count == 2
    assert depot.orphan_bytes == 333 + 4444
    assert depot.kept_count == 2
    assert depot.kept_bytes == 300
    assert depot.keep_set_count == 2
    assert depot.size_mismatch_count == 0
    assert plan.orphan_bytes == 333 + 4444
    assert plan.planned_depots == [441]


def test_plan_gc_orphan_bytes_come_from_disk_not_from_the_manifest(
    cache: Path, depot_root: str, archive_dir: Path
) -> None:
    """The manifest's declared size is irrelevant for an orphan — it is not in
    the manifest at all. And for a KEPT chunk, a declared/actual mismatch is
    reported without changing the plan."""
    write_archive_bin(archive_dir, depotid=441, manifestid="900", chunks={_cid(1): 100})
    write_chunks(cache, 441, {_cid(1): 7, _cid(2): 11})

    depot = only_depot(
        gc.plan_gc(
            440,
            inputs(
                mapping_rows=[(441, 440)],
                content_states={440: True},
                recorded={(440, 441): "900"},
            ),
            depot_root=depot_root,
            archive_dir=str(archive_dir),
        )
    )

    assert depot.orphan_chunks == {_cid(2): 11}
    assert depot.kept_bytes == 7  # on-disk, not the manifest's 100
    assert depot.size_mismatch_count == 1


def test_plan_gc_shared_depot_keeps_a_chunk_another_app_still_needs(
    cache: Path, depot_root: str, archive_dir: Path
) -> None:
    """Chunk 2 is NOT in app 440's manifest but IS in co-owner 730's, so it
    must survive — the union, not the requesting app's manifest alone."""
    write_archive_bin(archive_dir, depotid=441, manifestid="900", chunks={_cid(1): 10})
    write_archive_bin(archive_dir, depotid=441, manifestid="901", chunks={_cid(2): 20})
    write_chunks(cache, 441, {_cid(1): 10, _cid(2): 20, _cid(3): 30})

    depot = only_depot(
        gc.plan_gc(
            440,
            inputs(
                mapping_rows=[(441, 440), (441, 730)],
                content_states={440: True, 730: True},
                recorded={(440, 441): "900", (730, 441): "901"},
            ),
            depot_root=depot_root,
            archive_dir=str(archive_dir),
        )
    )

    assert depot.status == gc.STATUS_PLANNED
    assert depot.orphan_chunks == {_cid(3): 30}


def test_plan_gc_uncached_co_owner_does_not_block_and_does_not_pin(
    cache: Path, depot_root: str, archive_dir: Path
) -> None:
    """End-to-end version of the uncached-app decision, through plan_gc."""
    write_archive_bin(archive_dir, depotid=441, manifestid="900", chunks={_cid(1): 10})
    write_chunks(cache, 441, {_cid(1): 10, _cid(2): 20})

    depot = only_depot(
        gc.plan_gc(
            440,
            inputs(
                mapping_rows=[(441, 440), (441, 730)],
                content_states={440: True, 730: False},
                recorded={(440, 441): "900"},
            ),
            depot_root=depot_root,
            archive_dir=str(archive_dir),
        )
    )

    assert depot.status == gc.STATUS_PLANNED
    assert depot.orphan_chunks == {_cid(2): 20}


def test_plan_gc_co_owner_with_no_apps_row_defaults_to_protected(
    cache: Path, depot_root: str, archive_dir: Path
) -> None:
    """A co-owner absent from ``content_states`` (no ``apps`` row at all) must
    default to has_content=True ⇒ counting ⇒ the gate fires."""
    write_archive_bin(archive_dir, depotid=441, manifestid="900", chunks={_cid(1): 10})
    write_chunks(cache, 441, {_cid(1): 10, _cid(2): 20})

    depot = only_depot(
        gc.plan_gc(
            440,
            inputs(
                mapping_rows=[(441, 440), (441, 730)],
                content_states={440: True},  # 730 deliberately missing
                recorded={(440, 441): "900"},
            ),
            depot_root=depot_root,
            archive_dir=str(archive_dir),
        )
    )

    assert depot.status == gc.STATUS_NO_MANIFEST
    assert depot.orphan_chunks == {}


def test_plan_gc_skips_a_depot_with_a_poisoned_co_owner_row(
    cache: Path, depot_root: str, archive_dir: Path
) -> None:
    write_archive_bin(archive_dir, depotid=441, manifestid="900", chunks={_cid(1): 10})
    write_chunks(cache, 441, {_cid(1): 10, _cid(2): 20})

    depot = only_depot(
        gc.plan_gc(
            440,
            inputs(
                mapping_rows=[(441, 440), (441, "not-an-appid")],
                content_states={440: True},
                recorded={(440, 441): "900"},
            ),
            depot_root=depot_root,
            archive_dir=str(archive_dir),
        )
    )

    assert depot.status == gc.STATUS_UNREADABLE_OWNER
    assert depot.orphan_chunks == {}


@pytest.mark.parametrize("raw_depotid", ["../../evil", " 441 ", "1_0", None, True, -1])
def test_plan_gc_reports_a_poisoned_depot_id_and_touches_nothing(
    depot_root: str, archive_dir: Path, raw_depotid: object
) -> None:
    plan = gc.plan_gc(
        440,
        inputs(mapping_rows=[(raw_depotid, 440)], content_states={440: True}),
        depot_root=depot_root,
        archive_dir=str(archive_dir),
    )

    depot = only_depot(plan)
    assert depot.status == gc.STATUS_UNUSABLE_DEPOTID
    assert depot.depotid == 0
    assert depot.orphan_chunks == {}
    assert repr(raw_depotid) in depot.note


def test_plan_gc_missing_depot_directory(depot_root: str, archive_dir: Path) -> None:
    depot = only_depot(
        gc.plan_gc(
            440,
            inputs(
                mapping_rows=[(441, 440)],
                content_states={440: True},
                recorded={(440, 441): "900"},
            ),
            depot_root=depot_root,
            archive_dir=str(archive_dir),
        )
    )

    assert depot.status == gc.STATUS_MISSING_DIR
    assert depot.depot_dir_exists is False
    assert depot.orphan_chunks == {}


def test_plan_gc_empty_depot_directory_plans_zero_orphans(
    cache: Path, depot_root: str, archive_dir: Path
) -> None:
    """Distinct from the missing-directory case: the depot exists, so the plan
    is a real (empty) one rather than a skip."""
    depot_dir(cache, 441).mkdir(parents=True)
    write_archive_bin(archive_dir, depotid=441, manifestid="900", chunks={_cid(1): 10})

    depot = only_depot(
        gc.plan_gc(
            440,
            inputs(
                mapping_rows=[(441, 440)],
                content_states={440: True},
                recorded={(440, 441): "900"},
            ),
            depot_root=depot_root,
            archive_dir=str(archive_dir),
        )
    )

    assert depot.status == gc.STATUS_PLANNED
    assert depot.depot_dir_exists is True
    assert depot.chunk_dir_exists is False
    assert depot.orphan_chunks == {}
    assert depot.keep_set_count == 1
    assert depot.kept_count == 0


def test_plan_gc_never_plans_a_manifest_file_as_an_orphan(
    cache: Path, depot_root: str, archive_dir: Path
) -> None:
    write_archive_bin(archive_dir, depotid=441, manifestid="900", chunks={_cid(1): 10})
    write_chunks(cache, 441, {_cid(1): 10})
    manifest_path = write_cache_manifest(
        cache, depotid=441, manifestid="900", chunks={_cid(1): 10}
    )

    depot = only_depot(
        gc.plan_gc(
            440,
            inputs(
                mapping_rows=[(441, 440)],
                content_states={440: True},
                recorded={(440, 441): "900"},
            ),
            depot_root=depot_root,
            archive_dir=str(archive_dir),
        )
    )

    assert depot.orphan_chunks == {}
    assert manifest_path.exists()


def test_plan_gc_reports_dedupe_candidates_even_for_a_skipped_depot(
    cache: Path, depot_root: str, archive_dir: Path
) -> None:
    write_chunks(cache, 441, {_cid(1): 10})
    write_cache_manifest(
        cache, depotid=441, manifestid="900", chunks={_cid(1): 10}, request_code="aaa"
    )
    write_cache_manifest(
        cache, depotid=441, manifestid="900", chunks={_cid(1): 10}, request_code="bbb"
    )

    depot = only_depot(
        gc.plan_gc(
            440,
            inputs(
                mapping_rows=[(441, 440), (441, 730)],
                content_states={440: True, 730: True},
                # 730 records manifest 777, which exists nowhere ⇒ gate fires
                recorded={(440, 441): "900", (730, 441): "777"},
            ),
            depot_root=depot_root,
            archive_dir=str(archive_dir),
        )
    )

    assert depot.status == gc.STATUS_NO_MANIFEST
    assert depot.orphan_chunks == {}
    assert len(depot.dedupe) == 1
    assert depot.dedupe_bytes > 0


def test_plan_gc_bounds_the_unrecognized_examples(
    cache: Path, depot_root: str, archive_dir: Path
) -> None:
    write_archive_bin(archive_dir, depotid=441, manifestid="900", chunks={_cid(1): 10})
    write_chunks(cache, 441, {_cid(1): 10})
    chunk_dir = depot_dir(cache, 441) / "chunk"
    for index in range(gc.MAX_REPORTED_NAMES + 15):
        (chunk_dir / f"junk-{index}").write_bytes(b"x")

    depot = only_depot(
        gc.plan_gc(
            440,
            inputs(
                mapping_rows=[(441, 440)],
                content_states={440: True},
                recorded={(440, 441): "900"},
            ),
            depot_root=depot_root,
            archive_dir=str(archive_dir),
        )
    )

    assert depot.unrecognized_count == gc.MAX_REPORTED_NAMES + 15
    assert len(depot.unrecognized_examples) == gc.MAX_REPORTED_NAMES
    assert depot.orphan_chunks == {}


def test_plan_gc_covers_every_depot_of_the_app(
    cache: Path, depot_root: str, archive_dir: Path
) -> None:
    write_archive_bin(archive_dir, depotid=441, manifestid="900", chunks={_cid(1): 10})
    write_archive_bin(archive_dir, depotid=442, manifestid="901", chunks={_cid(2): 20})
    write_chunks(cache, 441, {_cid(1): 10, _cid(3): 30})
    write_chunks(cache, 442, {_cid(2): 20, _cid(4): 40})

    plan = gc.plan_gc(
        440,
        inputs(
            mapping_rows=[(441, 440), (442, 440)],
            content_states={440: True},
            recorded={(440, 441): "900", (440, 442): "901"},
        ),
        depot_root=depot_root,
        archive_dir=str(archive_dir),
    )

    assert [d.depotid for d in plan.depots] == [441, 442]
    assert plan.orphan_count == 2
    assert plan.orphan_bytes == 70
    assert plan.kept_bytes == 30
    assert plan.status_counts()[gc.STATUS_PLANNED] == 2


def test_plan_gc_ignores_depots_that_belong_only_to_other_apps(
    cache: Path, depot_root: str, archive_dir: Path
) -> None:
    write_chunks(cache, 999, {_cid(9): 90})

    plan = gc.plan_gc(
        440,
        inputs(mapping_rows=[(999, 730)], content_states={730: True}),
        depot_root=depot_root,
        archive_dir=str(archive_dir),
    )

    assert plan.depots == []


def test_plan_gc_is_read_only(
    cache: Path, depot_root: str, archive_dir: Path
) -> None:
    """A structural check that this package deletes nothing: snapshot every
    path and size under the cache root and the archive, plan, compare."""
    write_archive_bin(archive_dir, depotid=441, manifestid="900", chunks={_cid(1): 10})
    write_chunks(cache, 441, {_cid(1): 10, _cid(2): 20, _cid(3): 30})
    write_cache_manifest(
        cache, depotid=441, manifestid="900", chunks={_cid(1): 10}, request_code="aaa"
    )
    write_cache_manifest(
        cache, depotid=441, manifestid="900", chunks={_cid(1): 10}, request_code="bbb"
    )

    def snapshot() -> dict[str, int]:
        found: dict[str, int] = {}
        for base in (cache, archive_dir):
            for root, _dirs, files in os.walk(base):
                for name in files:
                    path = os.path.join(root, name)
                    found[path] = os.path.getsize(path)
        return found

    before = snapshot()
    plan = gc.plan_gc(
        440,
        inputs(
            mapping_rows=[(441, 440)],
            content_states={440: True},
            recorded={(440, 441): "900"},
        ),
        depot_root=depot_root,
        archive_dir=str(archive_dir),
    )

    assert plan.orphan_count == 2  # something really was planned
    assert snapshot() == before


@pytest.mark.parametrize(
    "case",
    [
        "unmapped",
        "unreadable_owner",
        "no_counting_apps",
        "no_manifest",
        "missing_dir",
        "unusable_depotid",
    ],
)
def test_no_skip_status_ever_carries_orphans(
    cache: Path, depot_root: str, archive_dir: Path, case: str
) -> None:
    """The module's primary fail-closed promise, once per skip reason: WP 3.8
    deleting "whatever the plan says" must delete nothing on a skip."""
    write_chunks(cache, 441, {_cid(1): 10, _cid(2): 20})

    rows: list[tuple[object, object]]
    states: dict[int, bool]
    if case == "unmapped":
        # A depot the app maps, whose only row is unreadable on the owner side
        # is covered below; "unmapped" is reached through resolve directly.
        resolution = gc.resolve_depot_chunkset(
            441, [], archive_dir=str(archive_dir), stored_manifests={}
        )
        assert resolution.status == gc.STATUS_UNMAPPED
        assert resolution.keep == {}
        return
    if case == "unreadable_owner":
        rows, states = [(441, 440), (441, "poison")], {440: True}
    elif case == "no_counting_apps":
        rows, states = [(441, 440)], {440: False}
    elif case == "no_manifest":
        rows, states = [(441, 440)], {440: True}
    elif case == "missing_dir":
        rows, states = [(4242, 440)], {440: True}
    else:  # unusable_depotid
        rows, states = [("../evil", 440)], {440: True}

    depot = only_depot(
        gc.plan_gc(
            440,
            inputs(mapping_rows=rows, content_states=states),
            depot_root=depot_root,
            archive_dir=str(archive_dir),
        )
    )

    assert depot.status != gc.STATUS_PLANNED
    assert depot.orphan_chunks == {}
    assert depot.orphan_bytes == 0


def test_plan_gc_handles_a_large_depot(
    cache: Path, depot_root: str, archive_dir: Path
) -> None:
    """The 70k-chunk case, scaled down to stay a unit test: the point is that
    the implementation is set-based, so this must not degrade quadratically."""
    kept = {_cid(i): 16 for i in range(1, 4001)}
    orphaned = {_cid(i): 8 for i in range(9001, 11001)}
    write_archive_bin(archive_dir, depotid=441, manifestid="900", chunks=kept)
    write_chunks(cache, 441, {**kept, **orphaned})

    depot = only_depot(
        gc.plan_gc(
            440,
            inputs(
                mapping_rows=[(441, 440)],
                content_states={440: True},
                recorded={(440, 441): "900"},
            ),
            depot_root=depot_root,
            archive_dir=str(archive_dir),
        )
    )

    assert depot.orphan_count == 2000
    assert depot.orphan_bytes == 2000 * 8
    assert depot.kept_count == 4000
    assert set(depot.orphan_chunks) == set(orphaned)


def test_plan_gc_parses_a_shared_manifest_only_once(
    cache: Path, depot_root: str, archive_dir: Path, monkeypatch
) -> None:
    """Two apps recording the same manifest for the same depot must not cost
    two parses (memoisation is what keeps a real depot's cost sane)."""
    write_archive_bin(archive_dir, depotid=441, manifestid="900", chunks={_cid(1): 10})
    write_chunks(cache, 441, {_cid(1): 10})

    calls: list[str] = []
    real = gc.parse_bin_payload

    def counting_parse(path, **kwargs):
        calls.append(path)
        return real(path, **kwargs)

    monkeypatch.setattr(gc, "parse_bin_payload", counting_parse)

    gc.plan_gc(
        440,
        inputs(
            mapping_rows=[(441, 440), (441, 730)],
            content_states={440: True, 730: True},
            recorded={(440, 441): "900", (730, 441): "900"},
        ),
        depot_root=depot_root,
        archive_dir=str(archive_dir),
    )

    assert len(calls) == 1


# ==========================================================================
# Database loaders
# ==========================================================================


@pytest.fixture
def conn(tmp_path: Path):
    from vault_api.db import get_connection, init_db

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    connection = get_connection(db_path)
    yield connection
    connection.close()


def test_load_gc_inputs_reads_mapping_content_state_and_manifests(conn) -> None:
    from vault_api.depot_manifests import upsert_depot_manifest
    from vault_api.mapping import upsert_mapping

    upsert_mapping(conn, depotid=441, appid=440, name="TF2")
    upsert_mapping(conn, depotid=441, appid=730, name="CS")
    conn.execute(
        "UPDATE apps SET status = 'done', last_prefill_at = '2026-08-09T10:00:00Z' "
        "WHERE appid = 440"
    )
    conn.commit()
    upsert_depot_manifest(
        conn,
        appid=440,
        containing_appid=440,
        depotid=441,
        manifestid="900",
        chunk_count=1,
        total_bytes=10,
        recorded_at="2026-08-09T10:00:00Z",
        source="steamprefill_bin",
    )

    loaded = gc.load_gc_inputs(conn, 440)

    assert sorted(loaded.mapping_rows) == [(441, 440), (441, 730)]
    assert loaded.content_states[440] is True
    assert loaded.content_states[730] is False  # idle, never prefilled, job-free
    assert loaded.recorded_manifests == {(440, 441): "900"}


def test_load_gc_inputs_end_to_end_against_a_real_tree(
    conn, cache: Path, depot_root: str, archive_dir: Path
) -> None:
    from vault_api.mapping import upsert_mapping
    from vault_api.depot_manifests import upsert_depot_manifest

    upsert_mapping(conn, depotid=441, appid=440, name="TF2")
    conn.execute("UPDATE apps SET status = 'done' WHERE appid = 440")
    conn.commit()
    upsert_depot_manifest(
        conn,
        appid=440,
        containing_appid=440,
        depotid=441,
        manifestid="900",
        chunk_count=1,
        total_bytes=10,
        recorded_at="2026-08-09T10:00:00Z",
        source="steamprefill_bin",
    )
    write_archive_bin(archive_dir, depotid=441, manifestid="900", chunks={_cid(1): 10})
    write_chunks(cache, 441, {_cid(1): 10, _cid(5): 55})

    depot = only_depot(
        gc.plan_gc(
            440,
            gc.load_gc_inputs(conn, 440),
            depot_root=depot_root,
            archive_dir=str(archive_dir),
        )
    )

    assert depot.status == gc.STATUS_PLANNED
    assert depot.orphan_chunks == {_cid(5): 55}


def test_load_content_states_batches_beyond_the_sqlite_parameter_limit(conn) -> None:
    """A poisoned/absurd mapping must degrade into more queries, never into
    "too many SQL variables"."""
    appids = list(range(1, gc.CONTENT_STATE_BATCH * 2 + 7))
    conn.executemany(
        "INSERT INTO apps (appid, status) VALUES (?, 'idle')", [(a,) for a in appids]
    )
    conn.commit()

    states = gc.load_content_states(conn, appids)

    assert len(states) == len(appids)
    assert all(value is False for value in states.values())


def test_load_recorded_manifests_skips_poisoned_rows(conn) -> None:
    from vault_api.mapping import upsert_mapping

    upsert_mapping(conn, depotid=441, appid=440, name="TF2")
    conn.execute(
        "INSERT INTO depot_manifests (appid, containing_appid, depotid, manifestid, "
        "chunk_count, total_bytes, recorded_at, source) "
        "VALUES ('not-an-appid', NULL, 441, '900', 1, 10, 'now', 'x')"
    )
    conn.commit()

    assert gc.load_recorded_manifests(conn, 440) == {}


# ==========================================================================
# parse_bin_payload — the WP 3.7 addition to manifests.py
# ==========================================================================


def test_parse_bin_payload_reads_the_archives_own_two_segment_filename(
    archive_dir: Path,
) -> None:
    """Regression guard for the reason this function was added: the archive
    renames its copies to {depotid}_{manifestid}.bin, which SteamPrefill's
    four-segment filename contract rejects."""
    from vault_api.manifests import ManifestParseError, parse_steamprefill_bin

    path = write_archive_bin(
        archive_dir, depotid=441, manifestid="900", chunks={_cid(1): 10}
    )

    with pytest.raises(ManifestParseError, match="filename contract"):
        parse_steamprefill_bin(str(path))

    manifest = gc.parse_bin_payload(
        str(path), filename_depot_id=441, filename_manifest_id=900
    )
    assert manifest.depot_id == 441
    assert manifest.chunks == {_cid(1): 10}
