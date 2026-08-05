"""Manifest parsers (vault_api/manifests.py, WP 3.1) — pure-function tests.

All fixtures are built **in code** (deliberately, per the work package): a
tiny varint/protobuf encoder lives right here, so no binary blob needs to
be committed to the repo and the test itself documents the exact bytes
being exercised. The encoders below are intentionally independent of
``manifests.py``'s own wire reader — they exist so a bug in the reader
can't hide behind a matching bug in the writer.

Real-file cross-checks (SteamPrefill's ``%LOCALAPPDATA%\\SteamPrefill\\v1``
and ``poc/cache/depot/*/manifest/``) were run manually while building this
module and are reported in the work-package summary, not committed here —
those files are machine-local and not fixtures a CI run could rely on.
"""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

import pytest

from vault_api.manifests import (
    CHUNK_ID_HEX_LEN,
    ManifestParseError,
    parse_cache_manifest,
    parse_steamprefill_bin,
)

# --------------------------------------------------------------------------
# Minimal protobuf/wire encoders (independent of manifests.py's reader)
# --------------------------------------------------------------------------


def _encode_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _tag(field_number: int, wire_type: int) -> bytes:
    return _encode_varint((field_number << 3) | wire_type)


def _varint_field(field_number: int, value: int) -> bytes:
    return _tag(field_number, 0) + _encode_varint(value)


def _bytes_field(field_number: int, data: bytes) -> bytes:
    return _tag(field_number, 2) + _encode_varint(len(data)) + data


#: A syntactically valid 40-hex chunk id, distinguishable per index for
#: fixtures that need several distinct ids.
def _chunk_id(index: int) -> str:
    return f"{index:040x}"[-CHUNK_ID_HEX_LEN:]


def _chunk_sha20(index: int) -> bytes:
    """20 raw bytes, distinguishable per index (for the cache-manifest format)."""
    return bytes([index % 256]) + bytes(19)


# -- SteamPrefill .bin ------------------------------------------------------


def _bin_chunk_data(chunk_id: str, compressed_length: int) -> bytes:
    return _bytes_field(1, chunk_id.encode("ascii")) + _varint_field(2, compressed_length)


def _bin_file_data(chunk_specs: list[tuple[str, int]]) -> bytes:
    return b"".join(_bytes_field(1, _bin_chunk_data(cid, size)) for cid, size in chunk_specs)


def _bin_manifest_bytes(
    *, depot_id: int, manifest_id: int, files: list[list[tuple[str, int]]]
) -> bytes:
    """Build a full ``.bin`` payload: repeated field 1 (FileData), field 2
    (manifest id), field 4 (depot id) — field order doesn't matter to a
    correct protobuf reader, but this mirrors what a real encoder would
    likely emit (fields ascending)."""
    body = b"".join(_bytes_field(1, _bin_file_data(chunks)) for chunks in files)
    body += _varint_field(2, manifest_id)
    body += _varint_field(4, depot_id)
    return body


def _write_bin(tmp_path: Path, filename: str, data: bytes) -> Path:
    path = tmp_path / filename
    path.write_bytes(data)
    return path


# -- cache-stored manifest (zip -> sections -> protobuf) --------------------

_MAGIC_PAYLOAD = 0x71F617D0
_MAGIC_METADATA = 0x1F4812BE
_MAGIC_SIGNATURE = 0x1B81B817
_MAGIC_END = 0x32C415AB


def _section(magic: int, payload: bytes) -> bytes:
    return struct.pack("<II", magic, len(payload)) + payload


def _cache_chunk_data(sha20: bytes, cb_compressed: int) -> bytes:
    return _bytes_field(1, sha20) + _varint_field(5, cb_compressed)


def _cache_file_mapping(chunk_specs: list[tuple[bytes, int]]) -> bytes:
    return b"".join(_bytes_field(6, _cache_chunk_data(sha, size)) for sha, size in chunk_specs)


def _cache_metadata_bytes(*, depot_id: int, manifest_id: int, unique_chunks: int) -> bytes:
    return (
        _varint_field(1, depot_id)
        + _varint_field(2, manifest_id)
        + _varint_field(7, unique_chunks)
    )


def _cache_payload_bytes(file_mappings: list[list[tuple[bytes, int]]]) -> bytes:
    return b"".join(_bytes_field(1, _cache_file_mapping(fm)) for fm in file_mappings)


def _sectioned_stream(
    *,
    depot_id: int,
    manifest_id: int,
    file_mappings: list[list[tuple[bytes, int]]],
    unique_chunks: int | None = None,
    include_signature: bool = True,
    include_end: bool = True,
) -> bytes:
    """A full sectioned stream: PAYLOAD, METADATA, (SIGNATURE), (END).

    ``unique_chunks`` defaults to the true chunk count across
    ``file_mappings`` — pass an explicit value to build a deliberately
    inconsistent fixture for the mismatch test.
    """
    if unique_chunks is None:
        unique_chunks = sum(len(fm) for fm in file_mappings)

    stream = _section(_MAGIC_PAYLOAD, _cache_payload_bytes(file_mappings))
    stream += _section(
        _MAGIC_METADATA,
        _cache_metadata_bytes(
            depot_id=depot_id, manifest_id=manifest_id, unique_chunks=unique_chunks
        ),
    )
    if include_signature:
        stream += _section(_MAGIC_SIGNATURE, b"\x00" * 8)
    if include_end:
        stream += struct.pack("<I", _MAGIC_END)
    return stream


def _write_cache_manifest_zip(
    tmp_path: Path, filename: str, section_bytes: bytes, *, entry_name: str = "z"
) -> Path:
    path = tmp_path / filename
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(entry_name, section_bytes)
    path.write_bytes(buf.getvalue())
    return path


# ==========================================================================
# parse_steamprefill_bin
# ==========================================================================


def test_bin_happy_path_two_files_multiple_chunks(tmp_path: Path) -> None:
    files = [
        [(_chunk_id(1), 1000), (_chunk_id(2), 2000)],
        [(_chunk_id(3), 3000)],
    ]
    data = _bin_manifest_bytes(depot_id=441, manifest_id=123456789, files=files)
    path = _write_bin(tmp_path, "440_440_441_123456789.bin", data)

    manifest = parse_steamprefill_bin(path)

    assert manifest.depot_id == 441
    assert manifest.manifest_id == 123456789
    assert manifest.source == "steamprefill_bin"
    assert manifest.chunks == {
        _chunk_id(1): 1000,
        _chunk_id(2): 2000,
        _chunk_id(3): 3000,
    }


def test_bin_shared_depot_filename_containing_app_differs_from_original(
    tmp_path: Path,
) -> None:
    """Real example from the research doc: ``107100_228980_229002_...bin`` —
    app 107100 pulling a depot that "belongs" to app 228980. The parser must
    not require originalAppId == containingAppId."""
    data = _bin_manifest_bytes(
        depot_id=229002, manifest_id=987, files=[[(_chunk_id(9), 42)]]
    )
    path = _write_bin(tmp_path, "107100_228980_229002_987.bin", data)

    manifest = parse_steamprefill_bin(path)

    assert manifest.depot_id == 229002
    assert manifest.manifest_id == 987


def test_bin_zero_chunks_is_not_an_error(tmp_path: Path) -> None:
    data = _bin_manifest_bytes(depot_id=441, manifest_id=1, files=[])
    path = _write_bin(tmp_path, "440_440_441_1.bin", data)

    manifest = parse_steamprefill_bin(path)

    assert manifest.chunks == {}


@pytest.mark.parametrize(
    "filename",
    [
        "441_123.bin",  # too few segments
        "440_440_441_441_123.bin",  # too many segments
        "440_440_441.bin",  # missing manifest id segment entirely
    ],
)
def test_bin_filename_rejects_wrong_segment_count(tmp_path: Path, filename: str) -> None:
    path = _write_bin(tmp_path, filename, b"irrelevant")
    with pytest.raises(ManifestParseError, match="filename contract"):
        parse_steamprefill_bin(path)


@pytest.mark.parametrize(
    "filename",
    [
        "440_440_ 441_123.bin",  # whitespace inside a segment
        "440_440_+441_123.bin",  # leading sign
        "440_440_441_-123.bin",  # negative
        "440_440_441_12.5.bin",  # non-digit character (still 4 segments)
    ],
)
def test_bin_filename_rejects_non_ascii_digit_segments(tmp_path: Path, filename: str) -> None:
    path = _write_bin(tmp_path, filename, b"irrelevant")
    with pytest.raises(ManifestParseError):
        parse_steamprefill_bin(path)


def test_bin_filename_rejects_missing_extension(tmp_path: Path) -> None:
    path = tmp_path / "440_440_441_123"
    path.write_bytes(b"irrelevant")
    with pytest.raises(ManifestParseError, match=r"\.bin extension"):
        parse_steamprefill_bin(path)


def test_bin_depot_id_mismatch_between_filename_and_payload_is_rejected(
    tmp_path: Path,
) -> None:
    data = _bin_manifest_bytes(depot_id=999, manifest_id=123, files=[])
    # filename claims depot 441, payload says 999
    path = _write_bin(tmp_path, "440_440_441_123.bin", data)

    with pytest.raises(ManifestParseError, match="depot id"):
        parse_steamprefill_bin(path)


def test_bin_manifest_id_mismatch_between_filename_and_payload_is_rejected(
    tmp_path: Path,
) -> None:
    data = _bin_manifest_bytes(depot_id=441, manifest_id=999, files=[])
    # filename claims manifest id 123, payload says 999
    path = _write_bin(tmp_path, "440_440_441_123.bin", data)

    with pytest.raises(ManifestParseError, match="manifest id"):
        parse_steamprefill_bin(path)


def test_bin_truncated_varint_is_rejected(tmp_path: Path) -> None:
    # A tag byte with the continuation bit set and nothing after it.
    data = bytes([0x80])
    path = _write_bin(tmp_path, "440_440_441_123.bin", data)

    with pytest.raises(ManifestParseError, match="truncated varint"):
        parse_steamprefill_bin(path)


def test_bin_oversized_declared_length_is_rejected(tmp_path: Path) -> None:
    # Field 1 (FileData), claims a length far larger than what follows.
    data = _tag(1, 2) + _encode_varint(10_000) + b"short"
    path = _write_bin(tmp_path, "440_440_441_123.bin", data)

    with pytest.raises(ManifestParseError, match="only"):
        parse_steamprefill_bin(path)


def test_bin_missing_manifest_id_field_is_rejected(tmp_path: Path) -> None:
    # Only depot id (field 4) present, no manifest id (field 2).
    data = _varint_field(4, 441)
    path = _write_bin(tmp_path, "440_440_441_123.bin", data)

    with pytest.raises(ManifestParseError, match="missing required field 2"):
        parse_steamprefill_bin(path)


def test_bin_chunk_id_wrong_length_is_rejected(tmp_path: Path) -> None:
    bad_chunk = _bytes_field(1, b"deadbeef") + _varint_field(2, 10)  # only 8 hex chars
    file_data = _bytes_field(1, bad_chunk)
    body = _bytes_field(1, file_data) + _varint_field(2, 1) + _varint_field(4, 441)
    path = _write_bin(tmp_path, "440_440_441_1.bin", body)

    with pytest.raises(ManifestParseError, match="hex string"):
        parse_steamprefill_bin(path)


def test_bin_empty_file_is_rejected(tmp_path: Path) -> None:
    path = _write_bin(tmp_path, "440_440_441_123.bin", b"")
    with pytest.raises(ManifestParseError, match="empty file"):
        parse_steamprefill_bin(path)


def test_bin_missing_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "440_440_441_123.bin"  # never written
    with pytest.raises(ManifestParseError, match="cannot read"):
        parse_steamprefill_bin(path)


def test_bin_max_chunk_count_bound_is_enforced(tmp_path: Path, monkeypatch) -> None:
    """Cheap exercise of the sanity bound: shrink the ceiling instead of
    building millions of chunks."""
    import vault_api.manifests as manifests_module

    monkeypatch.setattr(manifests_module, "MAX_CHUNK_COUNT", 2)
    files = [[(_chunk_id(i), i) for i in range(3)]]
    data = _bin_manifest_bytes(depot_id=441, manifest_id=1, files=files)
    path = _write_bin(tmp_path, "440_440_441_1.bin", data)

    with pytest.raises(ManifestParseError, match="chunk sanity bound"):
        parse_steamprefill_bin(path)


def test_bin_max_message_size_bound_is_enforced(tmp_path: Path, monkeypatch) -> None:
    import vault_api.manifests as manifests_module

    monkeypatch.setattr(manifests_module, "MAX_MESSAGE_SIZE", 4)
    data = _bin_manifest_bytes(depot_id=441, manifest_id=1, files=[])
    path = _write_bin(tmp_path, "440_440_441_1.bin", data)

    with pytest.raises(ManifestParseError, match="byte bound"):
        parse_steamprefill_bin(path)


# ==========================================================================
# parse_cache_manifest
# ==========================================================================


def test_cache_manifest_happy_path_two_file_mappings(tmp_path: Path) -> None:
    file_mappings = [
        [(_chunk_sha20(1), 1000), (_chunk_sha20(2), 2000)],
        [(_chunk_sha20(3), 3000)],
    ]
    stream = _sectioned_stream(depot_id=481, manifest_id=999, file_mappings=file_mappings)
    path = _write_cache_manifest_zip(tmp_path, "manifest.zip", stream)

    manifest = parse_cache_manifest(path)

    assert manifest.depot_id == 481
    assert manifest.manifest_id == 999
    assert manifest.source == "cache_manifest"
    assert manifest.chunks == {
        _chunk_sha20(1).hex(): 1000,
        _chunk_sha20(2).hex(): 2000,
        _chunk_sha20(3).hex(): 3000,
    }


def test_cache_manifest_without_signature_section_still_parses(tmp_path: Path) -> None:
    stream = _sectioned_stream(
        depot_id=481,
        manifest_id=1,
        file_mappings=[[(_chunk_sha20(1), 10)]],
        include_signature=False,
    )
    path = _write_cache_manifest_zip(tmp_path, "manifest.zip", stream)

    manifest = parse_cache_manifest(path)
    assert manifest.chunks == {_chunk_sha20(1).hex(): 10}


def test_cache_manifest_zero_chunks_is_not_an_error(tmp_path: Path) -> None:
    stream = _sectioned_stream(depot_id=481, manifest_id=1, file_mappings=[])
    path = _write_cache_manifest_zip(tmp_path, "manifest.zip", stream)

    manifest = parse_cache_manifest(path)
    assert manifest.chunks == {}


def test_cache_manifest_unique_chunks_mismatch_is_rejected(tmp_path: Path) -> None:
    stream = _sectioned_stream(
        depot_id=481,
        manifest_id=1,
        file_mappings=[[(_chunk_sha20(1), 10), (_chunk_sha20(2), 20)]],
        unique_chunks=5,  # lie: only 2 chunks actually present
    )
    path = _write_cache_manifest_zip(tmp_path, "manifest.zip", stream)

    with pytest.raises(ManifestParseError, match="unique_chunks"):
        parse_cache_manifest(path)


def test_cache_manifest_duplicate_sha_across_file_mappings_collapses_to_one(
    tmp_path: Path,
) -> None:
    """A chunk shared by two files is one chunk on disk — unique_chunks
    counts distinct chunks, and so must the parser's map."""
    shared = _chunk_sha20(7)
    stream = _sectioned_stream(
        depot_id=481,
        manifest_id=1,
        file_mappings=[[(shared, 111)], [(shared, 111)]],
        unique_chunks=1,
    )
    path = _write_cache_manifest_zip(tmp_path, "manifest.zip", stream)

    manifest = parse_cache_manifest(path)
    assert manifest.chunks == {shared.hex(): 111}


def test_cache_manifest_missing_metadata_section_is_rejected(tmp_path: Path) -> None:
    stream = _section(_MAGIC_PAYLOAD, _cache_payload_bytes([])) + struct.pack(
        "<I", _MAGIC_END
    )
    path = _write_cache_manifest_zip(tmp_path, "manifest.zip", stream)

    with pytest.raises(ManifestParseError, match="METADATA"):
        parse_cache_manifest(path)


def test_cache_manifest_missing_payload_section_is_rejected(tmp_path: Path) -> None:
    stream = _section(
        _MAGIC_METADATA,
        _cache_metadata_bytes(depot_id=481, manifest_id=1, unique_chunks=0),
    ) + struct.pack("<I", _MAGIC_END)
    path = _write_cache_manifest_zip(tmp_path, "manifest.zip", stream)

    with pytest.raises(ManifestParseError, match="PAYLOAD"):
        parse_cache_manifest(path)


def test_cache_manifest_missing_end_marker_is_rejected(tmp_path: Path) -> None:
    stream = _sectioned_stream(
        depot_id=481, manifest_id=1, file_mappings=[], include_end=False
    )
    path = _write_cache_manifest_zip(tmp_path, "manifest.zip", stream)

    with pytest.raises(ManifestParseError, match="truncated section"):
        parse_cache_manifest(path)


def test_cache_manifest_truncated_section_length_is_rejected(tmp_path: Path) -> None:
    # A section header whose declared length runs past the end of the stream.
    stream = struct.pack("<II", _MAGIC_METADATA, 10_000) + b"short"
    path = _write_cache_manifest_zip(tmp_path, "manifest.zip", stream)

    with pytest.raises(ManifestParseError, match="only"):
        parse_cache_manifest(path)


def test_cache_manifest_bad_sha_length_is_rejected(tmp_path: Path) -> None:
    bad_chunk_data = _bytes_field(1, b"\x00" * 19) + _varint_field(5, 10)  # 19, not 20
    file_mapping = _bytes_field(6, bad_chunk_data)
    payload = _bytes_field(1, file_mapping)
    stream = _section(_MAGIC_PAYLOAD, payload) + _section(
        _MAGIC_METADATA,
        _cache_metadata_bytes(depot_id=481, manifest_id=1, unique_chunks=1),
    ) + struct.pack("<I", _MAGIC_END)
    path = _write_cache_manifest_zip(tmp_path, "manifest.zip", stream)

    with pytest.raises(ManifestParseError, match="20"):
        parse_cache_manifest(path)


def test_cache_manifest_non_zip_garbage_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "manifest.zip"
    path.write_bytes(b"this is not a zip file at all, just plain garbage bytes")

    with pytest.raises(ManifestParseError, match="not a valid zip file"):
        parse_cache_manifest(path)


def test_cache_manifest_wrong_zip_entry_name_is_rejected(tmp_path: Path) -> None:
    stream = _sectioned_stream(depot_id=481, manifest_id=1, file_mappings=[])
    path = _write_cache_manifest_zip(tmp_path, "manifest.zip", stream, entry_name="notz")

    with pytest.raises(ManifestParseError, match="no 'z' entry"):
        parse_cache_manifest(path)


def test_cache_manifest_empty_zip_entry_is_rejected(tmp_path: Path) -> None:
    path = _write_cache_manifest_zip(tmp_path, "manifest.zip", b"")

    with pytest.raises(ManifestParseError, match="empty manifest stream"):
        parse_cache_manifest(path)


def test_cache_manifest_missing_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "manifest.zip"  # never written
    with pytest.raises(ManifestParseError, match="cannot read"):
        parse_cache_manifest(path)


def test_cache_manifest_max_chunk_count_bound_is_enforced(
    tmp_path: Path, monkeypatch
) -> None:
    import vault_api.manifests as manifests_module

    monkeypatch.setattr(manifests_module, "MAX_CHUNK_COUNT", 2)
    file_mappings = [[(_chunk_sha20(i), i) for i in range(3)]]
    stream = _sectioned_stream(depot_id=481, manifest_id=1, file_mappings=file_mappings)
    path = _write_cache_manifest_zip(tmp_path, "manifest.zip", stream)

    with pytest.raises(ManifestParseError, match="chunk sanity bound"):
        parse_cache_manifest(path)


def test_cache_manifest_max_message_size_bound_is_enforced(
    tmp_path: Path, monkeypatch
) -> None:
    import vault_api.manifests as manifests_module

    monkeypatch.setattr(manifests_module, "MAX_MESSAGE_SIZE", 4)
    stream = _sectioned_stream(depot_id=481, manifest_id=1, file_mappings=[])
    path = _write_cache_manifest_zip(tmp_path, "manifest.zip", stream)

    with pytest.raises(ManifestParseError, match="byte bound"):
        parse_cache_manifest(path)


# ==========================================================================
# Shared shape
# ==========================================================================


def test_both_formats_return_the_same_dataclass_type(tmp_path: Path) -> None:
    """The "common shape usable by GC" requirement (WP 3.1 item 4): both
    parsers' return values are instances of one shared type, not merely two
    structurally-similar ones."""
    from vault_api.manifests import CacheManifest, ParsedManifest, PrefillManifest

    assert PrefillManifest is ParsedManifest
    assert CacheManifest is ParsedManifest

    bin_path = _write_bin(
        tmp_path,
        "440_440_441_1.bin",
        _bin_manifest_bytes(depot_id=441, manifest_id=1, files=[]),
    )
    zip_path = _write_cache_manifest_zip(
        tmp_path,
        "manifest.zip",
        _sectioned_stream(depot_id=481, manifest_id=1, file_mappings=[]),
    )

    bin_manifest = parse_steamprefill_bin(bin_path)
    cache_manifest = parse_cache_manifest(zip_path)

    assert type(bin_manifest) is type(cache_manifest) is ParsedManifest
