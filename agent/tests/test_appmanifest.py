"""Tests for appmanifest_<appid>.acf extraction, incl. StateFlags variants.

StateFlags semantics (documented in vault_agent/acf.py): bit 4 means
"fully installed". This was empirically verified against every real
appmanifest on the dev machine's c:\\steam install (all currently show
StateFlags == 4, since nothing there has a pending update or partial
download right now). The update-required (6) and partial (2) fixtures
below are synthetic, modeled on Valve's publicly documented StateFlags
bit combinations, since no such file exists on this machine to copy from.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vault_agent.acf import (
    VdfParseError,
    _parse_strict_uint,
    parse_appmanifest,
    parse_appmanifest_file,
)

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_LIBRARY = Path("C:/VaultTest/SteamMain")


def test_fully_installed_app() -> None:
    app = parse_appmanifest_file(
        FIXTURES / "appmanifest_installed.acf", library_path=FAKE_LIBRARY
    )
    assert app.appid == "999001"
    assert app.name == "Vault Test Game A"
    assert app.state_flags == 4
    assert app.installed is True
    assert app.size_on_disk == 1234567890
    assert app.library_path == FAKE_LIBRARY


def test_update_required_app_is_still_installed() -> None:
    # StateFlags 6 = installed (4) + update-required (2): still on disk.
    app = parse_appmanifest_file(
        FIXTURES / "appmanifest_update_required.acf", library_path=FAKE_LIBRARY
    )
    assert app.state_flags == 6
    assert app.installed is True


def test_partial_download_app_is_not_installed() -> None:
    # StateFlags 2 = update-required only, installed bit (4) not set.
    app = parse_appmanifest_file(
        FIXTURES / "appmanifest_partial.acf", library_path=FAKE_LIBRARY
    )
    assert app.state_flags == 2
    assert app.installed is False


def test_missing_size_on_disk_is_tolerated() -> None:
    app = parse_appmanifest_file(
        FIXTURES / "appmanifest_missing_size.acf", library_path=FAKE_LIBRARY
    )
    assert app.size_on_disk is None
    assert app.installed is True


def test_corrupt_file_raises_parse_error() -> None:
    with pytest.raises(VdfParseError):
        parse_appmanifest_file(FIXTURES / "appmanifest_corrupt.acf", library_path=FAKE_LIBRARY)


def test_missing_appstate_root_raises_parse_error() -> None:
    with pytest.raises(VdfParseError):
        parse_appmanifest_file(
            FIXTURES / "appmanifest_missing_appstate.acf", library_path=FAKE_LIBRARY
        )


def test_missing_file_raises_parse_error() -> None:
    with pytest.raises(VdfParseError):
        parse_appmanifest_file(FIXTURES / "does_not_exist.acf", library_path=FAKE_LIBRARY)


# --------------------------------------------------------------------------
# Strict digit grammar (_parse_strict_uint) — appid / StateFlags / SizeOnDisk
#
# Deliberately stricter than Python's int(), and pinned to match what Go's
# strconv.Atoi (base 10) accepts, since this parser is the executable
# specification a Go port (ADR-0005) must reproduce exactly.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("4", 4),
        ("0", 0),
        ("004", 4),  # leading zeros tolerated -- matches int() AND strconv.Atoi
        ("999999999999", 999999999999),
    ],
)
def test_parse_strict_uint_accepts(raw: str, expected: int) -> None:
    assert _parse_strict_uint(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " 4 ",  # surrounding whitespace -- int() accepts, strconv.Atoi does not
        "+4",  # explicit sign -- int() accepts, strconv.Atoi(base 10) does not
        "-4",  # explicit sign
        "1_0",  # underscore digit-group separator -- int() parses as 10
        "٦4",  # Arabic-Indic '٦' (six) + ascii '4': non-ASCII digit, int() -> 64
        "4.0",
        "0x4",
        "notanumber",
        "4 ",
        " 4",
    ],
)
def test_parse_strict_uint_rejects(raw: str) -> None:
    assert _parse_strict_uint(raw) is None


def test_appid_with_surrounding_whitespace_raises() -> None:
    with pytest.raises(VdfParseError):
        parse_appmanifest_file(
            FIXTURES / "appmanifest_appid_whitespace.acf", library_path=FAKE_LIBRARY
        )


def test_appid_non_numeric_raises() -> None:
    with pytest.raises(VdfParseError):
        parse_appmanifest_file(
            FIXTURES / "appmanifest_appid_nonnumeric.acf", library_path=FAKE_LIBRARY
        )


def test_state_flags_with_surrounding_whitespace_raises() -> None:
    # StateFlags is required; a value that fails the strict grammar must
    # raise, not silently coerce via int()'s liberal parsing.
    with pytest.raises(VdfParseError):
        parse_appmanifest_file(
            FIXTURES / "appmanifest_stateflags_whitespace.acf", library_path=FAKE_LIBRARY
        )


def test_size_on_disk_with_liberal_int_form_is_tolerated_as_none() -> None:
    # SizeOnDisk is the one field that degrades to None instead of raising
    # -- but "+123" must become None, not silently be accepted as 123.
    app = parse_appmanifest_file(
        FIXTURES / "appmanifest_sizeondisk_liberal.acf", library_path=FAKE_LIBRARY
    )
    assert app.size_on_disk is None
    assert app.appid == "999008"  # rest of the record still parses fine


def test_appid_leading_zeros_are_tolerated() -> None:
    # Consistent with _parse_strict_uint: leading zeros are not corruption.
    text = '"AppState" { "appid" "0042" "name" "Padded" "StateFlags" "4" }'
    app = parse_appmanifest(text, library_path=FAKE_LIBRARY)
    assert app.appid == "0042"


def test_utf8_bom_prefixed_appmanifest_file_parses_correctly(tmp_path: Path) -> None:
    # Written as raw bytes (not via the fixture files, which are read/
    # written as text and wouldn't reliably preserve a BOM) to prove the
    # utf-8-sig file read handles a real BOM'd file on disk.
    content = (
        '"AppState"\n{\n\t"appid"\t\t"999009"\n\t"name"\t\t"BOM Game"\n'
        '\t"StateFlags"\t\t"4"\n}\n'
    )
    bom_path = tmp_path / "appmanifest_999009.acf"
    bom_path.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))

    app = parse_appmanifest_file(bom_path, library_path=FAKE_LIBRARY)
    assert app.appid == "999009"
    assert app.name == "BOM Game"
    assert app.installed is True
