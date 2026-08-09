"""Client endpoint (plan §6): ``GET /v1/clients``.

Plan §6 describes this row as "per-client hit stats incl. bypass warnings".
WP 2.4 shipped the agent-report half of that object; **WP 3.11 (ADR-0008) adds
the promised cache-side fields** now that vault-api sweeps vault-core's
structured event log:

    {"client_id", "first_seen", "last_reported_at", "app_count",
     "source_addrs", "cache_hits", "cache_misses", "bytes_served",
     "last_seen_in_cache_log", "bypass_suspected"}

The WP 2.4 shape was chosen to be forward-compatible — a flat object per
client — and that held: every new field sits next to the old ones and nothing
was restructured.

How a client is correlated with cache traffic
---------------------------------------------
The event log knows **addresses**; agent reports know **client_ids**. Schema
v9's ``agent_reports.source_addr`` is the only bridge: each report records the
address it arrived from, and a client's statistics are the sum over every
address its retained reports came from (``agent_reports.source_addrs_for``).

Bypass detection, and why it fails toward NOT accusing
------------------------------------------------------
``bypass_suspected`` answers plan §5's actual pain point: a machine that
reports installed games but never appears at the cache is probably resolving
Steam's CDN around vault-dns (IPv6 leak, hardcoded DNS, a VPN). A false
positive here sends an operator hunting a network fault that does not exist,
so **every unknown reads as "not suspected"**. The rule is a chain of
disqualifications, and only a client that survives all of them is flagged:

1. the event feed is off (``VAULT_EVENT_LOG_PATH`` unset) — there is no cache
   log to be absent from;
2. no sweep has ever completed, or the feed is younger than
   ``VAULT_BYPASS_WINDOW_DAYS`` — "we have not been watching long enough" is
   not evidence about the client (``event_sweep.feed_is_young``);
3. the client's own report is older than the window — a machine that has been
   off cannot be bypassing anything, and would otherwise be accused forever;
4. the client reports **no** installed games (or its snapshot was unreadable) —
   nothing to download, so nothing to download around;
5. no retained report recorded a source address — including every report
   written before schema v9 — so the client cannot be correlated at all;
6. the client HAS appeared in the cache log within the window.

Only then: reporting, recently, with games, from a known address, and with
zero cache-log presence in the window ⇒ ``true``.

ADR-0001's production requirement 7 is why the default window is 3 days rather
than 1: Steam LAN peer-to-peer transfers can legitimately replace cache
traffic, so a single quiet day proves nothing.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth").
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from vault_api import agent_reports, event_sweep
from vault_api.auth import require_api_key
from vault_api.config import Settings
from vault_api.deps import DbOpener, db_opener
from vault_api.jobs import to_utc_iso

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
    #: Addresses this client's retained reports arrived from (schema v9). The
    #: keys its cache statistics are summed over. Empty for reports stored
    #: before schema v9, which is why such a client is never bypass_suspected.
    source_addrs: list[str]
    #: Cache HITs served to this client, summed over the RETAINED statistics
    #: windows (VAULT_CLIENT_STATS_KEEP). Not a lifetime counter — old windows
    #: are pruned, so this number can go down. 0 when the event feed is off.
    cache_hits: int
    #: Cache MISSes, same retention caveat. hits/(hits+misses) is the hit rate.
    cache_misses: int
    #: Bytes vault-core actually delivered to this client (2xx responses only),
    #: same retention caveat.
    bytes_served: int
    #: Newest event-log timestamp for any of this client's addresses; ``null``
    #: when it has never appeared in the cache log (or the feed is off).
    last_seen_in_cache_log: str | None
    #: Reports installed games but has no cache-log presence within
    #: VAULT_BYPASS_WINDOW_DAYS. Fails toward ``false`` on every unknown — see
    #: the module docstring for the full disqualification chain.
    bypass_suspected: bool


@router.get("/v1/clients", response_model=list[ClientOut])
def list_clients(
    request: Request,
    open_db: DbOpener = Depends(db_opener),
) -> list[ClientOut]:
    """Every client that has reported installed apps, by ``client_id``."""
    settings: Settings = request.app.state.settings
    now = datetime.now(timezone.utc)
    cutoff_iso = to_utc_iso(now - timedelta(days=settings.bypass_window_days))

    with open_db() as conn:
        summaries = agent_reports.list_clients(conn)
        state = event_sweep.read_state(conn)
        totals = {
            summary.client_id: event_sweep.totals_for_addrs(conn, summary.source_addrs)
            for summary in summaries
        }

    # Computed once for every client rather than per row: whether the feed can
    # support an accusation at all is a property of the FEED, not of a client.
    feed_can_accuse = settings.event_sweep_enabled and not event_sweep.feed_is_young(
        state, settings, now
    )

    return [
        ClientOut(
            client_id=summary.client_id,
            first_seen=summary.first_seen,
            last_reported_at=summary.last_reported_at,
            app_count=summary.app_count,
            source_addrs=summary.source_addrs,
            cache_hits=totals[summary.client_id].hits,
            cache_misses=totals[summary.client_id].misses,
            bytes_served=totals[summary.client_id].bytes_served,
            last_seen_in_cache_log=totals[summary.client_id].last_seen,
            bypass_suspected=_bypass_suspected(
                summary,
                totals[summary.client_id],
                feed_can_accuse=feed_can_accuse,
                cutoff_iso=cutoff_iso,
            ),
        )
        for summary in summaries
    ]


def _bypass_suspected(
    summary: agent_reports.ClientSummary,
    totals: event_sweep.AddrTotals,
    *,
    feed_can_accuse: bool,
    cutoff_iso: str,
) -> bool:
    """The disqualification chain from the module docstring, in order.

    Written as early returns rather than one boolean expression on purpose:
    each ``return False`` is a distinct reason a client is NOT accused, and
    each one is separately mutation-tested (flip it and a named test dies) per
    docs/LEARNINGS.md's rule about pinning fail-safe DEFAULTS, not just the
    happy path.
    """
    # 1 + 2: the feed is off, has never swept, or is younger than the window.
    if not feed_can_accuse:
        return False
    # 3: the client itself has been silent longer than the window.
    if summary.last_reported_at < cutoff_iso:
        return False
    # 4: nothing installed (or an unreadable snapshot) means nothing to bypass.
    if not summary.app_count:
        return False
    # 5: no address on any retained report — correlation is impossible.
    if not summary.source_addrs:
        return False
    # 6: it HAS been seen at the cache within the window.
    if totals.last_seen is not None and totals.last_seen >= cutoff_iso:
        return False
    return True
