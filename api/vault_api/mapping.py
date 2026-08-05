"""Depot->app mapping write path (plan §4).

``upsert_mapping`` is the single hook both write paths go through:

- the manual fallback endpoint ``PUT /v1/mapping/{depotid}``
  (routers/mapping.py) for edge cases (delisted games, depots SteamPrefill
  doesn't recognize);
- WP 1.4's prefill flow, which calls it directly (no HTTP round-trip)
  since SteamPrefill already knows the depot->app mapping at download time
  (plan §4: "vault-api keeps its own mapping table ... and updates it
  during prefill").

Mapping is deliberately ADDITIVE: ``PUT``-ing the same depotid again under
a different appid does not replace the old mapping, it adds a second one
(a depot legitimately belonging to two tracked apps is exactly plan §4's
shared-depot case, e.g. redistributables). ``delete_mapping`` is the
correction path for a genuine mistake (fat-fingered appid) — it removes
one specific ``(depotid, appid)`` pair and nothing else (WP 1.3 review,
B2).

Plain sqlite3, no ORM — a handful of explicit statements is simpler and
more readable here than an upsert-via-``ON CONFLICT`` one-liner.
"""

from __future__ import annotations

import sqlite3


def upsert_mapping(
    conn: sqlite3.Connection, depotid: int, appid: int, name: str | None
) -> None:
    """Record that ``depotid`` belongs to ``appid``. Idempotent.

    - Creates the ``apps`` row if it doesn't exist yet, with status
      ``'idle'`` (plan §4/§6: a fresh mapping doesn't imply anything has
      been prefilled).
    - If the app row already exists and ``name`` is given, updates the
      stored name (covers the case where an earlier mapping call created
      the row with ``name=None``). A ``None`` name never overwrites an
      existing name.
    - Inserts into ``depot_app_map`` with ``INSERT OR IGNORE`` — calling
      this twice with the same ``(depotid, appid)`` pair is a no-op the
      second time, which is required since the same pair can legitimately
      be reported repeatedly (repeated prefill runs, repeated manual
      fallback calls).

    Commits internally: this function is the complete write unit for a
    single mapping fact, called either per-HTTP-request or per-depot
    during a prefill job (WP 1.4) — neither caller needs a shared
    transaction across multiple mapping facts.
    """
    existing = conn.execute(
        "SELECT name FROM apps WHERE appid = ?", (appid,)
    ).fetchone()

    if existing is None:
        conn.execute(
            "INSERT INTO apps (appid, name, status) VALUES (?, ?, 'idle')",
            (appid, name),
        )
    elif name is not None and existing["name"] != name:
        conn.execute("UPDATE apps SET name = ? WHERE appid = ?", (name, appid))

    conn.execute(
        "INSERT OR IGNORE INTO depot_app_map (depotid, appid) VALUES (?, ?)",
        (depotid, appid),
    )
    conn.commit()


def delete_mapping(conn: sqlite3.Connection, depotid: int, appid: int) -> bool:
    """Remove exactly one ``(depotid, appid)`` mapping pair. Returns whether
    a row was actually deleted (``False`` if the pair didn't exist).

    Deliberately does NOT touch the ``apps`` row — this is a mapping
    correction (fat-fingered appid, wrong depotid), not a "forget this
    game" operation; deletion of an app itself is a later work package's
    scope (plan §4/§6: ``DELETE /v1/cache/{appid}``).
    """
    cursor = conn.execute(
        "DELETE FROM depot_app_map WHERE depotid = ? AND appid = ?",
        (depotid, appid),
    )
    conn.commit()
    return cursor.rowcount > 0
