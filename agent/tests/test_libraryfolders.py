"""Tests for libraryfolders.vdf parsing: modern + old flat formats.

Ground truth for the modern format was the real file at
c:\\steam\\steamapps\\libraryfolders.vdf on the dev machine (single library,
numbered block "0" with "path"/"apps" keys — see agent/README.md for the
fixture policy). The old flat format fixture is synthetic, modeled on the
documented pre-2019 Steam client format, since this dev machine only has
the modern one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vault_agent.acf import VdfParseError, parse_libraryfolders, parse_libraryfolders_file

FIXTURES = Path(__file__).parent / "fixtures"


def test_modern_format_multiple_libraries() -> None:
    paths = parse_libraryfolders_file(FIXTURES / "libraryfolders_modern.vdf")
    assert paths == [
        Path("C:/VaultTest/SteamMain"),
        Path("D:/VaultTest/SteamLibrary2"),
    ]


def test_old_flat_format_skips_non_numeric_keys() -> None:
    paths = parse_libraryfolders_file(FIXTURES / "libraryfolders_old_flat.vdf")
    assert paths == [
        Path("E:/VaultTest/OldStyleLibrary"),
        Path("F:/VaultTest/AnotherOldLibrary"),
    ]


def test_library_entry_without_apps_key_still_yields_path() -> None:
    paths = parse_libraryfolders_file(FIXTURES / "libraryfolders_no_apps_key.vdf")
    assert paths == [Path("C:/VaultTest/SteamMain")]


def test_corrupt_file_raises_parse_error() -> None:
    with pytest.raises(VdfParseError):
        parse_libraryfolders_file(FIXTURES / "libraryfolders_corrupt.vdf")


def test_missing_file_raises_parse_error() -> None:
    with pytest.raises(VdfParseError):
        parse_libraryfolders_file(FIXTURES / "does_not_exist.vdf")


def test_duplicate_numbered_entry_last_wins() -> None:
    # A malformed/hostile file with the same library index twice: the
    # underlying dict-level "last wins" rule (parse_vdf) means only the
    # second path survives -- it never becomes a 2-entry list.
    text = """
    "libraryfolders"
    {
        "0" { "path" "C:\\\\VaultTest\\\\First" }
        "0" { "path" "C:\\\\VaultTest\\\\Second" }
    }
    """
    paths = parse_libraryfolders(text)
    assert paths == [Path("C:/VaultTest/Second")]


def test_utf8_bom_prefixed_libraryfolders_file_parses_correctly(tmp_path: Path) -> None:
    content = (
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"C:\\\\VaultTest\\\\BomLib"\n\t}\n}\n'
    )
    bom_path = tmp_path / "libraryfolders.vdf"
    bom_path.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))

    paths = parse_libraryfolders_file(bom_path)
    assert paths == [Path("C:/VaultTest/BomLib")]
