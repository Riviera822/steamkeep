"""Per-game cache deletion (plan §4 "Deletion", plan §6 ``DELETE /v1/cache/{appid}``).

**This module deletes user data.** Everything in it is therefore built so that
each decision is (a) provable by a unit test without an HTTP request or a
database, and (b) written to the log as an audit trail. The layers:

1. **Path guards** — ``resolve_depot_root`` / ``depot_dir_path``. Pure
   functions over strings that decide *which absolute path may be deleted at
   all*. They exist because the only two inputs to that decision come from
   outside the code: ``VAULT_CACHE_ROOT`` (operator config, may be empty, a
   filesystem root, relative, a UNC path) and ``depot_app_map.depotid``
   (a database column — and SQLite's INTEGER affinity does **not** enforce
   integers, so a poisoned or hand-edited database really can hold
   ``'../../..'`` there). Both are validated before anything is removed.
2. **Link safety** — ``remove_depot_dir``. A depot directory that is itself a
   symlink or a Windows junction is *unlinked*, never traversed: deleting the
   link's target would destroy data outside the cache. See the function's
   docstring for what ``shutil.rmtree`` actually does with a junction, measured
   rather than assumed.
3. **The plan** — ``plan_deletion``. Splits an app's mapped depots into
   *exclusive* (delete), *shared with another tracked app* (never delete, plan
   §4) and *unusable mapping rows* (poisoned data, report). Pure function over
   rows.
4. **Execution** — ``delete_app_depots``. Per-depot ``try``/``except`` so one
   undeletable depot is reported, not turned into a half-reported success, and
   one INFO log line per depot decision.

What deliberately does NOT happen here: the depot→app **mapping rows are
kept**. See ``routers/cache.py`` and api/README.md for that decision and its
consequence.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from vault_api.sizes import is_link_like, walk_file_stats

logger = logging.getLogger(__name__)

#: The one subdirectory of VAULT_CACHE_ROOT deletion may ever touch (plan §4
#: storage layout: ``/cache/depot/<depotid>/...``).
DEPOT_DIRNAME = "depot"


class DeletionGuardError(Exception):
    """A safety guard refused to let a deletion proceed. Nothing was deleted."""


class UnsafeCacheRootError(DeletionGuardError):
    """``VAULT_CACHE_ROOT`` is unusable as a deletion base.

    Empty, resolving to a filesystem root, or holding no ``depot/`` directory.
    """


class UnsafeDepotTargetError(DeletionGuardError):
    """A depot id from the database does not yield a path strictly inside ``depot/``."""


# --------------------------------------------------------------------------
# Path guards (pure functions, unit-tested directly)
# --------------------------------------------------------------------------


def is_filesystem_root(path: str) -> bool:
    """True if ``path`` is the top of a filesystem and has no parent.

    ``os.path.dirname`` is idempotent exactly at a root, which covers all the
    shapes that matter (verified on Windows 11 / CPython 3.12.10):
    ``dirname('/') == '/'``, ``dirname('C:\\\\') == 'C:\\\\'`` and — the one
    that a naive check misses — ``dirname('\\\\\\\\server\\\\share') ==
    '\\\\\\\\server\\\\share'``, so a bare UNC share root is recognised too.
    """
    return os.path.dirname(path) == path


def resolve_depot_root(cache_root: str) -> str:
    """Resolve the one directory deletions may happen inside, or refuse.

    Returns the absolute, symlink-resolved ``<cache_root>/depot`` path. Raises
    ``UnsafeCacheRootError`` — deleting **nothing** — when:

    - ``cache_root`` is empty or whitespace. This is not paranoia: measured,
      ``os.path.abspath('') == os.getcwd()``, so an unset/blank
      ``VAULT_CACHE_ROOT`` would silently aim a recursive delete at whatever
      directory vault-api happens to be running in.
    - ``cache_root`` resolves to a filesystem root (``/``, ``C:\\``, a bare
      UNC share). Note ``os.path.realpath('/')`` is ``'C:\\'`` on Windows —
      a Docker-style config pasted onto a Windows host lands here.
    - the resolved ``depot/`` path is itself a filesystem root (only reachable
      via a link, but this is the guard that would catch it).
    - ``depot/`` does not exist or is not a directory. A cache root without a
      ``depot/`` subdirectory is either misconfigured or has never stored
      anything, and in **both** cases the honest answer is to refuse loudly
      rather than to "successfully delete" nothing.

    ``realpath`` is applied on purpose: pointing ``cache/`` or ``cache/depot``
    at another volume with a symlink/junction is a legitimate homelab setup, so
    the *resolved* directory is what becomes the deletion base — and the
    strict-child check in ``depot_dir_path`` is then made against that resolved
    base, which is what makes the check meaningful.
    """
    raw = (cache_root or "").strip()
    if not raw:
        raise UnsafeCacheRootError(
            "VAULT_CACHE_ROOT is empty or unset. Refusing to delete anything: an "
            "empty path resolves to the current working directory, which is not a "
            "cache. Set VAULT_CACHE_ROOT (see api/.env.example)."
        )

    root = os.path.realpath(os.path.abspath(raw))
    if is_filesystem_root(root):
        raise UnsafeCacheRootError(
            f"VAULT_CACHE_ROOT={cache_root!r} resolves to the filesystem root "
            f"{root!r}. Refusing to delete anything — point VAULT_CACHE_ROOT at "
            "the cache directory itself, not at a drive or filesystem root."
        )

    depot_root = os.path.realpath(os.path.join(root, DEPOT_DIRNAME))
    if is_filesystem_root(depot_root):  # pragma: no cover - needs a link to a root
        raise UnsafeCacheRootError(
            f"{DEPOT_DIRNAME}/ under VAULT_CACHE_ROOT={cache_root!r} resolves to the "
            f"filesystem root {depot_root!r}. Refusing to delete anything."
        )
    if not os.path.isdir(depot_root):
        raise UnsafeCacheRootError(
            f"{depot_root!r} does not exist or is not a directory, so there is no "
            "depot cache to delete from. Refusing to delete anything. Either "
            "VAULT_CACHE_ROOT points at the wrong place, or nothing has ever been "
            "cached on this host."
        )
    return depot_root


def coerce_positive_id(value: object) -> int | None:
    """A depot id / app id as a positive ``int``, or ``None`` if unusable.

    Both ids arrive from ``depot_app_map``, and SQLite's INTEGER *affinity*
    does not enforce the column type — a hand-edited or corrupted database can
    hold a string (or NULL) there. Everything that is not a plain positive
    integer is rejected here rather than reaching path construction; ``bool``
    is excluded explicitly because ``True == 1`` would otherwise sneak through
    as id 1.

    **Strings must be exactly ASCII digits** (``isascii() and isdigit()``), not
    "whatever ``int()`` accepts" (WP 1.6 review): ``int()`` happily parses
    ``" 441 "`` (surrounding whitespace), ``"1_0"`` (underscore separators) and
    non-ASCII digit characters such as Arabic-Indic ``"٤٤١"``. None of those is
    a depot id any part of vault-api ever writes, and on the path that decides
    which directory gets destroyed, exactness beats liberal parsing — a value
    that odd means the row is wrong, and the operator should be told rather
    than have it silently normalised. (``isdigit()`` alone is not enough: it is
    ``True`` for superscripts like ``"²"``, which ``isascii()`` rules out.)
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        if not (value.isascii() and value.isdigit()):
            return None
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def unusable_depotid_message(raw_depotid: object) -> str:
    """The one wording for "this depot id cannot be turned into a path".

    Shared by the path guard and the deletion loop so an operator reading the
    ``failed`` list and an operator reading the log see the same sentence, with
    the raw value quoted — that value is the only clue to which row is broken.
    """
    return (
        f"{raw_depotid!r} is not a usable depot id (expected a positive integer), "
        "so no directory was touched for it. Repair the mapping row with "
        "DELETE /v1/mapping/{depotid}/{appid} or directly in the database."
    )


def depot_dir_path(depot_root: str, depotid: object) -> str:
    """Absolute path of one depot directory, verified strictly inside ``depot_root``.

    The path is built from the **integer** depot id only (``int`` → ``str``),
    so no character from the database can ever reach the filesystem; the
    strict-child check afterwards is the belt to that suspenders. Raises
    ``UnsafeDepotTargetError`` for anything that is not a positive integer or
    that does not land exactly one level below ``depot_root``.

    "Strict child" is checked as *exactly one level below*
    (``dirname(candidate) == depot_root``), not as a prefix match:
    ``startswith`` would happily accept ``…/depot-evil`` and a ``commonpath``
    check would accept arbitrarily deep paths. The link case is deliberately
    NOT resolved here — a depot directory that is a junction must still be
    recognised as a legitimate target so ``remove_depot_dir`` can unlink it,
    and resolving it would instead report the *target* as "outside the cache".
    """
    if not os.path.isabs(depot_root):  # pragma: no cover - defensive
        raise UnsafeDepotTargetError(
            f"depot root {depot_root!r} is not absolute; refusing to build a "
            "deletion target from it (use resolve_depot_root)."
        )

    numeric = coerce_positive_id(depotid)
    if numeric is None:
        raise UnsafeDepotTargetError(unusable_depotid_message(depotid))

    name = str(numeric)
    candidate = os.path.normpath(os.path.join(depot_root, name))
    if os.path.dirname(candidate) != depot_root or os.path.basename(candidate) != name:
        raise UnsafeDepotTargetError(  # pragma: no cover - unreachable via resolve_depot_root
            f"depot {numeric} would resolve to {candidate!r}, which is not a direct "
            f"child of {depot_root!r}. Refusing to delete it."
        )
    return candidate


# --------------------------------------------------------------------------
# Measuring and removing one depot directory
# --------------------------------------------------------------------------


def depot_dir_bytes(path: str) -> int:
    """Bytes this depot directory holds *and that deleting it would free*.

    A link-like depot directory reports ``0``: only the link itself is removed
    (see ``remove_depot_dir``), so its target's bytes are not freed and must
    not be claimed as freed. Uses the same walk as the size cache
    (``sizes.walk_file_stats``), which likewise does not follow links.
    """
    if is_link_like(path):
        return 0
    return sum(stat.st_size for stat in walk_file_stats(path))


def remove_depot_dir(path: str) -> None:
    """Delete one depot directory tree — never following a link out of the cache.

    Measured on Windows 11 / CPython 3.12.10 with a real ``mklink /J`` junction
    pointing at a directory **outside** the cache root (WP 1.6; every claim
    below is an observation, and each is pinned by a test):

    - ``shutil.rmtree(<junction>)`` **raises**
      ``OSError("Cannot call rmtree on a symbolic link")`` and the junction's
      target is untouched. So rmtree does not destroy foreign data here — but
      it does turn a legitimately-linked depot directory into a *failed*
      deletion that leaves the link in place, which is why this function
      handles the link case itself instead of calling rmtree and reporting a
      failure.
    - ``os.rmdir(<junction>)`` removes the junction only; the target directory
      and its contents survive. That is the link path taken below.
    - ``shutil.rmtree(<real directory containing a junction>)`` completes
      normally, removes the junction as a link, and the junction's target
      survives. Nested junctions are therefore *not* followed, so rmtree is
      safe for the ordinary case — pinned by a test so a future CPython
      regression is caught here rather than in a user's data.

    On POSIX, ``os.rmdir`` on a symlink fails with ``ENOTDIR`` (the final
    component of a path is never followed by ``rmdir``), hence the
    ``os.unlink`` fallback; ``rmtree`` refuses symlinks the same way it refuses
    junctions.
    """
    if is_link_like(path):
        try:
            # Windows junction / directory symlink.
            os.rmdir(path)
        except OSError:
            # POSIX symlink (rmdir -> ENOTDIR), or a file symlink on Windows.
            os.unlink(path)
        return
    shutil.rmtree(path)


#: How often ``remove_depot_dir_settling`` re-attempts a removal that failed
#: with an ``OSError``, and how long it pauses between attempts. Both are
#: deliberately tiny (worst case ~80 ms, and only on a path that is failing
#: anyway): the pause exists to let a *racing* deleter finish, not to "wait
#: for" anything (WP 1.6 second-stage review, B1).
CONCURRENT_REMOVAL_ATTEMPTS = 5
CONCURRENT_REMOVAL_PAUSE_SECONDS = 0.02


def remove_depot_dir_settling(path: str) -> tuple[bool, Exception | None]:
    """Remove one depot directory, tolerating a concurrent deleter.

    Returns ``(removed_by_this_call, error)``:

    - ``(True, None)`` — this call removed the directory.
    - ``(False, None)`` — the directory is gone, but somebody else removed it
      (a racing ``DELETE`` of the same app). The caller must attribute **0**
      freed bytes to itself, otherwise two racing requests would each claim the
      same bytes.
    - ``(False, exc)`` — the directory is **still there**: a real failure.

    **Why this is not just a try/except (WP 1.6 second-stage review, B1).** Two
    concurrent ``DELETE``s of the same app both pass the "does it exist" check
    and both start removing the same tree. The loser's ``shutil.rmtree`` then
    walks entries the winner is deleting underneath it and dies with
    ``FileNotFoundError`` part-way through — while the *outer* depot directory
    may still exist for another moment, because the winner has not finished. A
    naive "did it raise?" turns that into a reported failure, and a naive
    "raised but the path is gone?" check samples the filesystem too early and
    still sees the directory. Either way a perfectly clean concurrent deletion
    ends up in ``failed`` and drags the whole request to ``status='error'``
    (measured: the racing-deletes test failed in 12 of 40 isolated runs).

    So the outcome is decided by the **settled** filesystem state, not by the
    exception. The removal is idempotent, so an ``OSError`` simply leads to a
    few more attempts until it succeeds, the path is gone, or the small budget
    runs out — and the final ``lexists`` has the last word, so a path that
    ended up gone is never reported as a failure and a path still on disk is
    never reported as deleted.

    Retrying **every** ``OSError`` rather than only ``FileNotFoundError`` is
    also measured, not defensive padding: a racing deletion on Windows surfaces
    as ``PermissionError [WinError 5] Access denied`` just as often as it does
    as ``FileNotFoundError``, because a file that has been deleted while a
    handle is still open enters "delete pending" state and any further open of
    it reports access-denied rather than not-found. Observed in this suite —
    the first version of this function only retried ``FileNotFoundError`` and
    the racing-deletes test still failed 1 in ~40 runs on that exact error.

    A genuine, non-racing failure (an operator's file handle open on a chunk,
    a permissions problem) costs at most
    ``CONCURRENT_REMOVAL_ATTEMPTS * CONCURRENT_REMOVAL_PAUSE_SECONDS`` extra and
    is then reported as a failure exactly as before — a lock that is really held
    does not disappear in 80 ms. Non-``OSError`` exceptions are never retried.
    """
    last_error: Exception | None = None
    for attempt in range(CONCURRENT_REMOVAL_ATTEMPTS):
        try:
            remove_depot_dir(path)
            return True, None
        except OSError as exc:
            last_error = exc
            if not os.path.lexists(path):
                return False, None
            if attempt + 1 < CONCURRENT_REMOVAL_ATTEMPTS:
                time.sleep(CONCURRENT_REMOVAL_PAUSE_SECONDS)
        except Exception as exc:  # noqa: BLE001 - any failure is data, not a 500
            last_error = exc
            break

    if not os.path.lexists(path):
        return False, None
    return False, last_error


# --------------------------------------------------------------------------
# The plan: exclusive vs shared (plan §4 shared-depot protection)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SharedDepot:
    """A depot that is NOT deleted because another tracked app maps it too."""

    depotid: int
    #: The other app ids that map this depot (plan §4: "2 depots shared with
    #: game Y, not deleted" — the ids are what makes that report actionable).
    shared_with: list[int]


@dataclass(frozen=True)
class DeletedDepot:
    depotid: int
    size_bytes_freed: int


@dataclass(frozen=True)
class FailedDepot:
    """A depot that could not be deleted. ``depotid`` is ``0`` when the mapping
    row's depot id itself was unusable (poisoned database) and therefore cannot
    be named — the reason string carries the raw value."""

    depotid: int
    error: str


@dataclass(frozen=True)
class DeletionPlan:
    """What deletion *would* do, decided before anything is touched."""

    #: Mapped only to this app -> may be deleted (sorted).
    exclusive: list[int]
    #: Mapped to at least one other tracked app -> kept (sorted, plan §4).
    shared: list[SharedDepot]
    #: Mapping rows whose depot id is unusable -> nothing deleted, reported.
    unusable: list[FailedDepot]


def plan_deletion(rows: Iterable[Sequence[object]], appid: int) -> DeletionPlan:
    """Split ``appid``'s mapped depots into exclusive / shared / unusable.

    ``rows`` are ``(depotid, appid)`` pairs covering **every** app that maps
    any depot of this app (see ``load_mapping_rows``) — that is what makes the
    shared decision possible from a single query. Pure function: no database,
    no filesystem, so every branch below is directly unit-testable.

    Conservative in both unusual directions: a depot whose co-owner row has an
    unusable *app* id counts as **shared** (something else references it —
    refusing to delete is the safe reading), and a row with an unusable *depot*
    id is reported rather than skipped silently.
    """
    own: set[int] = set()
    others: dict[int, set[int]] = {}
    unreadable_owner: set[int] = set()
    unusable: list[FailedDepot] = []

    for row in rows:
        raw_depotid, raw_owner = row[0], row[1]
        depotid = coerce_positive_id(raw_depotid)
        if depotid is None:
            unusable.append(
                FailedDepot(depotid=0, error=unusable_depotid_message(raw_depotid))
            )
            continue

        owner = coerce_positive_id(raw_owner)
        if owner is None:
            unreadable_owner.add(depotid)
            continue
        if owner == appid:
            own.add(depotid)
        else:
            others.setdefault(depotid, set()).add(owner)

    exclusive = sorted(
        depotid
        for depotid in own
        if depotid not in others and depotid not in unreadable_owner
    )
    shared = [
        SharedDepot(depotid=depotid, shared_with=sorted(others.get(depotid, ())))
        for depotid in sorted(own & (set(others) | unreadable_owner))
    ]
    return DeletionPlan(exclusive=exclusive, shared=shared, unusable=unusable)


# --------------------------------------------------------------------------
# Execution (audit-logged, per-depot failure isolation)
# --------------------------------------------------------------------------


def delete_app_depots(
    depot_root: str,
    appid: int,
    depotids: Sequence[int],
    *,
    co_owners: "Callable[[int], list[int]]",
) -> tuple[list[DeletedDepot], list[FailedDepot], list[SharedDepot]]:
    """Delete the given depot directories under ``depot_root``. Never raises.

    Returns ``(deleted, failed, late_shared)``.

    One ``try``/``except`` **per depot**: an undeletable depot (open file
    handle, permissions, a junction whose removal is refused) is reported in
    the returned failures while the remaining depots are still deleted. A
    half-reported success — "deleted" for something still on disk — is the one
    outcome this must never produce.

    **The shared-depot recheck (WP 1.6 review, TOCTOU fix).** ``plan_deletion``
    decides exclusivity from a snapshot read at the start of the request, and no
    lock is held across the filesystem work (deliberately — see
    ``routers/cache.py``). A ``PUT /v1/mapping/{depotid}`` landing in that window
    can make a depot shared *after* it was planned for deletion, and deleting it
    then destroys another game's content — exactly the guarantee plan §4 makes.
    So immediately before removing each depot, ``co_owners(depotid)`` re-reads
    that one depot's other owners (one indexed lookup on
    ``depot_app_map(depotid, …)``, no transaction); a depot that became shared
    is moved to ``late_shared`` and left alone. The window is not closed by
    magic — it is narrowed to the microseconds between the recheck and
    ``remove_depot_dir``, which is the best a lock-free design can do, and the
    remaining race is the same one a mapping written *during* an ``rmtree``
    would lose anyway.

    If the recheck itself fails (a database error), the depot is **not** deleted
    and is reported as failed: "unknown ownership" must never resolve to
    "delete it".

    ``co_owners`` is a **required keyword argument** with no default (WP 1.6
    second-stage review): a future caller — Phase 3's garbage collection is the
    obvious one — must not be able to silently opt out of the shared-depot
    recheck by forgetting an argument. Skipping the check has to be spelled out
    at the call site (``co_owners=lambda _depotid: []``), which is a thing a
    reviewer can see.

    Every decision is logged at INFO (failures at ERROR) with the
    ``cache-delete`` prefix: that log is the audit trail for an operation that
    destroys user data, and it records the *resolved absolute path* that was
    removed, not just the depot id.

    Honest limitation on the reported byte counts: ``shutil.rmtree`` may remove
    part of a tree before failing, and that depot then contributes ``0`` to the
    freed total. A depot removed by a *concurrent* deletion also contributes
    ``0`` here (see ``remove_depot_dir_settling``), so two racing requests never
    both claim the same bytes. ``total_bytes_freed`` is therefore a floor, never
    an overstatement.
    """
    deleted: list[DeletedDepot] = []
    failed: list[FailedDepot] = []
    late_shared: list[SharedDepot] = []

    for raw_depotid in depotids:
        # Coerced first so `depotid` is a known-good int for the rest of the
        # loop (and for the report) without an `assert` that -O would strip.
        depotid = coerce_positive_id(raw_depotid)
        if depotid is None:
            message = unusable_depotid_message(raw_depotid)
            logger.error(
                "cache-delete appid=%s depot=%r REFUSED by the path guard: %s",
                appid, raw_depotid, message,
            )
            failed.append(FailedDepot(depotid=0, error=message))
            continue

        try:
            path = depot_dir_path(depot_root, depotid)
        except UnsafeDepotTargetError as exc:  # pragma: no cover - see the guard
            logger.error(
                "cache-delete appid=%s depot=%s REFUSED by the path guard: %s",
                appid, depotid, exc,
            )
            failed.append(FailedDepot(depotid=depotid, error=str(exc)))
            continue

        # Last look before destroying anything: did this depot become shared
        # since the plan was made? (TOCTOU fix, see the docstring.)
        if co_owners is not None:
            try:
                owners = co_owners(depotid)
            except Exception as exc:  # noqa: BLE001 - unknown ownership != delete
                logger.error(
                    "cache-delete appid=%s depot=%s NOT DELETED: could not re-check "
                    "its owners before removal: %s: %s",
                    appid, depotid, type(exc).__name__, exc,
                )
                failed.append(
                    FailedDepot(
                        depotid=depotid,
                        error=(
                            "could not re-check whether this depot is shared with "
                            f"another app before deleting it ({type(exc).__name__}: "
                            f"{exc}), so it was left alone."
                        ),
                    )
                )
                continue
            if owners:
                logger.info(
                    "cache-delete appid=%s depot=%s KEPT (late recheck): became "
                    "shared with app(s) %s after the deletion was planned",
                    appid, depotid, owners,
                )
                late_shared.append(
                    SharedDepot(depotid=depotid, shared_with=sorted(owners))
                )
                continue

        # lexists, not exists: a broken link must be removed, not declared absent.
        if not os.path.lexists(path):
            logger.info(
                "cache-delete appid=%s depot=%s ALREADY-ABSENT path=%s "
                "(nothing on disk; mapping row kept)",
                appid, depotid, path,
            )
            deleted.append(DeletedDepot(depotid=depotid, size_bytes_freed=0))
            continue

        linked = is_link_like(path)
        size_bytes = depot_dir_bytes(path)
        removed_here, error = remove_depot_dir_settling(path)

        if error is not None:
            logger.error(
                "cache-delete appid=%s depot=%s FAILED path=%s: %s: %s",
                appid, depotid, path, type(error).__name__, error,
            )
            failed.append(
                FailedDepot(depotid=depotid, error=f"{type(error).__name__}: {error}")
            )
            continue

        if not removed_here:
            logger.info(
                "cache-delete appid=%s depot=%s ALREADY-ABSENT path=%s (a "
                "concurrent deletion removed it; 0 bytes attributed to this "
                "request so the freed total is not counted twice)",
                appid, depotid, path,
            )
            deleted.append(DeletedDepot(depotid=depotid, size_bytes_freed=0))
            continue

        logger.info(
            "cache-delete appid=%s depot=%s DELETED path=%s bytes_freed=%d link=%s",
            appid, depotid, path, size_bytes, linked,
        )
        deleted.append(DeletedDepot(depotid=depotid, size_bytes_freed=size_bytes))

    return deleted, failed, late_shared


def log_kept_depots(appid: int, shared: Sequence[SharedDepot]) -> None:
    """Audit-log every depot that was deliberately NOT deleted (plan §4)."""
    for depot in shared:
        logger.info(
            "cache-delete appid=%s depot=%s KEPT: shared with app(s) %s",
            appid, depot.depotid, depot.shared_with or "(unreadable mapping row)",
        )


# --------------------------------------------------------------------------
# The database statements deletion needs
# --------------------------------------------------------------------------


def load_co_owners(conn: sqlite3.Connection, depotid: int, appid: int) -> list[int]:
    """Apps *other than* ``appid`` that map ``depotid`` — read right now.

    The execute-time half of the shared-depot protection (WP 1.6 review): one
    lookup per depot, immediately before that depot is removed, so a mapping
    written after ``plan_deletion`` ran still protects the depot. Served by
    ``depot_app_map``'s primary key, which is ``(depotid, appid)`` — i.e. this
    is an index seek, not a scan, and it is the reason a per-depot recheck is
    affordable at all.

    Unusable co-owner ids (poisoned rows) are reported as owner ``0`` rather
    than dropped: the caller must treat "somebody unreadable owns this" as
    shared, not as free to delete.
    """
    owners: list[int] = []
    for row in conn.execute(
        "SELECT appid FROM depot_app_map WHERE depotid = ? AND appid != ?",
        (depotid, appid),
    ).fetchall():
        owner = coerce_positive_id(row["appid"])
        owners.append(owner if owner is not None else 0)
    return sorted(set(owners))


def load_mapping_rows(conn: sqlite3.Connection, appid: int) -> list[tuple[object, object]]:
    """``(depotid, appid)`` for every app that maps any depot of ``appid``.

    One query answers both questions deletion has ("which depots does this app
    map" and "does anything else map them"), and the subquery avoids building
    SQL with a dynamic ``IN (?,?,?)`` list. Values are returned **raw**, not
    coerced: ``plan_deletion`` decides what is usable, so a poisoned row cannot
    make this function raise.

    An empty result means the app maps no depots at all, which the endpoint
    turns into a 404.
    """
    return [
        (row["depotid"], row["appid"])
        for row in conn.execute(
            """
            SELECT depotid, appid FROM depot_app_map
            WHERE depotid IN (SELECT depotid FROM depot_app_map WHERE appid = ?)
            """,
            (appid,),
        ).fetchall()
    ]


def reset_app_after_deletion(
    conn: sqlite3.Connection, appid: int, status: str, *, set_needs_force: bool
) -> None:
    """Plan §4: "reset status to 'idle'" — and clear ``last_prefill_at`` with it.

    The two belong together, and clearing the timestamp is **unconditional**
    (WP 1.6 review): leaving a value that claims "prefilled at 14:02" on an app
    whose depots were just removed — wholly or partly — would make the app's
    badge logic (plan §4) and Phase 3's staleness comparison read from a fact
    that is no longer true. ``status`` is the caller's call: ``'idle'`` after a
    clean deletion, ``'error'`` when any depot failed (see ``routers/cache.py``
    for why a partial failure is not a green badge).

    The depot→app **mapping rows are deliberately kept** — see
    ``routers/cache.py``.

    ``set_needs_force`` (schema v5, WP 3.4, ADR-0006 decision 2) is a
    **required keyword argument, no default** — same rule as ``co_owners`` on
    ``delete_app_depots`` above: a future caller must not be able to silently
    skip deciding this, it has to be spelled out at the call site. The caller
    (``routers/cache.py``) passes ``True`` exactly when this request actually
    changed or left uncertain what is on disk for this app — at least one
    depot landed in ``deleted`` (which includes the ALREADY-ABSENT case: the
    mapping said this depot was here, and it demonstrably wasn't, which is
    itself new information) or at least one depot landed in ``failed``
    (cache state unknown after a partial failure ⇒ the next fill must not
    trust SteamPrefill's own stale bookkeeping). ``False`` for the "nothing
    exclusive to delete" case (every mapped depot turned out to be shared) —
    nothing on disk changed for this app, so its ``needs_force`` is left
    exactly as it was.
    """
    if set_needs_force:
        conn.execute(
            "UPDATE apps SET status = ?, last_prefill_at = NULL, needs_force = 1 "
            "WHERE appid = ?",
            (status, appid),
        )
    else:
        conn.execute(
            "UPDATE apps SET status = ?, last_prefill_at = NULL WHERE appid = ?",
            (status, appid),
        )
    conn.commit()
