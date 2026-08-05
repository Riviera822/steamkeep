"""vault_api/manifest_archive.py (WP 3.2): archive + retention."""

from __future__ import annotations

import os
from pathlib import Path

from vault_api.manifest_archive import archive_filename, archive_manifest, prune_archive


def test_archive_manifest_copies_bytes_verbatim(tmp_path: Path) -> None:
    src = tmp_path / "source.bin"
    src.write_bytes(b"fake-manifest-payload")
    archive_dir = tmp_path / "archive"

    dest = archive_manifest(str(archive_dir), depotid=441, manifestid="123", src_path=str(src))

    assert os.path.basename(dest) == "441_123.bin"
    assert Path(dest).read_bytes() == b"fake-manifest-payload"


def test_archive_manifest_creates_the_archive_dir_if_missing(tmp_path: Path) -> None:
    src = tmp_path / "source.bin"
    src.write_bytes(b"x")
    archive_dir = tmp_path / "does" / "not" / "exist" / "yet"

    dest = archive_manifest(str(archive_dir), depotid=1, manifestid="1", src_path=str(src))

    assert Path(dest).exists()


def test_archive_manifest_leaves_no_tempfile_behind_on_success(tmp_path: Path) -> None:
    src = tmp_path / "source.bin"
    src.write_bytes(b"payload")
    archive_dir = tmp_path / "archive"

    archive_manifest(str(archive_dir), depotid=441, manifestid="123", src_path=str(src))

    names = os.listdir(archive_dir)
    assert names == ["441_123.bin"]  # no leftover .tmp file


def test_archive_manifest_reingesting_the_same_pair_overwrites_cleanly(tmp_path: Path) -> None:
    """A repeated ingest of an already-current app's manifest re-archives the
    same (depotid, manifestid) -- os.replace must overwrite, not fail or
    duplicate."""
    src1 = tmp_path / "a.bin"
    src1.write_bytes(b"first-version")
    src2 = tmp_path / "b.bin"
    src2.write_bytes(b"second-version-longer-payload")
    archive_dir = tmp_path / "archive"

    archive_manifest(str(archive_dir), depotid=441, manifestid="123", src_path=str(src1))
    dest = archive_manifest(str(archive_dir), depotid=441, manifestid="123", src_path=str(src2))

    assert Path(dest).read_bytes() == b"second-version-longer-payload"
    assert os.listdir(archive_dir) == ["441_123.bin"]


def test_archive_manifest_raises_oserror_for_a_missing_source(tmp_path: Path) -> None:
    import pytest

    archive_dir = tmp_path / "archive"
    with pytest.raises(OSError):
        archive_manifest(
            str(archive_dir), depotid=441, manifestid="1",
            src_path=str(tmp_path / "does-not-exist.bin"),
        )
    # And no tempfile leftover from the failed attempt.
    assert not os.path.isdir(archive_dir) or os.listdir(archive_dir) == []


def test_archive_filename_matches_the_documented_template() -> None:
    assert archive_filename(depotid=441, manifestid="123") == "441_123.bin"


# -- retention / pruning ----------------------------------------------------


def _archive_n(archive_dir: Path, depotid: int, manifestids: list[str]) -> None:
    """Archive several manifests for one depot, in order, with distinct
    mtimes (the pruning signal) -- a small sleep would be flaky under load,
    so mtimes are set explicitly instead."""
    import time

    for index, manifestid in enumerate(manifestids):
        src = archive_dir.parent / f"src-{depotid}-{manifestid}.bin"
        src.write_bytes(f"payload-{manifestid}".encode())
        dest = archive_manifest(
            str(archive_dir), depotid=depotid, manifestid=manifestid, src_path=str(src)
        )
        # Force a strictly increasing mtime per archived file, oldest first,
        # regardless of how fast the filesystem clock ticks in a test run.
        stamp = time.time() + index
        os.utime(dest, (stamp, stamp))


def test_prune_archive_keeps_only_the_newest_n(tmp_path: Path) -> None:
    archive_dir = tmp_path / "archive"
    _archive_n(archive_dir, depotid=441, manifestids=["1", "2", "3", "4", "5"])

    removed = prune_archive(str(archive_dir), depotid=441, keep=3)

    remaining = sorted(os.listdir(archive_dir))
    assert remaining == ["441_3.bin", "441_4.bin", "441_5.bin"]
    assert set(removed) == {"441_1.bin", "441_2.bin"}


def test_prune_archive_never_removes_more_depots_than_asked(tmp_path: Path) -> None:
    """A shorter depot id must not prefix-match a longer one's files
    (depot 44 vs depot 441)."""
    archive_dir = tmp_path / "archive"
    _archive_n(archive_dir, depotid=44, manifestids=["1", "2"])
    _archive_n(archive_dir, depotid=441, manifestids=["1", "2"])

    prune_archive(str(archive_dir), depotid=44, keep=1)

    remaining = set(os.listdir(archive_dir))
    # Only depot 44's older file is pruned; depot 441's two files survive.
    assert remaining == {"44_2.bin", "441_1.bin", "441_2.bin"}


def test_prune_archive_keep_at_or_above_count_removes_nothing(tmp_path: Path) -> None:
    archive_dir = tmp_path / "archive"
    _archive_n(archive_dir, depotid=441, manifestids=["1", "2"])

    removed = prune_archive(str(archive_dir), depotid=441, keep=5)

    assert removed == []
    assert sorted(os.listdir(archive_dir)) == ["441_1.bin", "441_2.bin"]


def test_prune_archive_on_a_missing_directory_returns_empty_and_does_not_raise(
    tmp_path: Path,
) -> None:
    removed = prune_archive(str(tmp_path / "never-created"), depotid=441, keep=3)
    assert removed == []
