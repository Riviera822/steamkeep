"""Prefill + job endpoints (plan §6): POST /v1/prefill, GET /v1/jobs[/{id}].

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth" section) — every route added here is authenticated
automatically.

The endpoints only touch the queue table; execution is the background worker's
job (``vault_api/worker.py``), so ``POST /v1/prefill`` answers immediately with
202 and the app polls ``GET /v1/jobs/{id}``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from vault_api import jobs as jobs_queue
from vault_api.auth import require_api_key
from vault_api.deps import DbOpener, db_opener
from vault_api.validation import AppId

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["jobs"])


class PrefillRequest(BaseModel):
    # extra="forbid" for the same reason as the mapping endpoints: a typo'd
    # field name must 422 rather than silently queue nothing.
    model_config = ConfigDict(extra="forbid")

    #: At least one app id; each must be >= 1.
    appids: list[AppId] = Field(min_length=1)


class PrefillJobRef(BaseModel):
    appid: int
    job_id: int
    status: str
    #: True when an existing queued/running job for this app was returned
    #: instead of a new one being created (see the docstring below).
    deduplicated: bool


class JobSummary(BaseModel):
    """A job without its log excerpt — the list view stays small."""

    id: int
    appid: int
    type: str
    status: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    #: SteamPrefill's own summary-table counters (schema v4, WP 3.3,
    #: ADR-0006 decision 1). All three ``None`` until the job finishes, and
    #: ``updated``/``up_to_date`` stay ``None`` even after finishing if
    #: ``summary_parse_ok`` is ``False`` (the table could not be parsed — see
    #: ``vault_api/prefill_summary.py``) or the job never got as far as a
    #: parsed summary (a GC job, a failed/timed-out/aborted prefill).
    updated: int | None = None
    up_to_date: int | None = None
    summary_parse_ok: bool | None = None
    #: GC jobs only (schema v7, WP 3.8, ADR-0007's "dry-run by default"):
    #: ``false`` = this job planned and reported but deleted nothing,
    #: ``true`` = it was allowed to delete. ``null`` for every ``prefill``
    #: job — the field is not applicable, which is a different thing from
    #: "dry run" and is reported as such.
    gc_execute: bool | None = None


class JobDetail(JobSummary):
    #: Tail of SteamPrefill's combined stdout/stderr (ANSI-stripped, capped at
    #: 4 KiB) plus vault-api's own diagnostic lines. Null until the job finishes.
    log_excerpt: str | None


@router.post(
    "/v1/prefill",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=list[PrefillJobRef],
)
def create_prefill_jobs(
    body: PrefillRequest,
    open_db: DbOpener = Depends(db_opener),
) -> list[PrefillJobRef]:
    """Queue one prefill job per app id (plan §6). 202 — execution is async.

    **Dedupe:** if an app already has a ``queued`` or ``running`` job, that
    job's id is returned with ``deduplicated: true`` instead of a second job
    being stacked on top of it. Requesting the same app twice (impatient user,
    or Phase 3's miss trigger firing on several misses of one game) therefore
    converges on one job rather than a pile of redundant re-downloads.

    Duplicate app ids *within one request body* fall out of the same rule: the
    response keeps one entry per requested id, in request order, so the second
    occurrence points at the same ``job_id`` with ``deduplicated: true``.
    """
    refs: list[PrefillJobRef] = []
    with open_db() as conn:
        for appid in body.appids:
            job, created = jobs_queue.enqueue_prefill(conn, appid)
            refs.append(
                PrefillJobRef(
                    appid=appid,
                    job_id=int(job["id"]),  # type: ignore[arg-type]
                    status=str(job["status"]),
                    deduplicated=not created,
                )
            )
    return refs


@router.get("/v1/jobs", response_model=list[JobSummary])
def list_recent_jobs(
    limit: int = Query(default=20, ge=1, le=200),
    open_db: DbOpener = Depends(db_opener),
) -> list[JobSummary]:
    """Most recent jobs, newest first — the app's polling list."""
    with open_db() as conn:
        rows = jobs_queue.list_jobs(conn, limit)
    return [JobSummary(**row) for row in rows]  # type: ignore[arg-type]


@router.get("/v1/jobs/{job_id}", response_model=JobDetail)
def get_job_status(
    job_id: int,
    open_db: DbOpener = Depends(db_opener),
) -> JobDetail:
    """Status of one job incl. its log excerpt (plan §6: "for app polling")."""
    with open_db() as conn:
        job = jobs_queue.get_job(conn, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown job id {job_id}"
        )
    return JobDetail(**job)  # type: ignore[arg-type]
