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

## Change frequency (WP 4h.1, schema v14)

Phase 4h's decision-support panel wants to say "changes every few days" vs.
"unchanged for two years" per app. The honest starting point is that
**"latest-per-(appid, depotid), not a history table" (above) means this
module has never recorded a change EVENT — only the current snapshot.** Three
columns exist to make a bounded, honest signal possible without turning this
table into the history table its own design deliberately avoids being:

- ``first_seen_at`` — written once, at INSERT, never touched by the
  ``ON CONFLICT ... DO UPDATE`` afterwards. The observation window's anchor:
  "since when do we have ANY manifest data for this depot".
- ``manifest_changed_at`` — starts equal to ``first_seen_at`` and only
  advances to the new ``recorded_at`` when a re-ingest's ``manifestid``
  actually differs from what was stored. A confirmed-current, non-forced run
  (ADR-0006 tier 1) re-ingests the SAME manifest and must not look like a
  change just because vault-api looked again.
- ``observation_count`` — increments on every ingest, changed or not. This is
  what makes **pin 2 (a game with exactly one observation)** answerable at
  all: without a count, a depot ingested once a year ago and never touched
  since would look identical — same ``first_seen_at``, same
  ``manifest_changed_at``, a full year of "window" — to a depot genuinely
  re-observed dozens of times over that year and found stable every time.
  Only the count tells those two apart, and the former is "we looked once and
  never came back", not "stable".

``change_frequency_for_app``/``change_frequency_by_app`` turn these three
columns into the two-pin-honest per-app answer ``routers/games.py`` exposes
as ``GameSummary.manifest_change_frequency`` /
``.manifest_observation_days`` / ``.manifest_days_since_last_change``. See
those functions' docstrings for the exact categorisation and **why every
threshold errs toward "insufficient_data" rather than toward a confident
claim**.

**Reviewer-authorised correction (post-implementation): this is NOT a rate.**
The category this module computes answers "has this app's manifest been
observed to change at least once since we started watching", full stop —
nothing decays, and an app that changed once three years ago and an app that
changes weekly are BOTH ``CATEGORY_CHANGED`` under this definition, with no
way to tell them apart from the category alone. A rate genuinely is not
supportable from a table that only ever stores the LATEST manifest per
depot (see "Latest-per-(appid, depotid)" above) — there is no event log to
compute a cadence from. What IS supportable, cheaply, from the columns
already described: **days since the most recently observed change**
(``ChangeFrequency.days_since_last_change``), which is what actually lets a
frontend say something closer to the plan's own example statements
("changes every three days" is out of reach; "last changed 3 days ago" is
in reach and is not the same claim, which is exactly why it gets its own
field instead of being folded into the category).

**Scoping note:** both functions read only the rows recorded under the
target app's OWN ``appid`` column — i.e. this app's own prefill/ingest
history — never rows a co-owning app's job recorded for a shared depot (see
this module's own docstring above and ``manifest_ingest.ingest_after_prefill``
for why a shared depot can have independent rows per attributing app). This
mirrors the same "own ingest vs. other apps' shared depots" boundary
``depot_manifests.containing_appid``'s docstring already draws, and keeps
this feature from having to solve the shared-depot merge problem ADR-0003/
ADR-0007 have dedicated machinery for.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from vault_api import deletion
from vault_api.jobs import parse_utc_iso

#: How many observations of a depot's manifest are needed before this module
#: will say anything about how often it changes. ``1`` is the boundary pin's
#: exact case (WP 4h.1): a depot ingested exactly once has a ``first_seen_at``
#: and a window, but "how often does it change" is not yet a defined
#: question — there is nothing to compare the one observation against.
MIN_OBSERVATIONS_FOR_FREQUENCY = 2

#: How long an app's YOUNGEST-observed depot must have been watched before a
#: "stable"/"changed" verdict is trusted, mirroring
#: ``routers/clients.py``/``event_sweep.feed_is_young``'s "a short window is
#: not evidence" posture for bypass detection, and matching the existing
#: ``VAULT_GC_GRACE_DAYS`` default (``config.py``) as the project's established
#: "give it two weeks before judging" scale. Not configurable (like
#: ``steam_relay.DEFAULT_CACHE_TTL_SECONDS``): this work package adds no new
#: ``VAULT_*`` setting, and a fixed, documented floor is what "argue your
#: choice" (the work package brief) asks for, not a knob.
MIN_OBSERVATION_WINDOW_DAYS = 14

#: The three category strings ``GameSummary.manifest_change_frequency`` may
#: hold. ``None`` (not a string here — see ``change_frequency_for_app``) is
#: the fourth, distinct state: "no manifest data for this app at all", never
#: conflated with "insufficient_data" (which means SOME data, just not
#: enough) — the same "absence is not the same as a known-thin answer"
#: distinction pin 1 draws for a missing upstream field.
CATEGORY_INSUFFICIENT_DATA = "insufficient_data"
CATEGORY_STABLE = "stable"
#: Named ``CHANGED``, not ``CHANGING`` (post-review rename): this category
#: means "has been observed to change at least once since we started
#: watching" — a one-time-three-years-ago change and a weekly-change pattern
#: are BOTH ``"changed"``, indistinguishable from the category alone. It is
#: deliberately NOT named to imply an ongoing rate, because this table
#: cannot support one (see the module docstring's "NOT a rate" correction).
#: ``ChangeFrequency.days_since_last_change`` is the field that carries the
#: one rate-adjacent fact this table CAN support cheaply.
CATEGORY_CHANGED = "changed"


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

    **Change-frequency bookkeeping (schema v14, WP 4h.1), folded into this
    same statement rather than a second write:** on a first INSERT,
    ``first_seen_at`` and ``manifest_changed_at`` both start equal to
    ``recorded_at`` and ``observation_count`` starts at 1 — "just seen for
    the first time, no change observed yet". On a conflict (a re-ingest for
    the same ``(appid, depotid)``), ``first_seen_at`` is intentionally
    ABSENT from the ``DO UPDATE`` set list — once written it is never
    touched again, by construction, not by a guard. ``manifest_changed_at``
    only advances to the NEW ``recorded_at`` when the incoming ``manifestid``
    differs from the row's current one; a re-ingest of the same manifest
    (ADR-0006 tier 1's confirmed-current case) leaves it exactly where it
    was. ``observation_count`` increments unconditionally — it counts
    ingests, not changes.

    Commits internally — the same "one write function is the complete write
    unit for one fact" pattern as ``mapping.upsert_mapping``.
    """
    conn.execute(
        """
        INSERT INTO depot_manifests
            (appid, containing_appid, depotid, manifestid, chunk_count,
             total_bytes, recorded_at, source,
             first_seen_at, manifest_changed_at, observation_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT (appid, depotid) DO UPDATE SET
            containing_appid = excluded.containing_appid,
            manifestid        = excluded.manifestid,
            chunk_count       = excluded.chunk_count,
            total_bytes       = excluded.total_bytes,
            recorded_at       = excluded.recorded_at,
            source            = excluded.source,
            manifest_changed_at = CASE
                WHEN excluded.manifestid != depot_manifests.manifestid
                    THEN excluded.recorded_at
                ELSE depot_manifests.manifest_changed_at
            END,
            observation_count = depot_manifests.observation_count + 1
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
            recorded_at,  # first_seen_at (INSERT branch only)
            recorded_at,  # manifest_changed_at (INSERT branch only)
        ),
    )
    conn.commit()


#: Shared column list for the two plain readers below — kept as one tuple so
#: adding a future column means editing one place, not two near-identical
#: SQL strings that could silently drift apart.
_ALL_COLUMNS = (
    "appid", "containing_appid", "depotid", "manifestid", "chunk_count",
    "total_bytes", "recorded_at", "source",
    "first_seen_at", "manifest_changed_at", "observation_count",
)
_SELECT_ALL_COLUMNS = ", ".join(_ALL_COLUMNS)


def get_depot_manifest(
    conn: sqlite3.Connection, *, appid: int, depotid: int
) -> dict[str, object] | None:
    """The stored manifest state for one ``(appid, depotid)`` pair, or ``None``."""
    row = conn.execute(
        f"""
        SELECT {_SELECT_ALL_COLUMNS}
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
            f"""
            SELECT {_SELECT_ALL_COLUMNS}
            FROM depot_manifests
            ORDER BY appid, depotid
            """
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT {_SELECT_ALL_COLUMNS}
            FROM depot_manifests
            WHERE appid = ?
            ORDER BY depotid
            """,
            (appid,),
        ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


# --------------------------------------------------------------------------
# Change frequency (WP 4h.1) — see the module docstring's "Change frequency"
# section for the full reasoning behind every threshold below.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ChangeFrequency:
    """The per-app answer ``routers/games.py`` exposes verbatim.

    ``category`` is ``None`` (not a string) when ``depot_manifests`` has NO
    row at all for this app — genuinely no data, never conflated with
    ``CATEGORY_INSUFFICIENT_DATA`` (some data, just not enough yet). Otherwise
    one of ``CATEGORY_INSUFFICIENT_DATA`` / ``CATEGORY_STABLE`` /
    ``CATEGORY_CHANGED``.

    ``observation_days`` is the number of days since the YOUNGEST-observed
    depot's ``first_seen_at`` — ``None`` only alongside ``category is None``
    (no data means no window either). It is populated even when ``category``
    is ``CATEGORY_INSUFFICIENT_DATA`` so a frontend can render "observed for
    3 days" rather than a bare "insufficient data" with no number behind it —
    pin 2's whole point is that the window itself is the honest thing to
    show, not a category label alone.

    ``days_since_last_change`` (post-review addition) is the number of days
    since the MOST RECENTLY observed change across this app's own depots —
    populated ONLY when ``category == CATEGORY_CHANGED`` (there is no "last
    change" to date otherwise: ``"stable"`` never observed one,
    ``"insufficient_data"``/``None`` never earned the right to say). This is
    the one rate-adjacent fact the underlying table can actually support —
    see the module docstring's "NOT a rate" correction for why ``category``
    alone cannot.
    """

    category: str | None
    observation_days: int | None
    days_since_last_change: int | None = None


def _aggregate_change_frequency(
    rows: Iterable[object], now: datetime
) -> ChangeFrequency:
    """Shared aggregation core for ``change_frequency_for_app`` and
    ``change_frequency_by_app`` — ``rows`` are this ONE app's own
    ``depot_manifests`` rows (already filtered/grouped by the caller), each
    exposing ``first_seen_at`` / ``manifest_changed_at`` / ``observation_count``
    by key (a ``sqlite3.Row`` or an equivalent mapping).

    **Every threshold below fails toward ``CATEGORY_INSUFFICIENT_DATA``, never
    toward a confident claim** — the same "unknown reads as not suspected"
    posture ``routers/clients.py``'s bypass detection uses, applied to
    stability instead of bypass:

    - No rows at all → ``(None, None)``: no manifest data whatsoever.
    - Every row's timestamp is corrupt/unparseable (a hand-edited database) →
      degrade the same way ``jobs.parse_utc_iso`` documents for its own
      callers: treat as "no usable value", not as a guess.
    - The WEAKEST-LINK depot (fewest observations, OR — via the window below
      — most recently first-seen) gates the whole app. A single freshly-added
      depot (e.g. a DLC just mapped in) pulls an otherwise long-tracked app
      back to "insufficient_data": the app-level claim needs ALL its
      currently-recorded depots to individually clear both bars, not just
      most of them (pin 2's "a young vault must not report stability" reading
      extended to "a young PART of an old vault" too).
    - ``observation_count`` below ``MIN_OBSERVATIONS_FOR_FREQUENCY`` (2) is
      pin 2's literal boundary case: one observation cannot support a
      frequency claim, however old that one observation is.
    - The observation window (now minus the YOUNGEST ``first_seen_at``,
      i.e. the most conservative — shortest — reading across the app's
      depots) below ``MIN_OBSERVATION_WINDOW_DAYS`` (14) is the "young vault"
      case the work package names directly.
    - Only once BOTH bars clear: ``CATEGORY_CHANGED`` if any depot's
      ``manifest_changed_at`` moved past its ``first_seen_at`` (a real,
      observed manifest change — NOT a rate, see the module docstring's "NOT
      a rate" correction), else ``CATEGORY_STABLE``. When ``CATEGORY_CHANGED``,
      ``days_since_last_change`` is also filled in: days since the MOST
      RECENT such change across the app's depots (the LATEST
      ``manifest_changed_at`` among the depots that actually changed) — the
      one rate-adjacent fact this table can support cheaply.
    """
    first_seen_dates: list[datetime] = []
    change_dates: list[datetime] = []
    min_observations: int | None = None

    for row in rows:
        first_seen = parse_utc_iso(row["first_seen_at"])
        changed_at = parse_utc_iso(row["manifest_changed_at"])
        count = row["observation_count"]

        if first_seen is not None:
            first_seen_dates.append(first_seen)
        if first_seen is not None and changed_at is not None and changed_at > first_seen:
            change_dates.append(changed_at)
        if isinstance(count, int) and not isinstance(count, bool):
            if min_observations is None or count < min_observations:
                min_observations = count

    if not first_seen_dates:
        # No rows at all, OR every row's first_seen_at was unparseable —
        # both degrade to "no usable data" rather than a guess (same
        # contract parse_utc_iso documents for its other callers).
        return ChangeFrequency(category=None, observation_days=None)

    # The YOUNGEST first_seen_at, i.e. the LEAST time we can honestly claim
    # to have watched EVERY one of this app's currently-recorded depots —
    # the conservative (shortest) reading, never the average or the oldest.
    window_start = max(first_seen_dates)
    observation_days = max(0, (now - window_start).days)

    if min_observations is None or min_observations < MIN_OBSERVATIONS_FOR_FREQUENCY:
        return ChangeFrequency(
            category=CATEGORY_INSUFFICIENT_DATA, observation_days=observation_days
        )
    if observation_days < MIN_OBSERVATION_WINDOW_DAYS:
        return ChangeFrequency(
            category=CATEGORY_INSUFFICIENT_DATA, observation_days=observation_days
        )

    if not change_dates:
        return ChangeFrequency(category=CATEGORY_STABLE, observation_days=observation_days)

    days_since_last_change = max(0, (now - max(change_dates)).days)
    return ChangeFrequency(
        category=CATEGORY_CHANGED,
        observation_days=observation_days,
        days_since_last_change=days_since_last_change,
    )


def change_frequency_for_app(
    conn: sqlite3.Connection, appid: int, *, now: datetime | None = None
) -> ChangeFrequency:
    """``ChangeFrequency`` for one app, scoped to rows recorded under this
    app's OWN ``appid`` (see the module docstring's "Scoping note").

    ``now`` is injectable for tests (mirrors ``event_sweep.feed_is_young``'s
    signature) — defaults to the real current time.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    rows = conn.execute(
        """
        SELECT first_seen_at, manifest_changed_at, observation_count
        FROM depot_manifests
        WHERE appid = ?
        """,
        (appid,),
    ).fetchall()
    return _aggregate_change_frequency(rows, now)


def change_frequency_by_app(
    conn: sqlite3.Connection, *, now: datetime | None = None
) -> dict[int, ChangeFrequency]:
    """Bulk variant of ``change_frequency_for_app`` for ``GET /v1/games``:
    one query for every tracked app's rows, grouped in Python, rather than
    one query per app (the N+1 shape ``routers/games.py``'s own
    ``depot_rows``/``app_depotids`` pattern already avoids for sizes).

    An appid with no rows at all is simply ABSENT from the returned dict —
    callers must treat a missing key the same as
    ``ChangeFrequency(None, None)``, exactly like ``change_frequency_for_app``
    would return for it directly.

    **A poisoned ``appid`` is skipped, not raised (post-review fix).** SQLite
    does not enforce column *type*, only affinity — a hand-edited or
    corrupted database can hold a non-numeric string in an INTEGER column,
    exactly the scenario ``tests/test_gc.py`` already seeds against this same
    table (``gc.load_recorded_manifests`` degrades the same way). The first
    version of this function did a bare ``int(row["appid"])``, which raised
    ``ValueError`` and took down the WHOLE ``GET /v1/games`` listing over one
    bad row — worse than the pre-existing behaviour it replaced (a poisoned
    row used to only degrade a GC report). ``deletion.coerce_positive_id`` is
    the same strict validator ``gc.py`` uses for this exact column; a row that
    fails it is dropped from the grouping entirely, matching
    ``change_frequency_for_app``'s own behaviour for the SAME poisoned data
    (that function never even looks at the ``appid`` column — its caller
    already supplies a trusted Python ``int``, which is why the two functions
    could disagree here until this fix).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    rows = conn.execute(
        "SELECT appid, first_seen_at, manifest_changed_at, observation_count "
        "FROM depot_manifests"
    ).fetchall()

    grouped: dict[int, list[object]] = {}
    for row in rows:
        appid = deletion.coerce_positive_id(row["appid"])
        if appid is None:
            continue
        grouped.setdefault(appid, []).append(row)

    return {appid: _aggregate_change_frequency(group, now) for appid, group in grouped.items()}
