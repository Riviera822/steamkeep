"""Cache-wide reporting (plan §6): GET /v1/cache/summary.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth" section) — every route added here is authenticated
automatically. Deletion (``DELETE /v1/cache/{appid}``) and garbage collection
(``POST /v1/cache/{appid}/gc``) are later work packages (WP 1.6 / Phase 3);
this router exists now so those routes have a natural home under
``/v1/cache`` without a later restructor.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from vault_api.auth import require_api_key
from vault_api.deps import DbOpener, db_opener, get_cache_root, get_size_cache
from vault_api.sizes import SizeCache, build_cache_summary

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
    with open_db() as conn:
        summary = build_cache_summary(
            conn, cache_root, size_cache, top_n=TOP_CONSUMERS_LIMIT
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
