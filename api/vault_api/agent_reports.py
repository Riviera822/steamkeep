"""Agent report storage and the ADR-0002 server-side diff (WP 2.4).

The contract (plan §3 "vault-agent", ADR-0002 decision 2): the agent is
**stateless and dumb**. It reports the COMPLETE list of installed Steam app
ids every time; vault-api stores that as a full-list snapshot and derives
additions *and removals* by diffing consecutive snapshots per ``client_id``.
No delta logic on the device.

What this module deliberately does NOT do
-----------------------------------------
A removal is **surfaced**, not acted on:

* no cache content is deleted (plan A9 keeps deletion a human/API decision —
  ``DELETE /v1/cache/{appid}``),
* ``apps.status`` is not changed and no ``apps`` row is created,
* nothing is queued.

An agent report is an *observation about a client machine*, not a statement
about the server's cache. Phase 3's scheduler is the component that turns
these snapshots into a prefill set (plan §7 Phase 2, last bullet); until then
the data path just records and reports.

Ordering: the "previous" snapshot is the previous row by SQLite ``rowid``,
i.e. by **insertion order**, not by ``reported_at``. ``reported_at`` has
second precision and comes from the server clock, so two reports inside one
second tie and a clock that steps backwards would reorder the chain. rowid
cannot do either. (Pruning only ever removes the *oldest* rows, so the
maximum rowid is monotonic and is never recycled.)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from vault_api.jobs import immediate_transaction, utcnow_iso

logger = logging.getLogger(__name__)

#: Longest accepted ``client_id`` (characters). A client id is an operator-set
#: label (hostname, "steam-deck", ...), it lands in log lines and in a TEXT
#: primary lookup column — 64 characters is generous for that and keeps a
#: buggy agent from writing kilobyte-long ids.
MAX_CLIENT_ID_LENGTH = 64

#: Upper bound on app ids in one report. The largest real Steam libraries are
#: a few thousand titles; this only stops a broken agent from pushing a
#: multi-megabyte JSON blob into SQLite on every 30-minute tick.
MAX_APPIDS_PER_REPORT = 10_000

#: Hard floor for the retention setting: the diff needs the previous snapshot
#: *plus* the one being written, so fewer than 2 rows per client would make
#: every report look like a first report.
MIN_REPORTS_KEPT = 2

#: Longest accepted source address (schema v9, WP 3.11). An IPv6 address with a
#: zone id fits in 45 characters; 64 leaves room for other peer spellings an
#: ASGI server might report.
MAX_SOURCE_ADDR_LENGTH = 64

#: How many removed/added ids a single log line prints before it summarizes.
_LOG_ID_SAMPLE = 50


@dataclass(frozen=True)
class StoredSnapshot:
    """One ``agent_reports`` row as read back.

    ``appids`` is ``None`` when the stored JSON could not be decoded — see
    ``_decode_appids``.
    """

    rowid: int
    reported_at: str
    appids: list[int] | None


@dataclass(frozen=True)
class ReportResult:
    """Outcome of one ``POST /v1/agent/installed``."""

    client_id: str
    reported_at: str
    #: Number of DISTINCT app ids stored (duplicates in the request collapse).
    received: int
    added: list[int]
    removed: list[int]
    #: True when there was no usable previous snapshot for this client.
    first_report: bool
    #: How many old snapshots retention removed in the same transaction.
    pruned: int


@dataclass(frozen=True)
class ClientSummary:
    """One row of ``GET /v1/clients`` (minimal v1 — see the router)."""

    client_id: str
    #: Oldest RETAINED report's timestamp, not necessarily the true first
    #: contact: retention prunes older snapshots away (see ``prune_reports``).
    first_seen: str
    last_reported_at: str
    #: Size of the latest snapshot; ``None`` if that row's JSON was unreadable.
    app_count: int | None
    #: Every distinct address this client's RETAINED reports arrived from
    #: (schema v9, WP 3.11). Usually one; more than one when the machine
    #: changed address (DHCP lease, wifi vs. cable) within the retained window.
    #: Empty when no retained report recorded an address — which includes every
    #: report written before schema v9, and is the case that must never be read
    #: as "this client is bypassing the cache" (see ``routers/clients.py``).
    source_addrs: list[str] = field(default_factory=list)


def normalize_source_addr(raw: str | None) -> str | None:
    """Validate the peer address of an agent report; ``None`` if unusable.

    This value is the join key between agent reports and event-log lines
    (ADR-0008's client identity story), it is written into log lines, and it is
    returned in an API response. It comes from the ASGI server rather than from
    the request body, so it is not attacker-chosen in any normal deployment —
    but "not normally attacker-chosen" is not a reason to store it unchecked,
    and a ``None`` here is perfectly serviceable (it means "cannot correlate",
    which every consumer already has to handle for pre-v9 rows).

    Bounded, ASCII, printable, whitespace-free — the same shape the event-log
    parser demands of field 3, because a value that cannot appear on that side
    can never correlate with anything on this one.
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value or len(value) > MAX_SOURCE_ADDR_LENGTH:
        return None
    if not value.isascii() or not value.isprintable():
        return None
    if any(character.isspace() for character in value):
        return None
    return value


def normalize_appids(appids: Iterable[int]) -> list[int]:
    """Sorted, de-duplicated app id list — the canonical stored form.

    A snapshot is conceptually a *set* ("what is installed right now"), so the
    stored order carries no information and duplicates in the request body are
    collapsed rather than rejected: a library listing the same app twice is a
    quirk of the reporter, not something the operator can act on.
    """
    return sorted({int(appid) for appid in appids})


def _decode_appids(raw: object, client_id: str, rowid: int) -> list[int] | None:
    """Decode a stored ``appids`` JSON array; ``None`` if the row is unusable.

    A corrupt row (hand-edited database, truncated write) must not wedge the
    endpoint forever — it degrades to "no usable previous snapshot", which
    restarts the diff chain at the next report, and says so loudly in the log.
    """
    if not isinstance(raw, str):
        logger.warning(
            "agent-report client=%r snapshot rowid=%d has a non-text appids "
            "column (%s); ignoring it for the diff",
            client_id, rowid, type(raw).__name__,
        )
        return None
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "agent-report client=%r snapshot rowid=%d holds unparseable JSON; "
            "ignoring it for the diff (the next report restarts the chain)",
            client_id, rowid,
        )
        return None
    if not isinstance(decoded, list):
        logger.warning(
            "agent-report client=%r snapshot rowid=%d holds JSON %s, expected a "
            "list; ignoring it for the diff",
            client_id, rowid, type(decoded).__name__,
        )
        return None
    # bool is an int subclass — exclude it explicitly so a stray `true` does
    # not silently become app id 1.
    return sorted(
        {value for value in decoded if isinstance(value, int) and not isinstance(value, bool)}
    )


def latest_snapshot(conn: sqlite3.Connection, client_id: str) -> StoredSnapshot | None:
    """The client's most recently INSERTED snapshot, or None if it has none.

    Ordered by ``rowid`` (insertion order) rather than ``reported_at`` — see
    the module docstring. ``idx_agent_reports_client_time`` narrows the scan to
    this client's rows; retention keeps that at a handful, so the tiny sort by
    rowid on top of it is free.
    """
    row = conn.execute(
        """
        SELECT rowid AS rowid, reported_at, appids
        FROM agent_reports
        WHERE client_id = ?
        ORDER BY rowid DESC
        LIMIT 1
        """,
        (client_id,),
    ).fetchone()
    if row is None:
        return None
    rowid = int(row["rowid"])
    return StoredSnapshot(
        rowid=rowid,
        reported_at=str(row["reported_at"]),
        appids=_decode_appids(row["appids"], client_id, rowid),
    )


def prune_reports(conn: sqlite3.Connection, client_id: str, keep: int) -> int:
    """Keep only this client's ``keep`` newest snapshots. Returns rows removed.

    Called inside the insert transaction, so a client that reports every 30
    minutes forever holds a bounded number of rows instead of growing the table
    without limit (the retention gap the WP 1.2 review left open).

    Only the *oldest* rows go, which is what keeps the newest rowid monotonic
    and the diff chain intact. ``keep`` is clamped to ``MIN_REPORTS_KEPT``
    defensively — the config layer already enforces it, and a caller passing 1
    would silently turn every report into a first report.
    """
    keep = max(MIN_REPORTS_KEPT, int(keep))
    cursor = conn.execute(
        """
        DELETE FROM agent_reports
        WHERE client_id = ?
          AND rowid NOT IN (
              SELECT rowid FROM agent_reports
              WHERE client_id = ?
              ORDER BY rowid DESC
              LIMIT ?
          )
        """,
        (client_id, client_id, keep),
    )
    return int(cursor.rowcount or 0)


def _sample(ids: Sequence[int]) -> str:
    """Render an id list for a log line without dumping thousands of numbers."""
    if len(ids) <= _LOG_ID_SAMPLE:
        return str(list(ids))
    return f"{list(ids[:_LOG_ID_SAMPLE])} (+{len(ids) - _LOG_ID_SAMPLE} more)"


def store_report(
    conn: sqlite3.Connection,
    client_id: str,
    appids: Sequence[int],
    keep: int,
    source_addr: str | None = None,
) -> ReportResult:
    """Store one full-list snapshot and diff it against the previous one.

    Everything that must not interleave happens inside ONE
    ``BEGIN IMMEDIATE`` transaction (``jobs.immediate_transaction``): read the
    previous snapshot, insert the new one, prune. Without that write lock two
    reports from the same client arriving together would both read the same
    "previous" row and both report the same additions — the diff chain would
    fork. With it they serialize: the loser sees the winner's snapshot as its
    predecessor, so every snapshot is diffed against exactly one other,
    whichever order the two land in.

    The removals are logged (audit) and returned. Nothing else happens to
    them — see the module docstring.
    """
    stored = normalize_appids(appids)
    payload = json.dumps(stored, separators=(",", ":"))
    reported_at = utcnow_iso()
    # Schema v9 (WP 3.11): recorded, never required. An unusable or absent peer
    # address stores NULL and the report itself is completely unaffected —
    # correlation is a nice-to-have on top of the report, not a precondition
    # for accepting one.
    stored_addr = normalize_source_addr(source_addr)

    with immediate_transaction(conn):
        previous = latest_snapshot(conn, client_id)
        conn.execute(
            "INSERT INTO agent_reports (client_id, reported_at, appids, source_addr) "
            "VALUES (?, ?, ?, ?)",
            (client_id, reported_at, payload, stored_addr),
        )
        pruned = prune_reports(conn, client_id, keep)

    previous_ids = None if previous is None else previous.appids
    first_report = previous_ids is None
    if first_report:
        added = list(stored)
        removed: list[int] = []
    else:
        current_set = set(stored)
        previous_set = set(previous_ids or ())
        added = sorted(current_set - previous_set)
        removed = sorted(previous_set - current_set)

    logger.info(
        "agent-report client=%r stored snapshot: reported_at=%s apps=%d "
        "first_report=%s added=%d removed=%d pruned=%d",
        client_id, reported_at, len(stored), first_report,
        len(added), len(removed), pruned,
    )
    if removed:
        # Audit line, deliberately separate and explicit about the boundary:
        # this is the one event an operator may want to grep for, and the one
        # place where somebody might expect vault-api to "clean up" by itself.
        # ASCII only, deliberately: this line is meant to be grepped out of a
        # console/json-file log, and a Windows console at codepage 850 renders
        # an em dash as a replacement character (observed during verification).
        logger.info(
            "agent-report client=%r REMOVED %d app(s) from the client library: "
            "%s - cache content is NOT deleted and apps.status is NOT changed "
            "(ADR-0002: removals are surfaced; deletion stays a human/API "
            "decision, plan A9)",
            client_id, len(removed), _sample(removed),
        )

    return ReportResult(
        client_id=client_id,
        reported_at=reported_at,
        received=len(stored),
        added=added,
        removed=removed,
        first_report=first_report,
        pruned=pruned,
    )


def list_clients(conn: sqlite3.Connection) -> list[ClientSummary]:
    """Every client that has ever reported (and still has a retained row).

    Two steps rather than one clever query: a GROUP BY for ``first_seen``, then
    ``latest_snapshot`` per client for the current app count and timestamp.
    The follow-up query runs once per *gaming machine* — a homelab has a
    handful — and it keeps ``last_reported_at`` and ``app_count`` derived from
    the same row, which a ``MAX(reported_at)`` aggregate would not guarantee
    (see the rowid-vs-timestamp note in the module docstring).
    """
    rows = conn.execute(
        """
        SELECT client_id, MIN(reported_at) AS first_seen
        FROM agent_reports
        GROUP BY client_id
        ORDER BY client_id
        """
    ).fetchall()

    summaries: list[ClientSummary] = []
    for row in rows:
        client_id = str(row["client_id"])
        latest = latest_snapshot(conn, client_id)
        if latest is None:  # pragma: no cover - GROUP BY proves a row exists
            continue
        summaries.append(
            ClientSummary(
                client_id=client_id,
                first_seen=str(row["first_seen"]),
                last_reported_at=latest.reported_at,
                app_count=None if latest.appids is None else len(latest.appids),
                source_addrs=source_addrs_for(conn, client_id),
            )
        )
    return summaries


def source_addrs_for(conn: sqlite3.Connection, client_id: str) -> list[str]:
    """Distinct source addresses across this client's RETAINED reports (v9).

    Every retained report, not only the newest: a laptop that moved from wifi
    to cable an hour ago still has cache-log traffic under the old address, and
    dropping it would make the machine look half-silent. Retention bounds the
    set naturally — it can never hold more addresses than
    ``VAULT_AGENT_REPORT_KEEP`` rows.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT source_addr FROM agent_reports
        WHERE client_id = ? AND source_addr IS NOT NULL
        ORDER BY source_addr
        """,
        (client_id,),
    ).fetchall()
    return [str(row["source_addr"]) for row in rows]
