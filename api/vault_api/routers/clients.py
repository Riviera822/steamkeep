"""Client endpoint (plan §6): ``GET /v1/clients`` — minimal v1 (WP 2.4).

Plan §6 describes this row as "per-client hit stats incl. bypass warnings".
Hit statistics and bypass detection are **Phase 3** (plan §7 Phase 3, and
plan §5's bypass pain point): they need vault-core's access log, which does
not feed vault-api yet. What exists today is the agent-report side of the same
object, so this ships that and nothing more:

    {"client_id": ..., "first_seen": ..., "last_reported_at": ..., "app_count": N}

The shape is chosen to stay **forward-compatible**: a flat object per client,
so Phase 3 adds fields (``cache_hits``, ``last_seen_in_cache_log``,
``bypass_suspected``, ...) next to these instead of restructuring the response.
A client that only reads the fields it knows keeps working.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth").
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from vault_api import agent_reports
from vault_api.auth import require_api_key
from vault_api.deps import DbOpener, db_opener

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["clients"])


class ClientOut(BaseModel):
    client_id: str
    #: Oldest RETAINED report. Retention (VAULT_AGENT_REPORT_KEEP) prunes older
    #: snapshots, so on a long-running client this moves forward over time — it
    #: is not a permanent "first contact" record. See api/README.md.
    first_seen: str
    last_reported_at: str
    #: Size of the client's latest snapshot. ``null`` only if that stored row's
    #: JSON was unreadable (corrupt/hand-edited database; logged at WARNING).
    app_count: int | None


@router.get("/v1/clients", response_model=list[ClientOut])
def list_clients(open_db: DbOpener = Depends(db_opener)) -> list[ClientOut]:
    """Every client that has reported installed apps, by ``client_id``.

    Phase 3 extends this row with hit statistics and bypass warnings (plan §5,
    §6) — this v1 answers only what the agent reports prove.
    """
    with open_db() as conn:
        summaries = agent_reports.list_clients(conn)
    return [
        ClientOut(
            client_id=summary.client_id,
            first_seen=summary.first_seen,
            last_reported_at=summary.last_reported_at,
            app_count=summary.app_count,
        )
        for summary in summaries
    ]
