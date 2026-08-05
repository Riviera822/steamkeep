"""Cache endpoints (plan §6): ``GET /v1/cache/summary``, ``DELETE /v1/cache/{appid}``.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth" section) — every route added here is authenticated
automatically. Garbage collection (``POST /v1/cache/{appid}/gc``) is Phase 3.

The deletion endpoint's semantics, decisions and failure modes are documented
in api/README.md ("Per-game deletion"); the safety-critical mechanics live in
``vault_api/deletion.py``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel

from vault_api import deletion, jobs
from vault_api.auth import require_api_key
from vault_api.deps import DbOpener, db_opener, get_cache_root, get_size_cache
from vault_api.sizes import SizeCache, build_cache_summary

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["cache"])

#: Plan §6: "per-app top consumers (top 10: appid, name, size_bytes)".
TOP_CONSUMERS_LIMIT = 10


class TopConsumerOut(BaseModel):
    appid: int
    name: str | None
    size_bytes: int


class UnmappedDepotsOut(BaseModel):
    count: int
    size_bytes: int


class CacheSummaryOut(BaseModel):
    #: Actual disk usage of depot/, each depot counted exactly once — may be
    #: LESS than the sum of every game's size_bytes on GET /v1/games, since
    #: shared depots count into every app that maps them there (documented
    #: in vault_api/sizes.py::app_size_bytes).
    total_bytes: int
    top_consumers: list[TopConsumerOut]
    #: Depot directories on disk with no mapping row for any app.
    unmapped_depots: UnmappedDepotsOut
    #: Free space on the filesystem backing VAULT_CACHE_ROOT; null if it
    #: could not be determined (see sizes.free_disk_bytes).
    free_disk_bytes: int | None


@router.get("/v1/cache/summary", response_model=CacheSummaryOut)
def get_cache_summary(
    open_db: DbOpener = Depends(db_opener),
    size_cache: SizeCache = Depends(get_size_cache),
    cache_root: str = Depends(get_cache_root),
) -> CacheSummaryOut:
    """Total usage, top consumers, unmapped depots, free space (plan §6)."""
    # The snapshot is taken BEFORE the connection is opened (WP 1.6 carry-over
    # fix #2 from the WP 1.5 review): a size-cache miss walks the whole depot/
    # tree while holding SizeCache's lock, and there is no reason to hold an
    # open SQLite connection across that.
    snapshot = size_cache.get(cache_root)

    with open_db() as conn:
        summary = build_cache_summary(
            conn, cache_root, snapshot, top_n=TOP_CONSUMERS_LIMIT
        )

    return CacheSummaryOut(
        total_bytes=summary.total_bytes,
        top_consumers=[
            TopConsumerOut(appid=c.appid, name=c.name, size_bytes=c.size_bytes)
            for c in summary.top_consumers
        ],
        unmapped_depots=UnmappedDepotsOut(
            count=summary.unmapped_depots.count,
            size_bytes=summary.unmapped_depots.size_bytes,
        ),
        free_disk_bytes=summary.free_disk_bytes,
    )


# --------------------------------------------------------------------------
# DELETE /v1/cache/{appid} — plan §4 "Deletion"
# --------------------------------------------------------------------------


class DeletedDepotOut(BaseModel):
    depotid: int
    #: Bytes measured immediately before the directory was removed. 0 for a
    #: depot that was already absent, and 0 for a symlinked/junctioned depot
    #: directory (only the link is removed — its target's bytes are not freed).
    size_bytes_freed: int


class SkippedSharedDepotOut(BaseModel):
    depotid: int
    #: The other tracked app ids that map this depot (plan §4: "2 depots shared
    #: with game Y, not deleted"). A depot that only *became* shared after the
    #: deletion was planned appears here too, with its fresh owner list — see
    #: the recheck in deletion.delete_app_depots.
    shared_with: list[int]


class FailedDepotOut(BaseModel):
    #: 0 when the mapping row's depot id itself was unusable and cannot be
    #: named (poisoned database); the error string carries the raw value.
    depotid: int
    error: str


class CacheDeletionOut(BaseModel):
    appid: int
    deleted_depots: list[DeletedDepotOut]
    #: Never deleted, always reported (plan §4 shared-depot protection).
    skipped_shared: list[SkippedSharedDepotOut]
    #: Non-empty means a PARTIAL deletion: these depots are still on disk (or
    #: partly on disk). The response is still 200 — see api/README.md.
    failed: list[FailedDepotOut]
    #: Sum of deleted_depots[].size_bytes_freed. A floor, never an
    #: overstatement: a depot that failed mid-tree contributes 0.
    total_bytes_freed: int


@router.delete("/v1/cache/{appid}", response_model=CacheDeletionOut)
def delete_cached_app(
    appid: int = Path(ge=1, description="Steam app id whose depots to delete"),
    open_db: DbOpener = Depends(db_opener),
    size_cache: SizeCache = Depends(get_size_cache),
    cache_root: str = Depends(get_cache_root),
) -> CacheDeletionOut:
    """Delete one game's depot directories from the cache (plan §4/§6).

    Order of decisions, all made before anything is removed:

    1. ``404`` if the app has no row in ``apps`` — nothing to delete.
    2. ``404`` if the app maps no depots — nothing to delete (checked before
       the job guard on purpose: "there is nothing here" is a more useful
       answer than "come back later").
    3. ``409`` if a prefill job for this app is ``queued`` or ``running``.
       Deleting the depots a download is writing into is a footgun: the job
       would keep refilling them and the reported freed bytes would be
       fiction. The client retries after the job finishes.
    4. ``500`` if the cache-root guards refuse (``deletion.resolve_depot_root``
       — empty / filesystem root / no ``depot/`` directory). Nothing was
       deleted.
    5. Otherwise ``200``: exclusive depots deleted, shared depots reported and
       kept, per-depot failures reported in ``failed``.

    Deliberately **200, not 207**, when some depots failed: the response body
    already distinguishes deleted / skipped / failed per depot, and a
    multi-status code would force every client to special-case a status it
    cannot act on differently anyway. The app's own state does carry the
    outcome, though: ``apps.status`` becomes ``error`` if **anything** failed
    and ``idle`` only on a fully clean run, and ``last_prefill_at`` is cleared
    in both cases.

    The deletion runs in the request thread (FastAPI's threadpool, this being a
    sync endpoint), which is right for the typical size of a depot tree and is
    what lets the response report exactly what happened. A client that
    disconnects mid-deletion does not abort it — the work completes; only the
    report is lost. No database transaction is held across the filesystem work:
    the connection is opened for the read, closed, and re-opened afterwards for
    the status reset.

    **The depot→app mapping rows are KEPT.** The mapping is knowledge (which
    depots belong to this game), not cache state — see api/README.md for the
    decision and its consequences.
    """
    with open_db() as conn:
        app_row = conn.execute(
            "SELECT appid FROM apps WHERE appid = ?", (appid,)
        ).fetchone()
        if app_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"Unknown appid {appid} — vault-api tracks no such app, so there "
                    "is nothing to delete."
                ),
            )

        rows = deletion.load_mapping_rows(conn, appid)
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"App {appid} has no depot mappings, so there is nothing to "
                    "delete. Mappings are created by a successful prefill or via "
                    "PUT /v1/mapping/{depotid}."
                ),
            )

        active_job = jobs.active_job_for_app(conn, appid)

    if active_job is not None:
        # Check-then-act, stated honestly: a job enqueued in the microseconds
        # after this check would still race the deletion. Single-worker
        # operation keeps the window tiny and the consequence is benign (the
        # job refills what was deleted), so this stays a guard rather than a
        # lock that would have to be held across the filesystem work.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Prefill job {active_job['id']} for app {appid} is "
                f"{active_job['status']}. Deleting depots while they are being "
                "downloaded would delete under an active write — retry once that "
                "job has finished (poll GET /v1/jobs/{id})."
            ),
        )

    plan = deletion.plan_deletion(rows, appid)

    try:
        depot_root = deletion.resolve_depot_root(cache_root)
    except deletion.DeletionGuardError as exc:
        logger.error("cache-delete appid=%s REFUSED (cache root guard): %s", appid, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    logger.info(
        "cache-delete appid=%s starting: depot_root=%s exclusive=%s shared=%s "
        "unusable_rows=%d",
        appid, depot_root, plan.exclusive,
        [depot.depotid for depot in plan.shared], len(plan.unusable),
    )
    deletion.log_kept_depots(appid, plan.shared)

    def current_co_owners(depotid: int) -> list[int]:
        """Re-read one depot's other owners immediately before it is removed.

        A fresh short-lived connection per depot, opened and closed inside this
        endpoint's own thread (the ``deps.db_opener`` rule) and holding no
        transaction — this is the execute-time half of the shared-depot
        protection, see ``deletion.delete_app_depots``.
        """
        with open_db() as conn:
            return deletion.load_co_owners(conn, depotid, appid)

    deleted, failed, late_shared = deletion.delete_app_depots(
        depot_root, appid, plan.exclusive, co_owners=current_co_owners
    )
    failed = plan.unusable + failed
    skipped_shared = sorted(
        plan.shared + late_shared, key=lambda depot: depot.depotid
    )

    # Disk content changed (or may have) — the size cache must not keep serving
    # pre-deletion sizes for up to VAULT_SIZE_CACHE_TTL seconds. This is the
    # invalidation hook sizes.SizeCache exports for exactly this case (WP 1.5).
    size_cache.invalidate()

    # Any failure at all -> 'error', never 'idle' (WP 1.6 review, blocker).
    # "Failed" does NOT mean "untouched": shutil.rmtree deletes files as it
    # walks and only then raises, so a failed depot is typically *half* deleted
    # (measured: 150 of 300 chunk files gone, 0 bytes reported freed). Leaving
    # such an app at 'done' with its last_prefill_at intact would show a green
    # badge on a half-destroyed game and — worse — Phase 3's staleness check
    # compares manifest ids, not file counts, so it would never re-fill it.
    # 'error' is the honest state: the operator sees it, and a re-prefill (which
    # runs with --force) repairs the cache. last_prefill_at is cleared either
    # way, because it is no longer true either way.
    new_status = jobs.STATUS_ERROR if failed else jobs.STATUS_IDLE
    with open_db() as conn:
        deletion.reset_app_after_deletion(conn, appid, new_status)

    total_bytes_freed = sum(depot.size_bytes_freed for depot in deleted)
    logger.info(
        "cache-delete appid=%s finished: deleted=%d skipped_shared=%d (of which "
        "%d late) failed=%d bytes_freed=%d; status set to '%s', last_prefill_at "
        "cleared; mapping rows kept",
        appid, len(deleted), len(skipped_shared), len(late_shared), len(failed),
        total_bytes_freed, new_status,
    )

    return CacheDeletionOut(
        appid=appid,
        deleted_depots=[
            DeletedDepotOut(depotid=d.depotid, size_bytes_freed=d.size_bytes_freed)
            for d in deleted
        ],
        skipped_shared=[
            SkippedSharedDepotOut(depotid=s.depotid, shared_with=s.shared_with)
            for s in skipped_shared
        ],
        failed=[FailedDepotOut(depotid=f.depotid, error=f.error) for f in failed],
        total_bytes_freed=total_bytes_freed,
    )
