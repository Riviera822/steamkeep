"""Depot->app mapping endpoints — the manual fallback path (plan §4).

The primary mapping source is SteamPrefill during a prefill run (WP 1.4
calls ``vault_api.mapping.upsert_mapping`` directly, no HTTP involved).
These routes exist for the documented edge case: delisted games or depots
SteamPrefill doesn't recognize. Kept minimal on purpose — this is not a
general CRUD surface.

Mapping is ADDITIVE (WP 1.3 review, B2): re-``PUT``-ing an existing depotid
under a different appid adds a second mapping row rather than replacing
the first — see ``vault_api/mapping.py`` module docstring for why (it's
plan §4's shared-depot case, not a bug). Corrections go through
``DELETE /v1/mapping/{depotid}/{appid}``.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth" section) — every route added here is authenticated
automatically.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field

from vault_api.auth import require_api_key
from vault_api.deps import get_db
from vault_api.mapping import delete_mapping, upsert_mapping

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["mapping"])


class MappingUpsertRequest(BaseModel):
    # extra="forbid": this is a human-driven fallback endpoint (plan §4
    # edge cases) — a typo'd field name (e.g. "appId") must 422, not
    # silently upsert with app_name defaulting to None.
    model_config = ConfigDict(extra="forbid")

    # Steam appids are positive; ge=1 rejects 0/negative junk that would
    # otherwise sit in the table permanently (no delete-by-appid exists
    # yet — WP 1.3 review, should-fix).
    appid: int = Field(ge=1)
    app_name: str | None = None


class MappingUpsertResponse(BaseModel):
    depotid: int
    appid: int


class MappingEntry(BaseModel):
    depotid: int
    appid: int


@router.put("/v1/mapping/{depotid}", response_model=MappingUpsertResponse)
def put_mapping(
    depotid: int = Path(ge=1),
    body: MappingUpsertRequest = ...,
    conn: sqlite3.Connection = Depends(get_db),
) -> MappingUpsertResponse:
    """Upsert a single depot->app mapping fact (additive, manual fallback, plan §4)."""
    upsert_mapping(conn, depotid=depotid, appid=body.appid, name=body.app_name)
    return MappingUpsertResponse(depotid=depotid, appid=body.appid)


@router.get("/v1/mapping", response_model=list[MappingEntry])
def list_mapping(conn: sqlite3.Connection = Depends(get_db)) -> list[MappingEntry]:
    """Full depot->app mapping table, for inspection/debugging."""
    rows = conn.execute(
        "SELECT depotid, appid FROM depot_app_map ORDER BY depotid, appid"
    ).fetchall()
    return [MappingEntry(depotid=row["depotid"], appid=row["appid"]) for row in rows]


@router.delete(
    "/v1/mapping/{depotid}/{appid}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def remove_mapping(
    depotid: int = Path(ge=1),
    appid: int = Path(ge=1),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    """Remove one mapping pair (correction path for the additive PUT, B2).

    Does NOT delete the ``apps`` row. 404 if the pair doesn't exist.
    """
    deleted = delete_mapping(conn, depotid=depotid, appid=appid)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No mapping for depotid {depotid} -> appid {appid}",
        )
