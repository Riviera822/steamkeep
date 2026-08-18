"""Games endpoints (plan §6): GET /v1/games, GET /v1/games/{appid}.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth" section) — every route added here is authenticated
automatically.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from vault_api import depot_manifests
from vault_api.auth import require_api_key
from vault_api.deps import DbOpener, db_opener, get_cache_root, get_size_cache
from vault_api.sizes import SizeCache, app_size_bytes

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["games"])


class GameSummary(BaseModel):
    appid: int
    name: str | None
    status: str
    last_prefill_at: str | None
    # Timestamp of the last run that CONFIRMED this app current (schema v4,
    # WP 3.3; surfaced here WP 4c). ADR-0006's tier-1 semantics are "current
    # as of <timestamp>", which is only honest once the timestamp is
    # visible. Same null-when-never / verbatim-string style as
    # last_prefill_at (see ``jobs.TIMESTAMP_FORMAT``) -- but written on a
    # MUCH narrower set of outcomes: only a run whose parsed summary was
    # ``Updated == 0 AND Up To Date > 0`` sets it (api/README.md's "Job
    # outcome honesty" table has the full outcome matrix). In particular an
    # ordinary run that actually changed depots (``Updated > 0``), an
    # unparseable summary, an unowned-app run, a failed/cancelled/paused
    # run, and vault-api's own crash-recovery path (``recover_stale_jobs``)
    # all leave it untouched -- so a game can be ``status: "done"`` with
    # this field still null.
    #
    # One place the two fields DIVERGE: ``DELETE /v1/cache/{appid}``
    # (``deletion.reset_app_after_deletion``) unconditionally nulls
    # ``last_prefill_at`` on delete, but deliberately leaves
    # ``last_manifest_check`` alone -- it is not a false claim (the app WAS
    # confirmed current at that timestamp), and ``needs_force``/``status``
    # are what actually gate whether the cache is trusted, not this field.
    # So a game with zero cached bytes can still show a non-null
    # ``last_manifest_check`` from before the deletion.
    last_manifest_check: str | None
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
    # Phase 4h / WP 4h.1: whether this game's manifest has EVER been observed
    # to change since this vault started watching it, derived from
    # depot_manifests (vault_api/depot_manifests.py's "Change frequency"
    # section has the full definition and reasoning). FOUR possible states:
    #   null                -> no manifest data recorded for this app at all
    #                          (never prefilled since this feature existed,
    #                          or mapped only via the manual fallback).
    #   "insufficient_data"  -> SOME data exists, but either fewer than 2
    #                          observations were recorded for at least one of
    #                          this app's depots, or the app's youngest-
    #                          observed depot has been watched for less than
    #                          14 days. Never "stable" -- a short window or a
    #                          single observation is not evidence of either
    #                          stability or change.
    #   "stable"             -> enough data, and no observed manifest change.
    #   "changed"            -> enough data, and at least one depot's
    #                          manifest has been seen to change AT LEAST ONCE.
    # `null` and `"insufficient_data"` are DELIBERATELY distinct states
    # (pin 2): "we have never looked" and "we looked, but not long/often
    # enough to say" are different honest answers, not the same "unknown".
    #
    # THIS IS NOT A FREQUENCY, despite the field's name (kept for continuity
    # with the plan section's own wording) -- "changed" only means "at least
    # once, ever", nothing decays, so a game that changed three years ago and
    # one that changes weekly are indistinguishable from this field alone.
    # See manifest_days_since_last_change below for the one rate-adjacent
    # fact depot_manifests can actually support.
    manifest_change_frequency: str | None = None
    # Days since the YOUNGEST-observed depot's first recorded observation
    # (the conservative bound across this app's depots -- see
    # depot_manifests.py). Null only alongside manifest_change_frequency
    # being null (no data at all); populated even when the category is
    # "insufficient_data" so a frontend can render the actual number ("only
    # observed for 3 days") instead of a bare label with nothing behind it.
    manifest_observation_days: int | None = None
    # Days since the MOST RECENTLY observed manifest change across this
    # app's own depots. Populated ONLY when manifest_change_frequency ==
    # "changed" -- null for "stable" (never observed one), "insufficient_data"
    # and null (never earned the right to say). This is the field that comes
    # closest to the plan section's "changes every three days" style
    # statement -- "last changed N days ago" is a supportable claim; a true
    # cadence is not (depot_manifests only ever stores the LATEST manifest
    # per depot, never a change history).
    manifest_days_since_last_change: int | None = None


class DepotEntry(BaseModel):
    depotid: int
    # True if this depot is also mapped to at least one other app (plan §4
    # shared-depot semantics). NOT the same as "never deleted" any more
    # (ADR-0003 addendum, WP 3.5): a shared=true depot whose every OTHER
    # mapped app currently has no cache content is deleted anyway as a "last
    # cached remnant" — see api/README.md "Last cached remnants".
    shared: bool
    # Bytes on disk for this one depot (WP 1.5), null if never written yet.
    size_bytes: int | None = None


class GameDetail(BaseModel):
    appid: int
    name: str | None
    status: str
    last_prefill_at: str | None
    # See GameSummary.last_manifest_check.
    last_manifest_check: str | None
    depots: list[DepotEntry]
    # See GameSummary.size_bytes.
    size_bytes: int | None = None
    # See GameSummary.needs_force.
    needs_force: bool
    # See GameSummary.manifest_change_frequency / .manifest_observation_days /
    # .manifest_days_since_last_change.
    manifest_change_frequency: str | None = None
    manifest_observation_days: int | None = None
    manifest_days_since_last_change: int | None = None


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
            SELECT a.appid, a.name, a.status, a.last_prefill_at,
                   a.last_manifest_check, a.needs_force,
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
        # One query for every app's change-frequency rows, grouped in Python
        # (vault_api.depot_manifests.change_frequency_by_app) -- same
        # avoid-the-N+1 shape as app_depotids/depot_bytes below, not a
        # per-app query in this loop.
        frequencies = depot_manifests.change_frequency_by_app(conn)

    app_depotids: dict[int, list[int]] = {}
    for row in depot_rows:
        app_depotids.setdefault(int(row["appid"]), []).append(int(row["depotid"]))

    depot_bytes = size_cache.get(cache_root).depot_bytes
    no_manifest_data = depot_manifests.ChangeFrequency(None, None)

    games = []
    for row in rows:
        frequency = frequencies.get(row["appid"], no_manifest_data)
        games.append(
            GameSummary(
                appid=row["appid"],
                name=row["name"],
                status=row["status"],
                last_prefill_at=row["last_prefill_at"],
                last_manifest_check=row["last_manifest_check"],
                depot_count=row["depot_count"],
                size_bytes=app_size_bytes(app_depotids.get(row["appid"], []), depot_bytes),
                needs_force=bool(row["needs_force"]),
                manifest_change_frequency=frequency.category,
                manifest_observation_days=frequency.observation_days,
                manifest_days_since_last_change=frequency.days_since_last_change,
            )
        )
    return games


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
            "SELECT appid, name, status, last_prefill_at, last_manifest_check, "
            "needs_force FROM apps WHERE appid = ?",
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
        frequency = depot_manifests.change_frequency_for_app(conn, appid)

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
        last_manifest_check=app_row["last_manifest_check"],
        depots=depots,
        size_bytes=app_size_bytes([row["depotid"] for row in depot_rows], depot_bytes),
        needs_force=bool(app_row["needs_force"]),
        manifest_change_frequency=frequency.category,
        manifest_observation_days=frequency.observation_days,
        manifest_days_since_last_change=frequency.days_since_last_change,
    )
