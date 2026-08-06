"""Games endpoints (plan §6): GET /v1/games, GET /v1/games/{appid}.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth" section) — every route added here is authenticated
automatically.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from vault_api.auth import require_api_key
from vault_api.deps import DbOpener, db_opener, get_cache_root, get_size_cache
from vault_api.sizes import SizeCache, app_size_bytes

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["games"])


class GameSummary(BaseModel):
    appid: int
    name: str | None
    status: str
    last_prefill_at: str | None
    depot_count: int
    # Per-app size (WP 1.5): sum of this app's mapped depots' bytes on disk,
    # from the cached scan (vault_api/sizes.py). Null if unmapped
    # (depot_count == 0) or uncached (mapped but never written to disk yet)
    # — see app_size_bytes for why those two cases are both "unknown", not 0.
    size_bytes: int | None = None
    # Whether the NEXT prefill for this app will run with --force (schema v5,
    # WP 3.4, ADR-0006 decision 2): True for a never-filled app (schema
    # default) or after a deletion that changed/left uncertain what's on
    # disk; False once a successful run has confirmed/refreshed it. Operator
    # visibility only — the API surface for triggering a forced run stays
    # DELETE then POST /v1/prefill, see api/README.md.
    needs_force: bool


class DepotEntry(BaseModel):
    depotid: int
    # True if this depot is also mapped to at least one other app (plan
    # §4 shared-depot semantics: shared depots are skipped on deletion).
    shared: bool
    # Bytes on disk for this one depot (WP 1.5), null if never written yet.
    size_bytes: int | None = None


class GameDetail(BaseModel):
    appid: int
    name: str | None
    status: str
    last_prefill_at: str | None
    depots: list[DepotEntry]
    # See GameSummary.size_bytes.
    size_bytes: int | None = None
    # See GameSummary.needs_force.
    needs_force: bool


@router.get("/v1/games", response_model=list[GameSummary])
def list_games(
    open_db: DbOpener = Depends(db_opener),
    size_cache: SizeCache = Depends(get_size_cache),
    cache_root: str = Depends(get_cache_root),
) -> list[GameSummary]:
    """All tracked apps with their depot count and size (plan §6)."""
    with open_db() as conn:
        rows = conn.execute(
            """
            SELECT a.appid, a.name, a.status, a.last_prefill_at, a.needs_force,
                   COUNT(d.depotid) AS depot_count
            FROM apps a
            LEFT JOIN depot_app_map d ON d.appid = a.appid
            GROUP BY a.appid
            ORDER BY a.appid
            """
        ).fetchall()
        depot_rows = conn.execute(
            "SELECT appid, depotid FROM depot_app_map"
        ).fetchall()

    app_depotids: dict[int, list[int]] = {}
    for row in depot_rows:
        app_depotids.setdefault(int(row["appid"]), []).append(int(row["depotid"]))

    depot_bytes = size_cache.get(cache_root).depot_bytes

    return [
        GameSummary(
            appid=row["appid"],
            name=row["name"],
            status=row["status"],
            last_prefill_at=row["last_prefill_at"],
            depot_count=row["depot_count"],
            size_bytes=app_size_bytes(app_depotids.get(row["appid"], []), depot_bytes),
            needs_force=bool(row["needs_force"]),
        )
        for row in rows
    ]


@router.get("/v1/games/{appid}", response_model=GameDetail)
def get_game(
    appid: int,
    open_db: DbOpener = Depends(db_opener),
    size_cache: SizeCache = Depends(get_size_cache),
    cache_root: str = Depends(get_cache_root),
) -> GameDetail:
    """Detail for one app, incl. its depot list with shared-depot flags and sizes.

    404 if the appid has no row in ``apps`` (plan §4: apps are created by
    a mapping upsert, either from a prefill run or the manual fallback —
    an appid with no mapping activity yet simply doesn't exist here).
    """
    with open_db() as conn:
        app_row = conn.execute(
            "SELECT appid, name, status, last_prefill_at, needs_force FROM apps "
            "WHERE appid = ?",
            (appid,),
        ).fetchone()
        if app_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown appid {appid}"
            )

        # Single query for the whole depot list, each row carrying its own
        # "does at least one other app also map this depotid" flag via a
        # correlated EXISTS subquery — avoids one extra round-trip per depot
        # (WP 1.3 review nitpick; the previous version ran a COUNT(*) per row).
        depot_rows = conn.execute(
            """
            SELECT d.depotid,
                   EXISTS (
                       SELECT 1 FROM depot_app_map d2
                       WHERE d2.depotid = d.depotid AND d2.appid != d.appid
                   ) AS shared
            FROM depot_app_map d
            WHERE d.appid = ?
            ORDER BY d.depotid
            """,
            (appid,),
        ).fetchall()

    depot_bytes = size_cache.get(cache_root).depot_bytes

    depots = [
        DepotEntry(
            depotid=row["depotid"],
            shared=bool(row["shared"]),
            size_bytes=depot_bytes.get(row["depotid"]),
        )
        for row in depot_rows
    ]

    return GameDetail(
        appid=app_row["appid"],
        name=app_row["name"],
        status=app_row["status"],
        last_prefill_at=app_row["last_prefill_at"],
        depots=depots,
        size_bytes=app_size_bytes([row["depotid"] for row in depot_rows], depot_bytes),
        needs_force=bool(app_row["needs_force"]),
    )
