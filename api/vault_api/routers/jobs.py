"""Prefill + job endpoints (plan §6): POST /v1/prefill, GET /v1/jobs[/{id}],
and job control (WP 3.12): DELETE /v1/jobs/{id}, POST /v1/jobs/{id}/pause,
POST /v1/jobs/{id}/resume.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth" section) — every route added here is authenticated
automatically.

The endpoints only touch the queue table; execution is the background worker's
job (``vault_api/worker.py``), so ``POST /v1/prefill`` answers immediately with
202 and the app polls ``GET /v1/jobs/{id}``. The three control endpoints follow
the same shape: they record an intent and answer immediately. For a queued or
paused job the intent IS the whole transition and it completes inside the
request; for a running job the worker owns the subprocess and therefore owns
the transition, so the response says "requested" and the client polls. Both
cases return ``200`` and describe which happened in the body rather than
splitting the outcome across status codes a client would have to branch on
twice (once on the code, once on the state it then reads anyway).
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
    #: When this job was most recently suspended (schema v8, WP 3.12), or
    #: ``null`` if it never was. NOT cleared on resume — it stays the true
    #: answer to "when was this last paused"; ``status`` says whether it is
    #: paused *now*.
    paused_at: str | None = None
    #: A pending operator request against a **running** job: ``"cancel"``,
    #: ``"pause"`` or ``null``. Lets a polling UI show "cancelling…" instead of
    #: a job that still says ``running`` for no visible reason. Always ``null``
    #: once the job reaches a terminal status or is parked.
    stop_request: str | None = None


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


# --------------------------------------------------------------------------
# Job control (WP 3.12)
# --------------------------------------------------------------------------


class JobControlOut(BaseModel):
    """What a cancel/pause/resume request did to the job."""

    job_id: int
    #: The job's status **after** the request. ``"cancelled"``/``"queued"`` when
    #: the transition completed inside this request; still ``"running"`` when
    #: only the request was recorded.
    status: str
    #: ``"immediate"`` — the transition is done; ``"requested"`` — the worker
    #: will carry it out (poll ``GET /v1/jobs/{id}``); ``"resumed"`` — the job
    #: is queued again. A client that only reads this field always knows
    #: whether it has to keep polling.
    outcome: str
    #: Plain-language explanation of exactly what happened and what to do next.
    detail: str


def _control_response(
    job_id: int, result: jobs_queue.ControlResult
) -> JobControlOut:
    """Turn a ``jobs.ControlResult`` into a response or the right HTTP error.

    One helper for all three endpoints so ``unknown`` -> 404 and ``conflict``
    -> 409 cannot drift apart between them (and so the 409 detail is always
    the domain layer's own explanation, never a second one written here).
    """
    if result.outcome == jobs_queue.CONTROL_UNKNOWN:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown job id {job_id}"
        )
    if result.outcome == jobs_queue.CONTROL_CONFLICT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=result.detail
        )
    assert result.job is not None  # every other outcome carries the row
    return JobControlOut(
        job_id=job_id,
        status=str(result.job["status"]),
        outcome=result.outcome,
        detail=result.detail,
    )


@router.delete("/v1/jobs/{job_id}", response_model=JobControlOut)
def cancel_job(
    job_id: int,
    open_db: DbOpener = Depends(db_opener),
) -> JobControlOut:
    """Cancel a job (WP 3.12). ``404`` unknown id, ``409`` already finished.

    - **queued** — cancelled on the spot and never runs. The decision is taken
      under the same write lock the worker claims jobs with, so "cancelled but
      it ran anyway" is not a race this can lose.
    - **paused** — cancelled on the spot too; pause already terminated
      SteamPrefill, so there is nothing left to stop.
    - **running, prefill** — SteamPrefill is terminated by the worker and the
      job ends ``cancelled``. Whatever was downloaded stays in the cache (it is
      served as local HITs later); the depot mapping, the manifest state and
      ``needs_force`` are all left untouched, because a run that did not finish
      is not evidence about any of them.
    - **running, GC** — cooperative: the depot currently being processed is
      finished, then the run stops. Documented rather than hidden — one depot
      is bounded work and stopping half way through one would leave a state
      nobody can reason about.
    - **done / error / cancelled** — ``409``. The job already has an outcome,
      and that outcome is what actually happened; it is not rewritten.

    Returns ``200`` in both working cases; ``outcome`` says whether the job is
    already cancelled (``"immediate"``) or whether the worker still has to act
    (``"requested"``). There is deliberately no ``204``: a cancellation that is
    still in flight has something to say.
    """
    with open_db() as conn:
        result = jobs_queue.cancel_job(conn, job_id)
    return _control_response(job_id, result)


@router.post("/v1/jobs/{job_id}/pause", response_model=JobControlOut)
def pause_job(
    job_id: int,
    open_db: DbOpener = Depends(db_opener),
) -> JobControlOut:
    """Pause a running **prefill** job (WP 3.12). ``404`` / ``409`` otherwise.

    **Pause terminates SteamPrefill — there is no wire protocol.** SteamPrefill
    offers no suspend signal, so pausing means killing the subprocess and
    resuming means running it again from the start. That is affordable, not
    wasteful: every chunk the first attempt already stored is served from disk
    by vault-core as a local HIT (ADR-0001 measured ~120x faster than a miss),
    so **the cache itself is the progress store**. SteamPrefill's own
    ``successfullyDownloadedDepots.json`` additionally keeps the depots it
    fully finished, so a non-forced resume skips those outright.

    ``409`` for a GC job (GC runs are short and rebuild their plan every time —
    cancel it instead) and for any job that is not currently ``running``.

    Answers ``200`` immediately with ``outcome: "requested"``: the worker owns
    the subprocess, so it performs the actual suspension. Poll
    ``GET /v1/jobs/{id}`` until ``status`` is ``"paused"``.
    """
    with open_db() as conn:
        result = jobs_queue.request_pause(conn, job_id)
    return _control_response(job_id, result)


@router.post("/v1/jobs/{job_id}/resume", response_model=JobControlOut)
def resume_job(
    job_id: int,
    open_db: DbOpener = Depends(db_opener),
) -> JobControlOut:
    """Resume a paused job (WP 3.12). ``404`` unknown, ``409`` if not paused.

    The job goes back to ``queued`` **keeping its original job id**, and since
    the queue is FIFO by id it therefore runs before anything enqueued while it
    was paused — resuming means "carry on with this", not "get in line again".
    That is also why no priority column exists.

    Completes inside the request (``outcome: "resumed"``): nothing has to be
    stopped, the worker simply finds the job at the head of the queue on its
    next poll.
    """
    with open_db() as conn:
        result = jobs_queue.resume_job(conn, job_id)
    return _control_response(job_id, result)
