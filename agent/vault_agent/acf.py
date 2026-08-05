"""Parser for Valve's KeyValues text format (VDF/ACF).

Used by the Steam client for two files vault-agent cares about:

- ``steamapps/appmanifest_<appid>.acf`` — one installed app's metadata.
- ``steamapps/libraryfolders.vdf`` — the list of library folders (drives)
  Steam knows about, in both the modern (numbered blocks with "path"/"apps"
  keys) and the older flat format (numbered key -> path string directly).

This is a real tokenizer + recursive-descent parser for KeyValues, not
regex line-picking: it understands quoted strings (with escaped quotes and
backslashes), unquoted tokens, nested ``{ }`` blocks, and ``//`` line
comments. The format itself is small and stable, so this stays a single
small module per the "no third-party parser deps" rule (PROJECT_PLAN.md
section 9) — stdlib only.

Everything here is deliberately tolerant: malformed input degrades to a
``VdfParseError`` that callers catch, never an uncaught crash. Library and
manifest paths are taken as plain ``Path`` objects throughout so the Linux/
SteamOS agent variant (ADR-0002, Phase 2.5) can reuse this module unchanged
— only the *caller's* choice of root paths differs between Windows and
Linux.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# StateFlags is a bitmask. Only bit 4 ("fully installed") is used by this
# module, and it is the only bit empirically verified against every real
# appmanifest on this dev machine's c:\steam install — all 15 currently
# installed apps report StateFlags == 4 exactly (nothing mid-update or
# partially downloaded right now). The other bits below are documented
# publicly (SteamKit's EAppState enum, widely mirrored across community
# Steam tooling such as steamctl/ArchiSteamFarm/lancache-manager) but were
# NOT independently re-derived or verified against a real file here; they
# are reproduced so a future port (ADR-0005: vault-agent ships in Go) does
# not have to re-research them:
#
#     1        Uninstalled
#     2        UpdateRequired
#     4        FullyInstalled
#     8        Encrypted
#     16       Locked
#     32       FilesMissing
#     64       AppRunning
#     128      FilesCorrupt
#     256      UpdateRunning
#     512      UpdatePaused
#     1024     UpdateStarted
#     2048     Uninstalling
#     4096     BackupRunning
#     8192     Reconfiguring
#     16384    Validating
#     32768    AddingFiles
#     65536    Preallocating
#     131072   Downloading
#     262144   Staging
#     524288   Committing
#     1048576  UpdateStopping
#
# Bits combine (e.g. 6 = UpdateRequired | FullyInstalled: still installed,
# just stale — see InstalledApp.installed below), which is why this module
# checks `state_flags & STATE_FLAG_FULLY_INSTALLED` rather than equality.
STATE_FLAG_FULLY_INSTALLED = 4


class VdfParseError(Exception):
    """Raised on malformed KeyValues (VDF/ACF) input.

    Callers that walk many files (discover_installed) catch this, log a
    warning, and skip the offending file rather than crashing.
    """


# --------------------------------------------------------------------------
# Tokenizer
# --------------------------------------------------------------------------

_LBRACE = "{"
_RBRACE = "}"

# Token kinds
_T_STRING = "STRING"
_T_LBRACE = "LBRACE"
_T_RBRACE = "RBRACE"


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    pos: int  # character offset, for error messages


def _tokenize(text: str) -> list[_Token]:
    """Turn raw KeyValues text into a flat token list.

    Handles: quoted strings with ``\\"``/``\\\\``/``\\n``/``\\t``/``\\r``
    escapes, unquoted bareword tokens, ``{``/``}``, and ``//`` line
    comments. Raises VdfParseError on an unterminated quoted string.
    """
    tokens: list[_Token] = []
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        # Whitespace
        if ch in " \t\r\n":
            i += 1
            continue

        # Line comment
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            nl = text.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue

        # Braces
        if ch == _LBRACE:
            tokens.append(_Token(_T_LBRACE, ch, i))
            i += 1
            continue
        if ch == _RBRACE:
            tokens.append(_Token(_T_RBRACE, ch, i))
            i += 1
            continue

        # Quoted string
        if ch == '"':
            start = i
            i += 1
            out: list[str] = []
            closed = False
            while i < n:
                c = text[i]
                if c == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    if nxt == "n":
                        out.append("\n")
                    elif nxt == "t":
                        out.append("\t")
                    elif nxt == "r":
                        out.append("\r")
                    elif nxt in ('"', "\\"):
                        out.append(nxt)
                    else:
                        # Unknown escape: PRESERVE the backslash rather than
                        # silently dropping it. A lone backslash before an
                        # unescaped character is far more likely to be
                        # mildly-malformed real-world VDF (a Windows path
                        # like "C:\Steam\common" someone forgot to
                        # double-escape) than a deliberate unknown escape
                        # sequence. Dropping it would silently turn that
                        # into "C:Steamcommon" -- a *different, wrong*
                        # library path with no error raised. Keeping both
                        # characters is the safe default for a parser whose
                        # output feeds filesystem paths.
                        out.append("\\")
                        out.append(nxt)
                    i += 2
                    continue
                if c == '"':
                    closed = True
                    i += 1
                    break
                out.append(c)
                i += 1
            if not closed:
                raise VdfParseError(
                    f"unterminated quoted string starting at offset {start}"
                )
            tokens.append(_Token(_T_STRING, "".join(out), start))
            continue

        # Unquoted bareword token: read until whitespace, brace, or a
        # comment start.
        start = i
        out = []
        while i < n:
            c = text[i]
            if c in " \t\r\n" or c in (_LBRACE, _RBRACE):
                break
            if c == "/" and i + 1 < n and text[i + 1] == "/":
                break
            out.append(c)
            i += 1
        if not out:
            # Shouldn't happen (all single chars are handled above), but
            # guard against infinite loops on unexpected input.
            raise VdfParseError(f"unexpected character {ch!r} at offset {i}")
        tokens.append(_Token(_T_STRING, "".join(out), start))

    return tokens


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

# A KeyValues "object" is a key -> (string | nested object) mapping.
KeyValues = dict[str, "str | KeyValues"]

#: Maximum nesting depth _parse_object will recurse to before refusing
#: further input. Real appmanifest/libraryfolders files nest 3 levels deep
#: at most (e.g. AppState -> InstalledDepots -> <depotid>). 100 is
#: generous headroom while staying far below Python's default recursion
#: limit (1000), so a maliciously or corruptly deep-nested file raises a
#: clean VdfParseError instead of an uncaught RecursionError escaping the
#: single-exception-type contract discover_installed relies on.
_MAX_NESTING_DEPTH = 100


def _is_conditional_tag(token_value: str) -> bool:
    """True if a token looks like a KeyValues platform conditional, e.g.
    ``[$WIN32]`` / ``[$LINUX]`` / ``[$OSX]`` / ``[!$WIN32]``.

    See the "tolerate-and-skip" decision documented on ``_parse_object``.
    """
    return len(token_value) >= 2 and token_value[0] == "[" and token_value[-1] == "]"


def _parse_object(
    tokens: list[_Token], pos: int, *, top_level: bool = False, depth: int = 0
) -> tuple[KeyValues, int]:
    """Parse key/value pairs until a RBRACE (nested) or end of tokens (top level).

    Returns (parsed dict, next position). Duplicate keys at the same
    level overwrite (last wins) but do not raise — real-world VDF quirk
    tolerance per the work package.

    A nested block (top_level=False) MUST end with a RBRACE — running out
    of tokens first means an unclosed block, which raises. The true top
    level (top_level=True) has no enclosing braces at all, so it must end
    exactly at EOF — an unmatched RBRACE there is structural garbage and
    raises too.

    **Nesting depth is capped** (``_MAX_NESTING_DEPTH``): a nested block
    deeper than that raises VdfParseError instead of recursing further, so
    a hostile or corrupt file with thousands of nested ``{`` cannot crash
    the agent with an uncaught RecursionError (which is not a
    VdfParseError and would escape discover_installed's per-file catch).

    **Platform conditional tags** (``[$WIN32]`` etc.) immediately following
    a key's value are tolerated and skipped, not treated as the start of
    the next key/value pair. Valve's KeyValues format uses this suffix in
    some real files (controller configs, localization files) to mark a
    pair as platform-specific; failing the WHOLE file on encountering one
    would be needlessly fragile for a format-compliant construct.
    Deliberate simplification: the tag is stripped and *ignored* — the
    key/value pair is always kept regardless of which platform the tag
    names. This module never needs to filter by platform (the agent only
    ever reads the manifest of the platform it runs on), so evaluating the
    condition would be unused complexity. Only the position after a
    value is handled (the common, real-world placement); a conditional
    directly after a *key* and before its value is not recognized as one
    (not observed in practice for the two file types this module parses).
    """
    if not top_level and depth > _MAX_NESTING_DEPTH:
        tok = tokens[pos] if pos < len(tokens) else None
        offset = tok.pos if tok is not None else -1
        raise VdfParseError(
            f"exceeded max nesting depth ({_MAX_NESTING_DEPTH}) at offset {offset}; "
            "refusing to parse further (likely corrupt or hostile input)"
        )

    result: KeyValues = {}
    n = len(tokens)

    while pos < n:
        tok = tokens[pos]

        if tok.kind == _T_RBRACE:
            if top_level:
                raise VdfParseError(f"unexpected closing brace at offset {tok.pos}")
            return result, pos + 1  # consume the closing brace

        if tok.kind != _T_STRING:
            raise VdfParseError(f"expected a key string at offset {tok.pos}, found {tok.kind}")

        key = tok.value
        pos += 1

        if pos >= n:
            raise VdfParseError(f"key {key!r} at offset {tok.pos} has no value (end of input)")

        value_tok = tokens[pos]

        if value_tok.kind == _T_LBRACE:
            nested, pos = _parse_object(tokens, pos + 1, top_level=False, depth=depth + 1)
            result[key] = nested
        elif value_tok.kind == _T_STRING:
            result[key] = value_tok.value
            pos += 1
        else:
            raise VdfParseError(
                f"key {key!r} at offset {tok.pos} followed by unexpected {value_tok.kind}"
            )

        # Tolerate-and-skip a platform conditional tag right after the
        # value (see docstring above).
        if pos < n and tokens[pos].kind == _T_STRING and _is_conditional_tag(tokens[pos].value):
            pos += 1

    if not top_level:
        raise VdfParseError("unexpected end of input: unclosed block")

    return result, pos


def parse_vdf(text: str) -> KeyValues:
    """Parse a full KeyValues document into a nested dict.

    Top level is itself an implicit object (no enclosing braces), e.g.
    ``"AppState" { ... }`` parses to ``{"AppState": {...}}``.

    A leading UTF-8 BOM (U+FEFF) is stripped before tokenizing: without
    this, the BOM character is neither whitespace nor a brace nor a quote,
    so the unquoted-bareword reader would swallow it together with the
    immediately following quoted key (e.g. turning ``"AppState"`` into one
    garbled bareword token containing the quote characters). Stripping it
    here — in addition to reading files with ``encoding="utf-8-sig"`` in
    ``parse_appmanifest_file``/``parse_libraryfolders_file`` — covers
    both a BOM'd file on disk and a BOM'd string handed to this function
    directly (e.g. a future reporter path, WP 2.2).
    """
    text = text.lstrip("﻿")
    tokens = _tokenize(text)
    result, _pos = _parse_object(tokens, 0, top_level=True, depth=0)
    return result


def _get_ci(obj: KeyValues, key: str) -> "str | KeyValues | None":
    """Case-insensitive key lookup (Valve's own tools are inconsistent
    about casing across KeyValues files in the wild)."""
    if key in obj:
        return obj[key]
    lowered = key.lower()
    for k, v in obj.items():
        if k.lower() == lowered:
            return v
    return None


def _parse_strict_uint(raw: str) -> int | None:
    """Parse ``raw`` as a non-negative integer using a strict grammar, or
    return ``None`` (never raises) if it doesn't match.

    **Accepted grammar:** one or more ASCII digit characters (``0``-``9``)
    and nothing else. Leading zeros are tolerated (``"004"`` -> ``4``).

    Deliberately **stricter** than Python's ``int()``, which additionally
    accepts surrounding whitespace (``" 4 "``), a leading ``+``/``-`` sign
    (``"+4"``), underscore digit-group separators (``"1_0"`` -> ``10``),
    and non-ASCII Unicode digit characters (e.g. Arabic-Indic ``"٤"`` ->
    ``4``) — none of which a real Steam-written ACF/VDF file ever
    contains, and none of which Go's ``strconv.Atoi`` (base 10) accepts
    either, which matters because this Python parser is the executable
    specification a Go port (ADR-0005) must match bit-for-bit. Leading
    zeros are the one thing both ``int()`` and ``strconv.Atoi`` already
    agree on, so that liberality is kept rather than "fixed".

    Mirrors the same strict-digit-string approach already used for depot/
    app ids in ``api/vault_api/deletion.py``'s ``coerce_positive_id``
    (``value.isascii() and value.isdigit()``) — this project's one house
    rule for "does this string look like a database/file integer field",
    kept consistent across ``api/`` and ``agent/``.
    """
    if not raw:
        return None
    if not (raw.isascii() and raw.isdigit()):
        return None
    return int(raw)


# --------------------------------------------------------------------------
# appmanifest_<appid>.acf
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class InstalledApp:
    """One installed app, as reported by an appmanifest_<appid>.acf file."""

    appid: str
    name: str
    state_flags: int
    size_on_disk: int | None
    library_path: Path

    @property
    def installed(self) -> bool:
        """True if the "fully installed" bit is set in StateFlags.

        Other bits (e.g. update-required) may be set alongside it — an app
        mid-update is still installed and playable, just stale.
        """
        return bool(self.state_flags & STATE_FLAG_FULLY_INSTALLED)


def parse_appmanifest(text: str, *, library_path: Path) -> InstalledApp:
    """Parse one appmanifest file's content into an InstalledApp.

    Raises VdfParseError if the file is structurally malformed or missing
    required fields (appid, name, StateFlags), or if ``appid``/
    ``StateFlags`` do not match the strict digit grammar (see
    ``_parse_strict_uint``) — e.g. ``" 480 "`` or ``"notanumber"`` as an
    appid is rejected, not silently coerced. ``SizeOnDisk`` is the one
    field that is tolerated when missing or malformed (see below).
    """
    parsed = parse_vdf(text)
    root = _get_ci(parsed, "AppState")
    if not isinstance(root, dict):
        raise VdfParseError("appmanifest has no top-level 'AppState' block")

    appid_raw = _get_ci(root, "appid")
    name = _get_ci(root, "name")
    state_flags_raw = _get_ci(root, "StateFlags")
    size_raw = _get_ci(root, "SizeOnDisk")

    if not isinstance(appid_raw, str) or not appid_raw:
        raise VdfParseError("appmanifest missing required 'appid'")
    if not isinstance(name, str):
        raise VdfParseError("appmanifest missing required 'name'")
    if not isinstance(state_flags_raw, str):
        raise VdfParseError("appmanifest missing required 'StateFlags'")

    # appid is kept as a str (it's an identifier, not a quantity to do
    # arithmetic on) but still validated against the same strict digit
    # grammar as StateFlags/SizeOnDisk — a poisoned/corrupt appid must not
    # silently pass through as a lookalike value.
    if _parse_strict_uint(appid_raw) is None:
        raise VdfParseError(f"appid {appid_raw!r} is not a valid ASCII digit string")
    appid = appid_raw

    state_flags = _parse_strict_uint(state_flags_raw)
    if state_flags is None:
        raise VdfParseError(f"StateFlags {state_flags_raw!r} is not a valid ASCII digit string")

    size_on_disk: int | None = None
    if isinstance(size_raw, str):
        # Missing/garbled SizeOnDisk is tolerated — not fatal to the rest
        # of the record — so an unparseable value degrades to None rather
        # than raising.
        size_on_disk = _parse_strict_uint(size_raw)

    return InstalledApp(
        appid=appid,
        name=name,
        state_flags=state_flags,
        size_on_disk=size_on_disk,
        library_path=library_path,
    )


def parse_appmanifest_file(path: Path, *, library_path: Path) -> InstalledApp:
    """Read and parse an appmanifest file from disk.

    Raises VdfParseError (wrapping OS/encoding errors too) so callers have
    one exception type to catch when walking many files.
    """
    try:
        # utf-8-sig transparently strips a leading UTF-8 BOM if present
        # (and behaves exactly like plain utf-8 when there is none) — see
        # parse_vdf's docstring for what a BOM does to the tokenizer
        # otherwise.
        text = path.read_text(encoding="utf-8-sig", errors="strict")
    except (OSError, UnicodeDecodeError) as exc:
        raise VdfParseError(f"cannot read {path}: {exc}") from exc
    return parse_appmanifest(text, library_path=library_path)


# --------------------------------------------------------------------------
# libraryfolders.vdf
# --------------------------------------------------------------------------


def parse_libraryfolders(text: str) -> list[Path]:
    """Parse a libraryfolders.vdf document into a list of library root paths.

    Supports both:
    - The modern format (Steam client 2019+): numbered blocks each with a
      "path" key (and an "apps" sub-block we don't need here), e.g.::

          "libraryfolders"
          {
              "0"
              {
                  "path"    "C:\\Steam"
                  "apps" { "480" "1906055" }
              }
          }

    - The older flat format: numbered keys mapping directly to a path
      string, alongside unrelated top-level keys (TimeNextStatsReport,
      ContentStatsID, ...) which are skipped::

          "LibraryFolders"
          {
              "TimeNextStatsReport"  "123"
              "1"                    "D:\\SteamLibrary"
          }

    Non-numeric keys are ignored in both formats (that's how the two are
    told apart from the unrelated bookkeeping keys in the old format).
    Raises VdfParseError if the file has no recognizable root block at all.
    """
    parsed = parse_vdf(text)
    root = _get_ci(parsed, "libraryfolders")
    if not isinstance(root, dict):
        raise VdfParseError("libraryfolders.vdf has no top-level 'libraryfolders' block")

    paths: list[Path] = []
    for key, value in root.items():
        if not key.isdigit():
            continue  # e.g. TimeNextStatsReport, ContentStatsID — not a library entry

        if isinstance(value, dict):
            # Modern format: nested block with a "path" key.
            path_value = _get_ci(value, "path")
            if isinstance(path_value, str) and path_value:
                paths.append(Path(path_value))
            else:
                logger.warning("libraryfolders.vdf entry %r has no usable 'path' key", key)
        elif isinstance(value, str) and value:
            # Old flat format: the value itself is the path.
            paths.append(Path(value))

    return paths


def parse_libraryfolders_file(path: Path) -> list[Path]:
    """Read and parse libraryfolders.vdf from disk.

    Raises VdfParseError (wrapping OS/encoding errors too).
    """
    try:
        # utf-8-sig transparently strips a leading UTF-8 BOM if present
        # (and behaves exactly like plain utf-8 when there is none) — see
        # parse_vdf's docstring for what a BOM does to the tokenizer
        # otherwise.
        text = path.read_text(encoding="utf-8-sig", errors="strict")
    except (OSError, UnicodeDecodeError) as exc:
        raise VdfParseError(f"cannot read {path}: {exc}") from exc
    return parse_libraryfolders(text)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def discover_installed(library_root: Path) -> list[InstalledApp]:
    """Discover all installed apps across every Steam library.

    ``library_root`` is the main Steam install directory (the one that
    contains ``steamapps/libraryfolders.vdf`` — e.g. ``C:\\Steam`` on
    Windows, ``~/.local/share/Steam`` on Linux/SteamOS per ADR-0002). That
    file lists every library folder Steam knows about, including the main
    one itself.

    Tolerant by design, never raises for per-file problems:
    - Missing or corrupt ``libraryfolders.vdf`` falls back to treating
      ``library_root`` as the only library (logged as a warning).
    - Missing or corrupt appmanifest files are skipped (logged as a
      warning).
    - Missing library directories on disk are skipped (logged as a
      warning); a plain directory scan of a nonexistent path simply
      yields no files, so this also degrades gracefully without special-
      casing.
    - Duplicate app IDs across libraries: first one found wins, later
      ones are skipped (logged as a warning).

    Returns the list of InstalledApp in discovery order (never raises for
    the failure modes above — an unreadable library_root itself still
    returns an empty list rather than crashing).
    """
    library_folders_path = library_root / "steamapps" / "libraryfolders.vdf"

    library_paths: list[Path]
    try:
        library_paths = parse_libraryfolders_file(library_folders_path)
        if not library_paths:
            logger.warning(
                "%s parsed but listed no library paths; falling back to %s",
                library_folders_path,
                library_root,
            )
            library_paths = [library_root]
    except VdfParseError as exc:
        logger.warning(
            "could not read/parse %s (%s); falling back to treating %s as the only library",
            library_folders_path,
            exc,
            library_root,
        )
        library_paths = [library_root]

    # De-duplicate while preserving order (libraryfolders.vdf shouldn't
    # list the same path twice, but tolerate it).
    seen_libraries: set[Path] = set()
    ordered_libraries: list[Path] = []
    for lib in library_paths:
        if lib not in seen_libraries:
            seen_libraries.add(lib)
            ordered_libraries.append(lib)

    apps: list[InstalledApp] = []
    seen_appids: dict[str, Path] = {}

    for lib in ordered_libraries:
        steamapps_dir = lib / "steamapps"
        if not steamapps_dir.is_dir():
            logger.warning("library path %s has no steamapps directory, skipping", lib)
            continue

        for manifest_path in sorted(steamapps_dir.glob("appmanifest_*.acf")):
            try:
                app = parse_appmanifest_file(manifest_path, library_path=lib)
            except VdfParseError as exc:
                logger.warning("skipping corrupt manifest %s: %s", manifest_path, exc)
                continue

            if app.appid in seen_appids:
                logger.warning(
                    "duplicate appid %s in library %s, keeping first occurrence from %s",
                    app.appid,
                    lib,
                    seen_appids[app.appid],
                )
                continue

            seen_appids[app.appid] = lib
            apps.append(app)

    return apps
