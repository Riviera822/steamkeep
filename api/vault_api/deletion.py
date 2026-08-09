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
   *exclusive* (delete), *shared and protected* (kept — at least one co-owner
   currently has cache content, plan §4), *shared but a last cached remnant*
   (deleted anyway — ADR-0003's addendum, see below) and *unusable mapping
   rows* (poisoned data, report). Pure function over rows plus co-owner state
   data; no database, no filesystem.
4. **Execution** — ``delete_app_depots``. Per-depot ``try``/``except`` so one
   undeletable depot is reported, not turned into a half-reported success, and
   one INFO log line per depot decision.

**A shared depot is not "never delete" (ADR-0003 addendum, 2026-08-06).** The
original plan §4 rule protected any depot mapped to more than one tracked app,
judged purely from ``depot_app_map`` rows — which survive deletion by design
(ADR-0003's main decision). If two apps share a depot and both get deleted one
after the other, that rule kept the depot "shared with the other" *both*
times, leaking its bytes forever: neither app ever reports it cached again,
and nothing ever reclaims the space. The fix: a shared depot may be deleted
when **no co-owning app currently has cache content** (conservatively judged —
see ``plan_deletion`` and ``delete_app_depots`` below), reported distinctly as
a "last cached remnant" rather than merged with ordinary exclusive deletions.
Mapping rows are still kept either way — that part of ADR-0003 is unchanged.

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
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Sequence

from vault_api import jobs
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


def safe_child_path(parent: str, name: str) -> str:
    """``<parent>/<name>``, verified to be exactly one level below ``parent``.

    The **file-level twin** of ``depot_dir_path``'s strict-child check, added
    in WP 3.8 because garbage collection deletes individual *files*
    (``depot/<id>/chunk/<chunkid>``, ``depot/<id>/manifest/<mid>/5/<code>``)
    rather than whole depot directories, and a second, less careful path
    builder for that is exactly what this module exists to prevent.

    ``depot_dir_path`` above is deliberately left as it is rather than being
    re-expressed in terms of this function: it carries its own id coercion and
    its own error wording, both covered by WP 1.6's reviewed tests, and
    re-plumbing reviewed deletion-path code to save four lines is a bad trade.
    The checks are identical in substance and both raise
    ``UnsafeDepotTargetError``, so a caller cannot be confused about what a
    refusal means.

    "Strict child" is checked as *exactly one level below*
    (``dirname(candidate) == parent`` and ``basename(candidate) == name``), not
    as a prefix match: ``startswith`` would accept ``…/chunk-evil`` and a
    ``commonpath`` check would accept arbitrarily deep paths. ``name`` is
    additionally required to be a bare path component up front, so ``".."``,
    ``"a/b"``, ``"a\\b"`` and an absolute path are refused by name rather than
    only by where they happen to land.

    **The two checks are redundant, and that is stated rather than implied**
    (measured on Windows 11 / CPython 3.12.10 for ``".."``, ``"."``, ``""``,
    ``"a/b"``, ``"a\\b"``, ``"441\\"``, ``"/etc/passwd"``, ``"C:x"`` and
    ``"a:b"``: every one of them fails *both*). Two of those are worth naming,
    because they are the cases where ``os.path.join`` does something
    surprising rather than something obviously wrong: ``join(parent, "C:x")``
    silently drops the drive and yields ``parent\\x``, and
    ``join(parent, "a:b")`` returns ``"a:b"`` — the parent is discarded
    entirely, because ``ntpath`` reads ``"a:"`` as a drive. Both are then
    caught by the ``basename(candidate) != name`` arm below. The up-front
    check is kept anyway: it costs nothing, it says what a caller is expected
    to pass, and it produces the error message that names the actual problem
    instead of one about a path that no longer resembles the input.

    Links are deliberately NOT resolved here — same reason as
    ``depot_dir_path``: resolving would report a legitimately-linked cache as
    "outside itself". Refusing to *follow* a link is the removal helper's job
    (``remove_file_settling``'s caller checks ``is_link_like`` first).
    """
    if not os.path.isabs(parent):  # pragma: no cover - defensive
        raise UnsafeDepotTargetError(
            f"{parent!r} is not an absolute path; refusing to build a deletion "
            "target from it."
        )
    if not name or name in (".", "..") or os.path.basename(name) != name:
        raise UnsafeDepotTargetError(
            f"{name!r} is not a plain filename (it contains a path separator, or is "
            "a relative marker), so it cannot be a direct child of "
            f"{parent!r}. Refusing to delete anything for it."
        )

    candidate = os.path.normpath(os.path.join(parent, name))
    if os.path.dirname(candidate) != parent or os.path.basename(candidate) != name:
        raise UnsafeDepotTargetError(
            f"{name!r} under {parent!r} would resolve to {candidate!r}, which is not "
            "a direct child of it. Refusing to delete it."
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


def remove_file_settling(path: str) -> tuple[bool, Exception | None]:
    """Unlink ONE file, tolerating a concurrent deleter. Never recurses.

    The single-file counterpart of ``remove_depot_dir_settling`` above, added
    for WP 3.8's garbage collection, which removes orphaned chunk files and
    redundant stored-manifest copies one at a time. Same return contract, and
    it must be read the same way:

    - ``(True, None)`` — this call removed the file, so its bytes may be
      attributed to this run.
    - ``(False, None)`` — the file is **gone**, but this call did not remove
      it: it was already absent, or a concurrent actor
      (``DELETE /v1/cache/{appid}`` running an ``rmtree`` over the same depot,
      another GC job, an operator) removed it underneath us. The caller must
      attribute **0** bytes to itself, otherwise two racing runs would both
      claim the same reclaimed space.
    - ``(False, exc)`` — the file is **still there**: a real failure (an open
      handle, a permissions problem). Nothing was freed.

    The three WP 1.6 lessons this inherits, each of which cost a review round
    there and applies verbatim to a single ``unlink`` (docs/LEARNINGS.md):

    1. **The settled filesystem state decides, not the exception.** A racing
       removal makes ``os.unlink`` raise even though the outcome is perfectly
       clean, so every ``OSError`` is followed by an ``os.path.lexists`` check
       and, while the tiny budget lasts, another attempt (unlink is
       idempotent). ``lexists`` — not ``exists`` — has the last word, so a
       dangling symlink counts as present and a removed file is never reported
       as a failure.
    2. **Every ``OSError`` is retried, not just ``FileNotFoundError``.** On
       Windows a file deleted while a handle is still open enters "delete
       pending" state, and any further access reports ``PermissionError
       [WinError 5] Access denied`` rather than not-found — measured in WP
       1.6, where retrying only ``FileNotFoundError`` still flaked roughly 1
       run in 40. Note delete-pending also makes ``lexists`` answer ``False``
       (``os.lstat`` raises ``PermissionError``, which ``lexists`` swallows),
       which is the correct reading here: the name is on its way out and
       nothing this caller does will bring it back.
    3. **A genuine lock is not a race.** A real failure costs at most
       ``CONCURRENT_REMOVAL_ATTEMPTS * CONCURRENT_REMOVAL_PAUSE_SECONDS``
       (~80 ms) extra and is then reported as a failure — a handle that is
       really held does not disappear in 80 ms. Non-``OSError`` exceptions are
       never retried.

    **This function does not check for links** — its caller must, *before*
    calling, and refuse rather than delete (see ``vault_api.gc_execute``). It
    is kept out of here on purpose: ``os.unlink`` on a symlink removes the
    link and not its target, so unlinking a link would be *safe* but *wrong* —
    GC plans the deletion of a chunk file, and a name that has become a link
    is no longer that file. The decision "this is not what I planned to
    delete" belongs to the planner's consumer, not to the syscall wrapper.
    """
    last_error: Exception | None = None
    for attempt in range(CONCURRENT_REMOVAL_ATTEMPTS):
        try:
            os.unlink(path)
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
    """A depot that is NOT deleted because a co-owner currently has cache
    content (plan §4; ADR-0003 addendum's "protected" outcome)."""

    depotid: int
    #: The other app ids that map this depot (plan §4: "2 depots shared with
    #: game Y, not deleted" — the ids are what makes that report actionable).
    shared_with: list[int]


@dataclass(frozen=True)
class RemnantDepot:
    """A shared depot classified as deletable: EVERY co-owner is verifiably
    idle, never-prefilled and job-free (ADR-0003 addendum) at plan time.

    Still subject to the execute-time recheck like any other candidate — see
    ``delete_app_depots`` — so this is a *plan*, not a guarantee.
    """

    depotid: int
    #: The co-owner app ids, all uncached at plan time (sorted). Carried
    #: through so a caller can report/flag them even before the recheck runs
    #: again with fresh data.
    shared_with_uncached: list[int]


@dataclass(frozen=True)
class CoOwner:
    """One other app mapping a depot, read fresh immediately before removal
    (the execute-time half of ADR-0003's addendum, see ``load_co_owners``).

    ``appid`` is ``0`` for an unusable (poisoned) mapping row — same
    convention the pre-addendum code used for an unreadable owner — and such
    a row always carries ``has_content=True``: unreadable ownership must never
    resolve to "no content therefore deletable".
    """

    appid: int
    has_content: bool


@dataclass(frozen=True)
class DeletedDepot:
    depotid: int
    size_bytes_freed: int
    #: Co-owner app ids this depot was a last cached remnant for — non-empty
    #: only for a remnant deletion (ADR-0003 addendum); empty for an ordinary
    #: exclusive deletion. Every id here needs ``apps.needs_force = 1`` (see
    #: ``set_needs_force_for_remnant_co_owners``).
    shared_with_uncached: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class FailedDepot:
    """A depot that could not be deleted. ``depotid`` is ``0`` when the mapping
    row's depot id itself was unusable (poisoned database) and therefore cannot
    be named — the reason string carries the raw value."""

    depotid: int
    error: str
    #: Same meaning as ``DeletedDepot.shared_with_uncached`` — a remnant depot
    #: whose removal FAILED still leaves its co-owners' on-disk assumption
    #: uncertain (the tree may be half-gone), so they still need
    #: ``needs_force = 1``. Empty for every failure that isn't a remnant
    #: deletion attempt (path-guard refusals, a failed recheck — "unknown
    #: ownership" never resolves to "these are the co-owners to flag").
    shared_with_uncached: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class DeletionPlan:
    """What deletion *would* do, decided before anything is touched."""

    #: Mapped only to this app -> may be deleted (sorted).
    exclusive: list[int]
    #: Shared, but every co-owner is uncached -> deletable last remnant
    #: (sorted by depotid; ADR-0003 addendum).
    remnant: list[RemnantDepot]
    #: Shared with at least one co-owner that currently has content -> kept
    #: (sorted, plan §4).
    shared: list[SharedDepot]
    #: Mapping rows whose depot id is unusable -> nothing deleted, reported.
    unusable: list[FailedDepot]


def plan_deletion(
    rows: Iterable[Sequence[object]],
    appid: int,
    co_owner_states: Mapping[int, bool] | None = None,
) -> DeletionPlan:
    """Split ``appid``'s mapped depots into exclusive / remnant / shared / unusable.

    ``rows`` are ``(depotid, appid)`` pairs covering **every** app that maps
    any depot of this app (see ``load_mapping_rows``) — that is what makes the
    shared decision possible from a single query. ``co_owner_states`` is
    ``{appid: has_content}`` for the co-owners worth asking about — built by
    ``load_co_owner_states`` alongside ``load_mapping_rows`` precisely so this
    function stays pure (no database, no filesystem): every branch below is
    directly unit-testable with plain dicts and tuples.

    Conservative in every unusual direction (ADR-0003 addendum, "unknown ⇒
    protected"):

    - a depot whose co-owner row has an unusable *app* id counts as
      **shared** (protected) regardless of ``co_owner_states`` — something
      else references it, and the reference is unreadable;
    - a co-owner appid **absent** from ``co_owner_states`` (not looked up, or
      looked up and found to have no ``apps`` row at all) defaults to
      ``has_content=True`` — protected. This is also what makes an omitted
      ``co_owner_states`` argument (``None``, the default) reproduce the
      pre-addendum "any other owner protects" behavior exactly, with no
      special-casing here: every lookup simply misses.
    - a row with an unusable *depot* id is reported rather than skipped
      silently (unchanged from before the addendum).

    Only when a depot has at least one readable co-owner and **every one** of
    them maps to ``has_content=False`` is it classified as ``remnant``
    (deletable last-remnant) rather than ``shared`` (protected).
    """
    states: Mapping[int, bool] = co_owner_states or {}
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

    shared: list[SharedDepot] = []
    remnant: list[RemnantDepot] = []
    for depotid in sorted(own & (set(others) | unreadable_owner)):
        if depotid in unreadable_owner:
            # An unreadable co-owner row protects unconditionally — it is
            # never eligible to become a remnant, no matter what the readable
            # others' states say.
            shared.append(
                SharedDepot(depotid=depotid, shared_with=sorted(others.get(depotid, ())))
            )
            continue

        owner_ids = sorted(others[depotid])
        if all(not states.get(owner, True) for owner in owner_ids):
            remnant.append(RemnantDepot(depotid=depotid, shared_with_uncached=owner_ids))
        else:
            shared.append(SharedDepot(depotid=depotid, shared_with=owner_ids))

    return DeletionPlan(exclusive=exclusive, remnant=remnant, shared=shared, unusable=unusable)


# --------------------------------------------------------------------------
# Execution (audit-logged, per-depot failure isolation)
# --------------------------------------------------------------------------


def delete_app_depots(
    depot_root: str,
    appid: int,
    depotids: Sequence[int],
    *,
    co_owners: "Callable[[int], list[CoOwner]]",
) -> tuple[list[DeletedDepot], list[FailedDepot], list[SharedDepot]]:
    """Delete the given depot directories under ``depot_root``. Never raises.

    ``depotids`` is the FULL set of candidates from the plan — both
    ``DeletionPlan.exclusive`` and the depot ids of ``DeletionPlan.remnant``
    (the caller, ``routers/cache.py``, concatenates them). Every one of them
    gets the identical treatment below: this function does not trust the
    plan's classification, it re-derives the outcome from fresh data.

    Returns ``(deleted, failed, late_shared)``.

    One ``try``/``except`` **per depot**: an undeletable depot (open file
    handle, permissions, a junction whose removal is refused) is reported in
    the returned failures while the remaining depots are still deleted. A
    half-reported success — "deleted" for something still on disk — is the one
    outcome this must never produce.

    **The shared-depot recheck (WP 1.6 review, TOCTOU fix; extended for the
    ADR-0003 addendum).** ``plan_deletion`` decides exclusivity/remnant status
    from a snapshot read at the start of the request, and no lock is held
    across the filesystem work (deliberately — see ``routers/cache.py``). A
    ``PUT /v1/mapping/{depotid}`` landing in that window can make a depot
    shared after it was planned for deletion; a co-owner's job being enqueued,
    or a co-owner's prefill finishing, can turn a planned *remnant* deletion
    into content destruction for another game — exactly the guarantee plan §4
    (and its addendum) makes. So immediately before removing each depot,
    ``co_owners(depotid)`` re-reads that one depot's other owners **and**
    each one's current content state (one joined, indexed lookup — see
    ``load_co_owners``), and the FULL rule is re-evaluated here, not just
    "does anyone else map it":

    - no other owners at all -> ordinary exclusive deletion, proceed.
    - at least one owner with ``has_content=True`` (includes an unusable/
      poisoned owner row, which always reports ``has_content=True``) -> moved
      to ``late_shared`` and left alone.
    - owners exist and every single one has ``has_content=False`` -> a last
      cached remnant, proceed to delete it, and remember the (sorted) owner
      appids in the resulting ``DeletedDepot``/``FailedDepot`` — see
      ``DeletedDepot.shared_with_uncached``.

    The window is not closed by magic — it is narrowed to the microseconds
    between the recheck and ``remove_depot_dir``, which is the best a
    lock-free design can do, and the remaining race is the same one a mapping
    or a job written *during* an ``rmtree`` would lose anyway.

    If the recheck itself fails (a database error), the depot is **not** deleted
    and is reported as failed: "unknown ownership" must never resolve to
    "delete it" — and, consistently, never resolves to "these are its
    co-owners to flag" either, so ``shared_with_uncached`` is empty on that
    failure.

    ``co_owners`` is a **required keyword argument** with no default (WP 1.6
    second-stage review): a future caller — Phase 3's garbage collection is the
    obvious one — must not be able to silently opt out of the shared-depot
    recheck by forgetting an argument. Skipping the check has to be spelled out
    at the call site (``co_owners=lambda _depotid: []``), which is a thing a
    reviewer can see. An explicit ``co_owners=None`` is deliberately NOT a
    second, quieter way to skip the recheck (WP 3.5 review) — there is no
    ``is not None`` guard here, so calling ``None(depotid)`` raises
    ``TypeError``, which the surrounding ``except Exception`` below turns into
    an ordinary "could not re-check" failure (fail-closed, not a 500). The
    lambda spelling is the only way to say "no recheck" on purpose, and it now
    has to be true of MORE than it did pre-addendum: it opts out of the
    remnant rule too, not just the plain shared-depot check.

    Every decision is logged at INFO (failures at ERROR) with the
    ``cache-delete`` prefix: that log is the audit trail for an operation that
    destroys user data, and it records the *resolved absolute path* that was
    removed, not just the depot id. A remnant deletion is logged distinctly
    ("DELETED (last remnant): ...") so it never blends into an ordinary
    exclusive deletion in the audit trail.

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

        # Last look before destroying anything: is this depot still exclusive,
        # a still-valid last remnant, or did a co-owner's state change since
        # the plan was made? (TOCTOU fix, see the docstring — full rule, not
        # just "does anyone else map it".) No ``is not None`` guard here on
        # purpose (WP 3.5 review): ``co_owners`` is a required, non-Optional
        # keyword argument (see the docstring below), so a caller passing an
        # explicit ``None`` is not a supported "skip the recheck" spelling —
        # it would silently bypass strictly more than the pre-addendum WP 1.6
        # recheck now does (the remnant rule AND the fail-closed error path
        # below), which is exactly the kind of silent opt-out the required-
        # kwarg design exists to prevent. A caller that means "no recheck"
        # must still say so explicitly: ``co_owners=lambda _depotid: []``.
        shared_with_uncached: list[int] = []
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
            if any(co.has_content for co in owners):
                owner_ids = sorted({co.appid for co in owners})
                logger.info(
                    "cache-delete appid=%s depot=%s KEPT (late recheck): shared "
                    "with app(s) %s that currently have cache content",
                    appid, depotid, owner_ids,
                )
                late_shared.append(
                    SharedDepot(depotid=depotid, shared_with=owner_ids)
                )
                continue
            # Every co-owner is verifiably uncached right now -> last
            # cached remnant (ADR-0003 addendum). Proceed to delete below,
            # but remember who to flag with needs_force afterwards.
            shared_with_uncached = sorted({co.appid for co in owners})

        remnant_suffix = (
            f" (last remnant, shared with uncached app(s) {shared_with_uncached})"
            if shared_with_uncached
            else ""
        )

        # lexists, not exists: a broken link must be removed, not declared absent.
        if not os.path.lexists(path):
            logger.info(
                "cache-delete appid=%s depot=%s ALREADY-ABSENT path=%s "
                "(nothing on disk; mapping row kept)%s",
                appid, depotid, path, remnant_suffix,
            )
            deleted.append(
                DeletedDepot(
                    depotid=depotid, size_bytes_freed=0,
                    shared_with_uncached=shared_with_uncached,
                )
            )
            continue

        linked = is_link_like(path)
        size_bytes = depot_dir_bytes(path)
        removed_here, error = remove_depot_dir_settling(path)

        if error is not None:
            logger.error(
                "cache-delete appid=%s depot=%s FAILED path=%s: %s: %s%s",
                appid, depotid, path, type(error).__name__, error, remnant_suffix,
            )
            failed.append(
                FailedDepot(
                    depotid=depotid, error=f"{type(error).__name__}: {error}",
                    shared_with_uncached=shared_with_uncached,
                )
            )
            continue

        if not removed_here:
            logger.info(
                "cache-delete appid=%s depot=%s ALREADY-ABSENT path=%s (a "
                "concurrent deletion removed it; 0 bytes attributed to this "
                "request so the freed total is not counted twice)%s",
                appid, depotid, path, remnant_suffix,
            )
            deleted.append(
                DeletedDepot(
                    depotid=depotid, size_bytes_freed=0,
                    shared_with_uncached=shared_with_uncached,
                )
            )
            continue

        if shared_with_uncached:
            logger.info(
                "cache-delete appid=%s depot=%s DELETED (last remnant): shared "
                "with uncached app(s) %s path=%s bytes_freed=%d link=%s",
                appid, depotid, shared_with_uncached, path, size_bytes, linked,
            )
        else:
            logger.info(
                "cache-delete appid=%s depot=%s DELETED path=%s bytes_freed=%d link=%s",
                appid, depotid, path, size_bytes, linked,
            )
        deleted.append(
            DeletedDepot(
                depotid=depotid, size_bytes_freed=size_bytes,
                shared_with_uncached=shared_with_uncached,
            )
        )

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


def _has_cache_content(
    status: object, last_prefill_at: object, has_active_job: object
) -> bool:
    """The ADR-0003 addendum rule, in one place so the plan-time bulk query
    (``load_co_owner_states``) and the execute-time per-depot query
    (``load_co_owners``) share exactly one definition of "has content".

    ``True`` (protects) unless ALL three signals say "never touched, idle,
    job-free": ``status is None`` means no ``apps`` row exists at all for
    this owner (a ``LEFT JOIN``/missing-key miss), which the addendum lists
    as its own protecting case, so that alone short-circuits to ``True``.
    """
    if status is None:
        return True
    idle = status == jobs.STATUS_IDLE
    never_prefilled = last_prefill_at is None
    job_free = not bool(has_active_job)
    return not (idle and never_prefilled and job_free)


#: Shared by every query below that needs "does this appid have a queued or
#: running job right now" as a correlated EXISTS — keeps the placeholder
#: count and the bound values trivially in sync with jobs.ACTIVE_STATUSES.
_ACTIVE_JOB_PLACEHOLDERS = ",".join("?" for _ in jobs.ACTIVE_STATUSES)


def load_co_owners(conn: sqlite3.Connection, depotid: int, appid: int) -> list[CoOwner]:
    """Apps *other than* ``appid`` that map ``depotid`` right now, each tagged
    with whether it currently has cache content (ADR-0003 addendum).

    The execute-time half of the shared-depot protection (WP 1.6 review,
    extended by the addendum): one **joined** lookup per depot, immediately
    before that depot is removed, so a mapping written — or a co-owner's
    status/last_prefill_at/job changed — after ``plan_deletion`` ran still
    protects the depot. Still a seek, not a scan, despite the join: the
    ``WHERE`` clause is served by ``depot_app_map``'s primary key
    ``(depotid, appid)``, the ``LEFT JOIN`` by ``apps``'s primary key
    (``appid``), and the correlated ``EXISTS`` by ``idx_jobs_appid_status`` —
    each co-owner row costs three index seeks, not a table scan, which is
    what keeps a per-depot recheck affordable at all.

    Unusable co-owner ids (poisoned rows) are reported as
    ``CoOwner(appid=0, has_content=True)`` rather than dropped: the caller
    must treat "somebody unreadable owns this" as protected, never as free to
    delete — the same convention the pre-addendum code used for owner ``0``.
    """
    rows = conn.execute(
        f"""
        SELECT
            dam.appid AS owner_appid,
            ap.status AS status,
            ap.last_prefill_at AS last_prefill_at,
            EXISTS (
                SELECT 1 FROM jobs j
                WHERE j.appid = dam.appid AND j.status IN ({_ACTIVE_JOB_PLACEHOLDERS})
            ) AS has_active_job
        FROM depot_app_map dam
        LEFT JOIN apps ap ON ap.appid = dam.appid
        WHERE dam.depotid = ? AND dam.appid != ?
        """,
        (*jobs.ACTIVE_STATUSES, depotid, appid),
    ).fetchall()

    owners: dict[int, bool] = {}
    for row in rows:
        owner = coerce_positive_id(row["owner_appid"])
        if owner is None:
            owners[0] = True
            continue
        has_content = _has_cache_content(
            row["status"], row["last_prefill_at"], row["has_active_job"]
        )
        # If the same (poisoned-elsewhere-aside) owner id somehow appears
        # twice, protect wins — content-having is sticky, not overwritten by
        # a later uncached row for the same appid (shouldn't happen given the
        # (depotid, appid) primary key, but this stays correct if it did).
        owners[owner] = owners.get(owner, False) or has_content
    return [CoOwner(appid=aid, has_content=has_content) for aid, has_content in sorted(owners.items())]


def other_owner_ids(rows: Iterable[Sequence[object]], appid: int) -> set[int]:
    """The set of usable co-owner appids across ``load_mapping_rows`` output,
    excluding ``appid`` itself.

    Kept separate from ``plan_deletion`` (which stays pure, no I/O) so a
    caller — ``routers/cache.py`` — knows exactly which appids are worth
    asking ``load_co_owner_states`` about before building the plan.
    """
    ids: set[int] = set()
    for row in rows:
        owner = coerce_positive_id(row[1])
        if owner is not None and owner != appid:
            ids.add(owner)
    return ids


def load_co_owner_states(
    conn: sqlite3.Connection, owner_appids: Iterable[int]
) -> dict[int, bool]:
    """``{appid: has_content}`` for the given co-owner appids (ADR-0003
    addendum), fed into ``plan_deletion`` so it can stay pure and DB-free.

    One bulk query for however many co-owners the request's mapping rows
    named (typically a handful), served by ``apps``'s primary key for the
    ``IN (...)`` and ``idx_jobs_appid_status`` for the correlated ``EXISTS``
    — the same index shapes ``load_co_owners`` relies on, just batched across
    appids instead of per-depot.

    An appid with no ``apps`` row at all is simply **absent** from the
    returned dict — ``plan_deletion``'s own conservative default (missing ⇒
    ``has_content=True``) is what turns "no apps row" into "protected", so
    this function does not need to special-case it, and callers that pass an
    id which turns out unusable never regress into "considered cached" by
    accident (they are filtered out below, so they end up as an equally-safe
    "missing" entry).
    """
    owner_ids = sorted({o for o in owner_appids if isinstance(o, int) and o > 0})
    if not owner_ids:
        return {}

    id_placeholders = ",".join("?" for _ in owner_ids)
    rows = conn.execute(
        f"""
        SELECT
            appid,
            status,
            last_prefill_at,
            EXISTS (
                SELECT 1 FROM jobs j
                WHERE j.appid = apps.appid AND j.status IN ({_ACTIVE_JOB_PLACEHOLDERS})
            ) AS has_active_job
        FROM apps
        WHERE appid IN ({id_placeholders})
        """,
        (*jobs.ACTIVE_STATUSES, *owner_ids),
    ).fetchall()
    return {
        row["appid"]: _has_cache_content(
            row["status"], row["last_prefill_at"], row["has_active_job"]
        )
        for row in rows
    }


def set_needs_force_for_remnant_co_owners(
    conn: sqlite3.Connection, appids: Iterable[int]
) -> None:
    """Flag every co-owner of a deleted/removal-attempted last-remnant depot
    with ``needs_force = 1`` (ADR-0003 addendum consequence #2), leaving
    ``status`` and ``last_prefill_at`` untouched.

    These apps are, by the precondition that got them into this set at all
    (``delete_app_depots``'s recheck proved ``has_content=False`` for each of
    them), currently ``status='idle'`` with ``last_prefill_at IS NULL`` — the
    honesty rule this mirrors (WP 3.4, ``reset_app_after_deletion``) is about
    *disk* state, not app lifecycle state, and their lifecycle state did not
    change: a depot THEY still map just lost bytes out from under them, so
    their own next prefill must not trust SteamPrefill's stale
    ``successfullyDownloadedDepots.json`` bookkeeping for that depot.

    **Race note, and why an unconditional ``UPDATE ... SET needs_force = 1``
    is still correct here (not a CAS).** A job for one of these co-owners
    could be claimed by the worker in the microseconds between
    ``delete_app_depots``'s recheck (which proved it job-free a moment ago)
    and this write landing. That is benign, not a repeat of the WP 3.4 bug:
    the WP 3.4 race was about *clearing* ``needs_force`` racing a write that
    *set* it, and it stays closed by ``jobs.clear_needs_force_if_unchanged``'s
    compare-and-swap on the value the job itself observed at claim time —
    unaffected by how ``needs_force`` came to be ``1`` here. If that newly
    claimed job finishes and tries to clear the flag, its CAS compares
    against the value it read at claim time (before this write), which no
    longer matches the current value (this write set it to ``1``), so the
    clear is skipped and ``needs_force`` correctly survives at ``1`` — the
    very self-healing path WP 3.4 built. Setting to ``1`` unconditionally
    therefore needs no CAS of its own: unlike a *clear*, a *set* can never be
    "wrongly" applied — worst case is one redundant ``--force`` run.
    """
    set_needs_force(conn, appids)


def set_needs_force(conn: sqlite3.Connection, appids: Iterable[int]) -> None:
    """``UPDATE apps SET needs_force = 1`` for the given app ids. Commits.

    The one primitive behind every "this app's on-disk state changed or became
    uncertain, so its next prefill must not trust SteamPrefill's own
    bookkeeping" write (ADR-0006 decision 2). Extracted in WP 3.8 so garbage
    collection flags apps through exactly the statement the deletion path
    already uses instead of growing a second copy that could drift.

    An app id with no ``apps`` row simply matches nothing — which is the right
    outcome, not a silent miss: the column's ``DEFAULT 1`` means a row created
    later starts out forced anyway.

    Setting the flag needs no compare-and-swap (unlike *clearing* it, see
    ``jobs.clear_needs_force_if_unchanged``): a set can never be wrongly
    applied — the worst case is one redundant ``--force`` run.
    """
    ids = sorted({int(a) for a in appids if a})
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    conn.execute(f"UPDATE apps SET needs_force = 1 WHERE appid IN ({placeholders})", ids)
    conn.commit()


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
