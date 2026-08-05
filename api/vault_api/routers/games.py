"""Games endpoints (plan §6): GET /v1/games, GET /v1/games/{appid}.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth" section) — every route added here is authenticated
automatically.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from vault_api.auth import require_api_key
from vault_api.deps import get_db

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["games"])


class GameSummary(BaseModel):
    appid: int
    name: str | None
    status: str
    last_prefill_at: str | None
    depot_count: int
    # TODO(WP 1.5): populate from the per-app size calculation (du over
    # depot folders, cached). Always null until then — documented gap,
    # not a guessed value.
    size_bytes: int | None = None


class DepotEntry(BaseModel):
    depotid: int
    # True if this depot is also mapped to at least one other app (plan
    # §4 shared-depot semantics: shared depots are skipped on deletion).
    shared: bool


class GameDetail(BaseModel):
    appid: int
    name: str | None
    status: str
    last_prefill_at: str | None
    depots: list[DepotEntry]
    # TODO(WP 1.5): see GameSummary.size_bytes.
    size_bytes: int | None = None


@router.get("/v1/games", response_model=list[GameSummary])
def list_games(conn: sqlite3.Connection = Depends(get_db)) -> list[GameSummary]:
    """All tracked apps with their depot count (plan §6)."""
    rows = conn.execute(
        """
        SELECT a.appid, a.name, a.status, a.last_prefill_at,
               COUNT(d.depotid) AS depot_count
        FROM apps a
        LEFT JOIN depot_app_map d ON d.appid = a.appid
        GROUP BY a.appid
        ORDER BY a.appid
        """
    ).fetchall()
    return [
        GameSummary(
            appid=row["appid"],
            name=row["name"],
            status=row["status"],
            last_prefill_at=row["last_prefill_at"],
            depot_count=row["depot_count"],
        )
        for row in rows
    ]


@router.get("/v1/games/{appid}", response_model=GameDetail)
def get_game(appid: int, conn: sqlite3.Connection = Depends(get_db)) -> GameDetail:
    """Detail for one app, incl. its depot list with shared-depot flags.

    404 if the appid has no row in ``apps`` (plan §4: apps are created by
    a mapping upsert, either from a prefill run or the manual fallback —
    an appid with no mapping activity yet simply doesn't exist here).
    """
    app_row = conn.execute(
        "SELECT appid, name, status, last_prefill_at FROM apps WHERE appid = ?",
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

    depots = [
        DepotEntry(depotid=row["depotid"], shared=bool(row["shared"]))
        for row in depot_rows
    ]

    return GameDetail(
        appid=app_row["appid"],
        name=app_row["name"],
        status=app_row["status"],
        last_prefill_at=app_row["last_prefill_at"],
        depots=depots,
    )
