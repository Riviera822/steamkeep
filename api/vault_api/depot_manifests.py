"""``depot_manifests`` table writes/reads (WP 3.2, schema v3).

ADR-0006 decision 3: vault-api remembers manifest state per depot from
SteamPrefill's own temp-cache filenames, so a future staleness comparison and
manifest-diff garbage collection (ADR-0007) have something durable to read —
the temp cache itself does NOT survive SteamPrefill's ``clear-temp`` (research
doc, Q1), which is exactly why ``vault_api.manifest_archive`` copies the
source ``.bin`` out before that can happen.

**Latest-per-(appid, depotid), not a history table.** A newer ingest for the
same ``(appid, depotid)`` pair *replaces* the row — see ``upsert_depot_manifest``
— rather than adding a second one, because nothing downstream (GC's keep-set,
the staleness comparison) ever needs "every manifest ever observed for this
app/depot", only "what does vault-api currently believe it is right now".

Plain sqlite3, no ORM — mirrors ``vault_api/mapping.py``'s shape: one
commit-internally write function per fact, one small read helper for tests
and future callers.
"""

from __future__ import annotations

import sqlite3


def upsert_depot_manifest(
    conn: sqlite3.Connection,
    *,
    appid: int,
    containing_appid: int | None,
    depotid: int,
    manifestid: str,
    chunk_count: int,
    total_bytes: int,
    recorded_at: str,
    source: str,
) -> None:
    """Replace the stored manifest state for ``(appid, depotid)``.

    ``INSERT ... ON CONFLICT (appid, depotid) DO UPDATE`` rather than a
    ``DELETE`` followed by an ``INSERT``: a reader (a future GC pass reading
    concurrently) never observes a momentary gap where the row is briefly
    absent between the two statements.

    ``manifestid`` is a **string** (schema v3, ``db.py``): Steam manifest ids
    are unsigned 64-bit and SQLite's ``INTEGER`` storage is signed 64-bit, so
    a real id above ``2**63 - 1`` would silently need TEXT anyway — this
    module never does arithmetic on it, only equality/lookup, so storing it
    as TEXT unconditionally costs nothing and never overflows.

    Commits internally — the same "one write function is the complete write
    unit for one fact" pattern as ``mapping.upsert_mapping``.
    """
    conn.execute(
        """
        INSERT INTO depot_manifests
            (appid, containing_appid, depotid, manifestid, chunk_count,
             total_bytes, recorded_at, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (appid, depotid) DO UPDATE SET
            containing_appid = excluded.containing_appid,
            manifestid        = excluded.manifestid,
            chunk_count       = excluded.chunk_count,
            total_bytes       = excluded.total_bytes,
            recorded_at       = excluded.recorded_at,
            source            = excluded.source
        """,
        (
            appid,
            containing_appid,
            depotid,
            manifestid,
            chunk_count,
            total_bytes,
            recorded_at,
            source,
        ),
    )
    conn.commit()


def get_depot_manifest(
    conn: sqlite3.Connection, *, appid: int, depotid: int
) -> dict[str, object] | None:
    """The stored manifest state for one ``(appid, depotid)`` pair, or ``None``."""
    row = conn.execute(
        """
        SELECT appid, containing_appid, depotid, manifestid, chunk_count,
               total_bytes, recorded_at, source
        FROM depot_manifests
        WHERE appid = ? AND depotid = ?
        """,
        (appid, depotid),
    ).fetchone()
    return None if row is None else {key: row[key] for key in row.keys()}


def list_depot_manifests(
    conn: sqlite3.Connection, *, appid: int | None = None
) -> list[dict[str, object]]:
    """All stored rows, optionally filtered to one ``appid`` (tests / future
    callers — no HTTP endpoint reads this in WP 3.2's scope)."""
    if appid is None:
        rows = conn.execute(
            """
            SELECT appid, containing_appid, depotid, manifestid, chunk_count,
                   total_bytes, recorded_at, source
            FROM depot_manifests
            ORDER BY appid, depotid
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT appid, containing_appid, depotid, manifestid, chunk_count,
                   total_bytes, recorded_at, source
            FROM depot_manifests
            WHERE appid = ?
            ORDER BY depotid
            """,
            (appid,),
        ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]
