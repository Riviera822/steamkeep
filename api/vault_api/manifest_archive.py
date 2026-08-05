"""Durable archive for SteamPrefill's manifest ``.bin`` files (WP 3.2).

ADR-0006 decision 3 / research doc risk 4: SteamPrefill's own temp cache
(``$HOME/.cache/SteamPrefill/v1/``) does **not** survive its own
``clear-temp`` command, so the only durable record of a manifest vault-api
ever ingested is a copy it makes itself. This module owns exactly that copy
— it never touches ``depot_manifests`` (that's ``vault_api.depot_manifests``)
and never parses anything (that's ``vault_api.manifests``); it is pure file
I/O, kept small and separately testable (plan §9).

Layout: one flat directory (``VAULT_MANIFEST_ARCHIVE_DIR``), one file per
depot+manifest: ``{depotid}_{manifestid}.bin``. A depot's *previous* archived
manifests are kept up to ``VAULT_MANIFEST_KEEP`` (default 3, **total** count
per depot including the current one — see ``config.py``'s
``DEFAULT_MANIFEST_KEEP`` docstring for why this isn't "N previous *plus*
current").
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile

logger = logging.getLogger(__name__)

#: ``{depotid}_{manifestid}.bin`` — deliberately NOT the SteamPrefill
#: temp-cache filename contract (``{originalAppId}_{containingAppId}_
#: {depotId}_{manifestId}.bin``): the archive's whole reason to exist is to
#: outlive any one app's attribution of a manifest, keyed on what GC
#: (ADR-0007) will actually look up — depot + manifest.
ARCHIVE_FILENAME_TEMPLATE = "{depotid}_{manifestid}.bin"


def archive_filename(*, depotid: int, manifestid: str) -> str:
    """The archive's own filename for one (depotid, manifestid) pair."""
    return ARCHIVE_FILENAME_TEMPLATE.format(depotid=depotid, manifestid=manifestid)


def archive_manifest(archive_dir: str, *, depotid: int, manifestid: str, src_path: str) -> str:
    """Copy ``src_path`` into ``archive_dir`` as ``{depotid}_{manifestid}.bin``.

    **Atomic (same pattern as ``vault_api.prefill.write_selected_apps``):**
    the copy is written to a tempfile in the SAME directory as the final
    name, then moved into place with ``os.replace`` — a same-directory
    tempfile keeps the replace a same-filesystem rename (atomic on both
    POSIX and Windows), so nothing that reads the archive (a future GC pass,
    an operator) can ever observe a partially-written file, and a re-archive
    of the same (depotid, manifestid) — e.g. a repeated prefill of an
    already-current app — safely overwrites rather than corrupting it.

    **Copy, not move.** The source is SteamPrefill's own temp cache, which
    vault-api does not own — deleting it out from under a concurrent
    SteamPrefill invocation (or just being wrong about ownership) is a risk
    this function has no reason to take; disk space for one ``.bin`` file
    (observed up to ~3.4 MB, ``manifests.py``'s docstring) is cheap.

    Returns the archived file's absolute path. Raises ``OSError`` for any
    filesystem failure (missing/unreadable source, unwritable archive
    directory) — callers (``vault_api.manifest_ingest``) treat one file's
    archive failure as a per-file event, same as a parse failure, never as a
    reason to fail the whole ingestion pass or the job.
    """
    os.makedirs(archive_dir, exist_ok=True)
    dest_name = archive_filename(depotid=depotid, manifestid=manifestid)
    dest_path = os.path.join(archive_dir, dest_name)

    fd, tmp_path = tempfile.mkstemp(dir=archive_dir, prefix=f".{dest_name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as tmp_handle, open(src_path, "rb") as src_handle:
            shutil.copyfileobj(src_handle, tmp_handle)
            tmp_handle.flush()
            os.fsync(tmp_handle.fileno())
        os.replace(tmp_path, dest_path)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:  # pragma: no cover - best effort cleanup
            pass
        raise
    return dest_path


def prune_archive(archive_dir: str, *, depotid: int, keep: int) -> list[str]:
    """Delete archived manifests for ``depotid`` beyond the newest ``keep``.

    "Newest" is decided by the archived file's own mtime (set the moment
    THIS module wrote it, i.e. ingestion order) — not by parsing or sorting
    ``manifestid`` values, which are opaque strings that may not even be
    numeric-comparable across all sources (schema v3 stores ``manifestid``
    as TEXT for exactly that reason, see ``db.py``).

    Matching is by filename prefix (``"{depotid}_"``) — safe against a
    shorter depot id being a false prefix of a longer one (e.g. depot ``44``
    matching a stored ``441_...bin``) because the delimiter is the literal
    underscore immediately after the digits: ``"44_"`` is not a prefix of
    ``"441_123.bin"``.

    Never raises: a missing/unreadable archive directory or an unremovable
    stale file is logged and otherwise ignored — pruning is best-effort
    housekeeping, not something that should fail an ingestion pass over it.
    Returns the names removed (for tests/logging).
    """
    prefix = f"{depotid}_"
    try:
        entries = [
            entry
            for entry in os.scandir(archive_dir)
            if entry.is_file() and entry.name.startswith(prefix) and entry.name.endswith(".bin")
        ]
    except OSError as exc:
        logger.warning("manifest-archive: could not list %s for pruning: %s", archive_dir, exc)
        return []

    entries.sort(key=lambda entry: entry.stat().st_mtime_ns, reverse=True)
    removed: list[str] = []
    for stale in entries[keep:]:
        try:
            os.unlink(stale.path)
            removed.append(stale.name)
        except OSError as exc:
            logger.warning("manifest-archive: could not prune %s: %s", stale.path, exc)
    return removed
