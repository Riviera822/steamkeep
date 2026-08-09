"""Cache endpoints (plan §6): ``GET /v1/cache/summary``, ``DELETE /v1/cache/{appid}``,
``POST /v1/cache/{appid}/gc``.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth" section) — every route added here is authenticated
automatically.

The deletion endpoint's semantics, decisions and failure modes are documented
in api/README.md ("Per-game deletion"); the safety-critical mechanics live in
``vault_api/deletion.py``. Garbage collection is documented under "Garbage
collection" there, plans in ``vault_api/gc.py`` and executes in
``vault_api/gc_execute.py`` — this file only queues the job.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, StrictBool

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
    #: Non-empty ONLY for a "last cached remnant" deletion (ADR-0003 addendum):
    #: this depot was still mapped to these other, currently-uncached app ids
    #: when it was removed. Empty ``[]`` for an ordinary exclusive deletion
    #: (the ONLY case before this addendum, and still the common case). Every
    #: id listed here has had ``apps.needs_force`` set to 1 as a side effect of
    #: this request — see api/README.md "Per-game deletion".
    shared_with_uncached: list[int] = []


class SkippedSharedDepotOut(BaseModel):
    depotid: int
    #: The other tracked app ids that map this depot (plan §4: "2 depots shared
    #: with game Y, not deleted"). A depot that only *became* shared after the
    #: deletion was planned appears here too, with its fresh owner list — see
    #: the recheck in deletion.delete_app_depots. Meaning UNCHANGED by the
    #: ADR-0003 addendum: a depot only appears here when AT LEAST ONE of these
    #: owners currently has cache content. A shared depot whose every co-owner
    #: is uncached is no longer reported here — see ``deleted_depots``'s
    #: ``shared_with_uncached`` for that case instead.
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
    5. Otherwise ``200``: exclusive depots deleted, protected shared depots
       reported and kept, per-depot failures reported in ``failed`` — AND
       (ADR-0003 addendum) a shared depot deleted anyway when every co-owning
       app currently has no cache content (a "last cached remnant"), flagged
       distinctly in ``deleted_depots`` via ``shared_with_uncached`` rather
       than merged with an ordinary exclusive deletion.

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

    **``apps.needs_force`` (schema v5, WP 3.4, ADR-0006 decision 2)** is set
    to 1 whenever this request changed or left uncertain what is on disk for
    this app — anything in ``deleted_depots`` (including the ALREADY-ABSENT
    case) or ``failed`` — so the next prefill for this app runs with
    ``--force`` rather than trusting SteamPrefill's own now-stale bookkeeping.
    Left untouched when nothing exclusive existed to delete (every mapped
    depot was shared and protected). This is also the documented way an
    operator forces a re-fill: ``DELETE`` then ``POST /v1/prefill`` — no
    separate "force" flag on the prefill API itself (see api/README.md's
    "needs_force lifecycle").

    **ADR-0003 addendum — last cached remnants.** A shared depot is not
    "never delete": it may be removed when NO co-owning app currently has
    cache content (conservatively judged — see ``deletion.plan_deletion`` and
    ``deletion.delete_app_depots``), because otherwise its bytes become
    unreclaimable the moment every co-owner has independently been deleted
    (mapping rows survive deletion by design, so the old "shared -> never
    delete" rule kept such a depot forever, attributed to no app). Every
    co-owner appid that was uncached at the moment ITS depot was removed
    (or removal was attempted and failed, or it was found already absent)
    gets ``apps.needs_force = 1`` — same honesty rule as the requesting app's
    own reset above, because a depot they still map just changed under them —
    but their ``status``/``last_prefill_at`` are left untouched (see
    ``deletion.set_needs_force_for_remnant_co_owners``).
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

        # ADR-0003 addendum: read every co-owner's current content state
        # (status/last_prefill_at/active job) up front, in the same
        # connection as the mapping read, so plan_deletion below can stay a
        # pure function over plain data.
        co_owner_states = deletion.load_co_owner_states(
            conn, deletion.other_owner_ids(rows, appid)
        )

        active_job = jobs.active_job_for_app(conn, appid)

    if active_job is not None:
        # Check-then-act, stated honestly: a job enqueued in the microseconds
        # after this check would still race the deletion. Single-worker
        # operation keeps the window tiny and the consequence is benign (the
        # job refills what was deleted), so this stays a guard rather than a
        # lock that would have to be held across the filesystem work.
        # The label follows the job's own type (WP 3.8): GC jobs share this
        # queue, so "Prefill job N" would now be a lie for half the cases —
        # and an operator who reads "prefill" and goes looking for a download
        # that isn't running has been sent the wrong way.
        job_label = (
            "GC" if str(active_job["type"]) == jobs.JOB_TYPE_GC else "Prefill"
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{job_label} job {active_job['id']} for app {appid} is "
                f"{active_job['status']}. Deleting depots while they are being "
                "downloaded (or garbage-collected) would delete under an active "
                "write — retry once that job has finished (poll "
                "GET /v1/jobs/{id})."
            ),
        )

    plan = deletion.plan_deletion(rows, appid, co_owner_states)

    try:
        depot_root = deletion.resolve_depot_root(cache_root)
    except deletion.DeletionGuardError as exc:
        logger.error("cache-delete appid=%s REFUSED (cache root guard): %s", appid, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    logger.info(
        "cache-delete appid=%s starting: depot_root=%s exclusive=%s remnant=%s "
        "shared=%s unusable_rows=%d",
        appid, depot_root, plan.exclusive,
        [depot.depotid for depot in plan.remnant],
        [depot.depotid for depot in plan.shared], len(plan.unusable),
    )
    deletion.log_kept_depots(appid, plan.shared)

    def current_co_owners(depotid: int) -> list[deletion.CoOwner]:
        """Re-read one depot's other owners immediately before it is removed.

        A fresh short-lived connection per depot, opened and closed inside this
        endpoint's own thread (the ``deps.db_opener`` rule) and holding no
        transaction — this is the execute-time half of the shared-depot
        protection, see ``deletion.delete_app_depots``.
        """
        with open_db() as conn:
            return deletion.load_co_owners(conn, depotid, appid)

    # Both plan.exclusive AND plan.remnant go through the identical execute-
    # time recheck below — delete_app_depots does not trust either
    # classification, it re-derives the outcome from fresh co-owner data
    # (ADR-0003 addendum's TOCTOU close, see that function's docstring).
    # Sorted (WP 3.5 review nit): `deleted`/`failed` are built by
    # `delete_app_depots` in the order it iterates this list, and
    # `plan.exclusive + [...]` is not globally sorted by depotid on its own
    # (each half is, the concatenation isn't) — sorting here keeps
    # `deleted_depots` in the same depotid order `skipped_shared` already is.
    candidate_depotids = sorted(
        plan.exclusive + [depot.depotid for depot in plan.remnant]
    )
    deleted, failed, late_shared = deletion.delete_app_depots(
        depot_root, appid, candidate_depotids, co_owners=current_co_owners
    )
    failed = plan.unusable + failed
    skipped_shared = sorted(
        plan.shared + late_shared, key=lambda depot: depot.depotid
    )

    # ADR-0003 addendum consequence #2: every co-owner a remnant depot was
    # shared with — whether the removal succeeded, failed mid-tree, or found
    # the depot already absent — needs its own needs_force set, because a
    # depot IT still maps just changed (or its state became uncertain) out
    # from under it. Gathered from both deleted and failed so a partial
    # failure still flags the co-owners of the depots that WERE touched.
    remnant_co_owner_appids: set[int] = set()
    for depot in deleted:
        remnant_co_owner_appids.update(depot.shared_with_uncached)
    for depot in failed:
        remnant_co_owner_appids.update(depot.shared_with_uncached)

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
    # WP 3.4 / ADR-0006 decision 2: this request changed or left uncertain
    # what is on disk for this app the moment ANYTHING landed in `deleted`
    # (includes the ALREADY-ABSENT case — the mapping claimed this depot was
    # here and it wasn't) or in `failed` (cache state unknown after a partial
    # failure). The "everything mapped turned out to be shared, nothing
    # exclusive to touch" case leaves both empty, so needs_force is left
    # exactly as it was — nothing on disk changed for this app.
    set_needs_force = bool(deleted) or bool(failed)
    with open_db() as conn:
        # Two separate statements, each committing on its own (both
        # `reset_app_after_deletion` and `set_needs_force_for_remnant_co_owners`
        # call `conn.commit()` internally) — a crash between them would leave
        # the requesting app's reset durable but the remnant co-owners'
        # needs_force not yet set. Accepted, not closed, for the same reason
        # WP 3.4 accepted the equivalent window around `reset_app_after_deletion`
        # itself: the filesystem work (which already happened, durably, before
        # either commit) is the side effect that actually matters, and the
        # worst case here is a co-owner's next prefill trusting SteamPrefill's
        # own bookkeeping one run longer than ideal, not data loss or a wrong
        # deletion — the depot in question is already gone either way.
        deletion.reset_app_after_deletion(
            conn, appid, new_status, set_needs_force=set_needs_force
        )
        deletion.set_needs_force_for_remnant_co_owners(conn, remnant_co_owner_appids)

    total_bytes_freed = sum(depot.size_bytes_freed for depot in deleted)
    remnant_deleted_count = sum(1 for d in deleted if d.shared_with_uncached)
    logger.info(
        "cache-delete appid=%s finished: deleted=%d (of which %d last-remnant) "
        "skipped_shared=%d (of which %d late) failed=%d bytes_freed=%d; status "
        "set to '%s', last_prefill_at cleared, needs_force=%s; remnant "
        "co-owners flagged=%s; mapping rows kept",
        appid, len(deleted), remnant_deleted_count, len(skipped_shared),
        len(late_shared), len(failed), total_bytes_freed, new_status,
        "1" if set_needs_force else "unchanged", sorted(remnant_co_owner_appids),
    )

    return CacheDeletionOut(
        appid=appid,
        deleted_depots=[
            DeletedDepotOut(
                depotid=d.depotid,
                size_bytes_freed=d.size_bytes_freed,
                shared_with_uncached=d.shared_with_uncached,
            )
            for d in deleted
        ],
        skipped_shared=[
            SkippedSharedDepotOut(depotid=s.depotid, shared_with=s.shared_with)
            for s in skipped_shared
        ],
        failed=[FailedDepotOut(depotid=f.depotid, error=f.error) for f in failed],
        total_bytes_freed=total_bytes_freed,
    )


# --------------------------------------------------------------------------
# POST /v1/cache/{appid}/gc — plan §6, ADR-0007
# --------------------------------------------------------------------------

#: The two words this API uses for the two modes, in the response and nowhere
#: else in logic — ``execute`` is the boolean that decides anything.
MODE_DRY_RUN = "dry-run"
MODE_EXECUTE = "execute"


class GcRequest(BaseModel):
    """Body of ``POST /v1/cache/{appid}/gc``. Optional — omitting it is a dry run.

    ``extra="forbid"`` for the same reason as ``PrefillRequest`` and the
    mapping endpoints, and with more at stake: a typo'd field name must 422
    rather than be silently dropped. (Dropping ``{"exceute": true}`` would land
    on the *safe* side — a dry run — but an operator who then believes the
    cache was cleaned is exactly as misinformed as one who did not want a
    deletion, and a 422 is free.)
    """

    model_config = ConfigDict(extra="forbid")

    #: ``StrictBool``, not ``bool``: pydantic's lax mode would accept
    #: ``"true"``, ``"yes"``, ``"on"`` and ``1`` here (docs/LEARNINGS.md
    #: records the int-field version of the same coercion). For the one flag
    #: that turns a report into a deletion, the only accepted spelling is a
    #: literal JSON ``true`` — a client that sends a string got its request
    #: rejected rather than half-understood.
    execute: StrictBool = False


class GcJobRef(BaseModel):
    """Which job was queued, and — unmistakably — in which mode."""

    appid: int
    job_id: int
    status: str
    #: Always ``"gc"``; present so a client that stores this response has the
    #: same ``type`` value ``GET /v1/jobs`` will report for the job.
    type: str
    #: ``"dry-run"`` or ``"execute"``. Redundant with ``execute`` below on
    #: purpose: the boolean is what the code acts on, the word is what a human
    #: reads in a terminal, and a UI that shows either one cannot get the mode
    #: wrong by accident.
    mode: str
    #: True only when this job will actually delete files.
    execute: bool
    #: True when an existing queued/running GC job **in the same mode** was
    #: returned instead of a second one being created (see
    #: ``jobs.enqueue_gc``: a dry run and an execute run never dedupe into
    #: each other).
    deduplicated: bool


@router.post(
    "/v1/cache/{appid}/gc",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=GcJobRef,
)
def create_gc_job(
    appid: int = Path(ge=1, description="Steam app id whose depots to collect"),
    body: GcRequest | None = None,
    open_db: DbOpener = Depends(db_opener),
) -> GcJobRef:
    """Queue a garbage-collection job for one game (plan §6, ADR-0007). 202.

    > For one depot, the chunks worth keeping are the UNION of the current
    > manifests of every app that has a claim on that depot; everything else in
    > that depot's ``chunk/`` directory is an orphan left behind by a game
    > update.

    **Dry run by default (ADR-0007).** With no body, or with
    ``{"execute": false}``, the job plans and reports and deletes **nothing**.
    Deletion requires the explicit opt-in ``{"execute": true}`` — a literal
    JSON boolean, see ``GcRequest.execute``. The mode is stored on the job row
    (``jobs.gc_execute``, schema v7), so it survives the queue wait and a
    restart, and an old job row still says which mode it ran in.

    Order of decisions:

    1. ``404`` if the app has no row in ``apps`` — vault-api tracks no such
       app, so there is nothing to collect.
    2. ``404`` if the app maps no depots — nothing to collect (same reasoning
       and same wording style as ``DELETE /v1/cache/{appid}``).
    3. ``202`` otherwise, with the job reference. The work happens on the
       single background worker.

    **No ``409`` for an active job, and that is not an oversight.** The
    equivalent guard on ``DELETE /v1/cache/{appid}`` exists because that
    endpoint deletes in the *request thread*, so it really can race a running
    download. GC runs *on the worker*, which executes one job at a time, so a
    GC job physically cannot overlap a prefill of the same app — it simply
    waits its turn. Serialization is the guard (ADR-0007: "runs as a queued
    job, serialized with prefills on the single worker").

    **The plan is built when the job runs, never here.** This endpoint stores
    an app id and a mode; the worker plans against a fresh database read and a
    fresh filesystem scan at execution time. A game that updates while the job
    sits in the queue is therefore collected against its new manifest — see
    ``vault_api/gc_execute.py``'s "TOCTOU" section.

    What a GC job does to app state: **nothing** to ``apps.status`` or
    ``last_prefill_at``; an *executing* run that actually reclaimed chunks
    invalidates the size cache and sets ``apps.needs_force = 1`` for every app
    mapped to a depot it took chunks from. The full argument (including why
    the "obvious" answer of not setting it was rejected) is in
    ``vault_api/gc_execute.py``'s module docstring and in api/README.md.
    """
    execute = bool(body.execute) if body is not None else False

    with open_db() as conn:
        app_row = conn.execute(
            "SELECT appid FROM apps WHERE appid = ?", (appid,)
        ).fetchone()
        if app_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"Unknown appid {appid} — vault-api tracks no such app, so there "
                    "is nothing to garbage-collect."
                ),
            )

        if not deletion.load_mapping_rows(conn, appid):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"App {appid} has no depot mappings, so there is nothing to "
                    "garbage-collect. Mappings are created by a successful prefill "
                    "or via PUT /v1/mapping/{depotid}."
                ),
            )

        job, created = jobs.enqueue_gc(conn, appid, execute=execute)

    logger.info(
        "cache-gc appid=%s queued job %s in %s mode (deduplicated=%s)",
        appid, job["id"], MODE_EXECUTE if execute else MODE_DRY_RUN, not created,
    )
    return GcJobRef(
        appid=appid,
        job_id=int(job["id"]),  # type: ignore[arg-type]
        status=str(job["status"]),
        type=str(job["type"]),
        mode=MODE_EXECUTE if execute else MODE_DRY_RUN,
        execute=execute,
        deduplicated=not created,
    )
