"""End-to-end tests for discover_installed() against a synthetic multi-
library tmp tree, plus resilience cases (corrupt files, duplicate appids,
missing directories) built the same way.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from vault_agent.acf import discover_installed

MODERN_APPMANIFEST = """"AppState"
{{
\t"appid"\t\t"{appid}"
\t"name"\t\t"{name}"
\t"StateFlags"\t\t"{state_flags}"
\t"SizeOnDisk"\t\t"{size}"
}}
"""


def _write_manifest(steamapps_dir: Path, appid: str, name: str, state_flags: int, size: int) -> None:
    steamapps_dir.mkdir(parents=True, exist_ok=True)
    (steamapps_dir / f"appmanifest_{appid}.acf").write_text(
        MODERN_APPMANIFEST.format(appid=appid, name=name, state_flags=state_flags, size=size),
        encoding="utf-8",
    )


def _write_libraryfolders(main_steamapps: Path, main_path: Path, extra_paths: list[Path]) -> None:
    main_steamapps.mkdir(parents=True, exist_ok=True)
    blocks = [f'\t"0"\n\t{{\n\t\t"path"\t\t"{_vdf_escape(main_path)}"\n\t}}']
    for i, p in enumerate(extra_paths, start=1):
        blocks.append(f'\t"{i}"\n\t{{\n\t\t"path"\t\t"{_vdf_escape(p)}"\n\t}}')
    content = '"libraryfolders"\n{\n' + "\n".join(blocks) + "\n}\n"
    (main_steamapps / "libraryfolders.vdf").write_text(content, encoding="utf-8")


def _vdf_escape(path: Path) -> str:
    return str(path).replace("\\", "\\\\")


def test_discover_end_to_end_multi_library(tmp_path: Path) -> None:
    main_lib = tmp_path / "main"
    second_lib = tmp_path / "second"
    main_steamapps = main_lib / "steamapps"
    second_steamapps = second_lib / "steamapps"

    _write_libraryfolders(main_steamapps, main_lib, [second_lib])
    _write_manifest(main_steamapps, "100", "Game One", 4, 111)
    _write_manifest(main_steamapps, "200", "Game Two", 4, 222)
    _write_manifest(second_steamapps, "300", "Game Three", 6, 333)

    apps = discover_installed(main_lib)

    appids = {a.appid for a in apps}
    assert appids == {"100", "200", "300"}

    by_id = {a.appid: a for a in apps}
    assert by_id["100"].library_path == main_lib
    assert by_id["300"].library_path == second_lib
    assert by_id["300"].installed is True  # StateFlags 6 still has the installed bit


def test_discover_skips_corrupt_manifest_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    main_lib = tmp_path / "main"
    main_steamapps = main_lib / "steamapps"
    _write_libraryfolders(main_steamapps, main_lib, [])
    _write_manifest(main_steamapps, "100", "Good Game", 4, 111)

    corrupt = main_steamapps / "appmanifest_999.acf"
    corrupt.write_text('"AppState" { "appid" "999" "name" "Broken', encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        apps = discover_installed(main_lib)

    assert {a.appid for a in apps} == {"100"}
    assert any("999" in record.message for record in caplog.records)


def test_discover_duplicate_appid_first_wins_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    main_lib = tmp_path / "main"
    second_lib = tmp_path / "second"
    main_steamapps = main_lib / "steamapps"
    second_steamapps = second_lib / "steamapps"

    _write_libraryfolders(main_steamapps, main_lib, [second_lib])
    _write_manifest(main_steamapps, "100", "Original", 4, 111)
    _write_manifest(second_steamapps, "100", "Duplicate", 4, 999)

    with caplog.at_level(logging.WARNING):
        apps = discover_installed(main_lib)

    assert len(apps) == 1
    assert apps[0].name == "Original"
    assert apps[0].library_path == main_lib
    assert any("duplicate appid" in record.message for record in caplog.records)


def test_discover_tolerates_missing_library_directory(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    main_lib = tmp_path / "main"
    missing_lib = tmp_path / "does_not_exist_on_disk"
    main_steamapps = main_lib / "steamapps"

    _write_libraryfolders(main_steamapps, main_lib, [missing_lib])
    _write_manifest(main_steamapps, "100", "Good Game", 4, 111)

    with caplog.at_level(logging.WARNING):
        apps = discover_installed(main_lib)

    assert {a.appid for a in apps} == {"100"}
    assert any(str(missing_lib) in record.message for record in caplog.records)


def test_discover_falls_back_when_libraryfolders_missing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    main_lib = tmp_path / "main"
    main_steamapps = main_lib / "steamapps"
    main_steamapps.mkdir(parents=True)
    _write_manifest(main_steamapps, "100", "Solo Game", 4, 111)
    # Deliberately no libraryfolders.vdf written at all.

    with caplog.at_level(logging.WARNING):
        apps = discover_installed(main_lib)

    assert {a.appid for a in apps} == {"100"}
    assert any("libraryfolders.vdf" in record.message for record in caplog.records)


def test_discover_falls_back_when_libraryfolders_corrupt(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    main_lib = tmp_path / "main"
    main_steamapps = main_lib / "steamapps"
    main_steamapps.mkdir(parents=True)
    (main_steamapps / "libraryfolders.vdf").write_text(
        '"libraryfolders" { "0" { "path" "unterminated', encoding="utf-8"
    )
    _write_manifest(main_steamapps, "100", "Solo Game", 4, 111)

    with caplog.at_level(logging.WARNING):
        apps = discover_installed(main_lib)

    assert {a.appid for a in apps} == {"100"}


def test_discover_on_completely_empty_root_returns_empty_list(tmp_path: Path) -> None:
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    assert discover_installed(empty_root) == []
