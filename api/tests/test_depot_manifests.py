"""vault_api/depot_manifests.py (WP 3.2): the depot_manifests write path.

WP 4h.1 adds the change-frequency bookkeeping (first_seen_at/
manifest_changed_at/observation_count, schema v14) and its two honesty
pins — tested from line ~185 onward, below the original WP 3.2 coverage.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from vault_api.db import get_connection, init_db
from vault_api.depot_manifests import (
    MIN_OBSERVATION_WINDOW_DAYS,
    CATEGORY_CHANGED,
    CATEGORY_INSUFFICIENT_DATA,
    CATEGORY_STABLE,
    ChangeFrequency,
    change_frequency_by_app,
    change_frequency_for_app,
    get_depot_manifest,
    list_depot_manifests,
    upsert_depot_manifest,
)


def _conn(tmp_path):
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    return get_connection(db_path)


def test_upsert_then_get_round_trips_all_fields(tmp_path) -> None:
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn,
            appid=440,
            containing_appid=440,
            depotid=441,
            manifestid="123456789",
            chunk_count=3,
            total_bytes=3000,
            recorded_at="2026-08-06T00:00:00Z",
            source="prefill_bin",
        )

        row = get_depot_manifest(conn, appid=440, depotid=441)
    finally:
        conn.close()

    assert row == {
        "appid": 440,
        "containing_appid": 440,
        "depotid": 441,
        "manifestid": "123456789",
        "chunk_count": 3,
        "total_bytes": 3000,
        "recorded_at": "2026-08-06T00:00:00Z",
        "source": "prefill_bin",
        # v14 (WP 4h.1): a first-ever insert starts first_seen_at and
        # manifest_changed_at both equal to recorded_at, and
        # observation_count at 1 -- "just seen for the first time, no
        # change observed yet".
        "first_seen_at": "2026-08-06T00:00:00Z",
        "manifest_changed_at": "2026-08-06T00:00:00Z",
        "observation_count": 1,
    }


def test_get_unknown_pair_returns_none(tmp_path) -> None:
    conn = _conn(tmp_path)
    try:
        assert get_depot_manifest(conn, appid=440, depotid=441) is None
    finally:
        conn.close()


def test_manifestid_stores_a_value_beyond_sqlite_int64_range_as_text(tmp_path) -> None:
    """WP 3.2 schema decision: manifestid is TEXT because Steam manifest ids
    are u64 and SQLite INTEGER storage is signed 64-bit. A real id observed
    during research (3040704736299968944) stays under 2**63-1, but the
    column must not silently break on one that doesn't."""
    huge = str(2**64 - 1)  # far beyond i64's max (2**63 - 1)
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn,
            appid=440,
            containing_appid=None,
            depotid=441,
            manifestid=huge,
            chunk_count=1,
            total_bytes=10,
            recorded_at="2026-08-06T00:00:00Z",
            source="prefill_bin",
        )
        row = get_depot_manifest(conn, appid=440, depotid=441)
    finally:
        conn.close()

    assert row["manifestid"] == huge
    assert isinstance(row["manifestid"], str)


def test_upsert_replaces_the_row_for_the_same_app_and_depot(tmp_path) -> None:
    """ADR-0006 decision 3: latest-per-(appid, depotid), not a history table
    -- a second ingest for the same pair overwrites, never adds a row."""
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=100, recorded_at="2026-08-06T00:00:00Z",
            source="prefill_bin",
        )
        upsert_depot_manifest(
            conn, appid=440, containing_appid=228980, depotid=441, manifestid="2",
            chunk_count=5, total_bytes=500, recorded_at="2026-08-06T01:00:00Z",
            source="cache_manifest",
        )

        rows = list_depot_manifests(conn, appid=440)
    finally:
        conn.close()

    assert len(rows) == 1
    assert rows[0]["manifestid"] == "2"
    assert rows[0]["containing_appid"] == 228980
    assert rows[0]["chunk_count"] == 5
    assert rows[0]["source"] == "cache_manifest"


def test_different_depots_of_the_same_app_are_independent_rows(tmp_path) -> None:
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10, recorded_at="2026-08-06T00:00:00Z",
            source="prefill_bin",
        )
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=442, manifestid="2",
            chunk_count=2, total_bytes=20, recorded_at="2026-08-06T00:00:01Z",
            source="prefill_bin",
        )

        rows = list_depot_manifests(conn, appid=440)
    finally:
        conn.close()

    assert {row["depotid"] for row in rows} == {441, 442}


def test_same_depot_shared_across_two_apps_gets_two_rows(tmp_path) -> None:
    """Latest-per-(appid, depotid) is scoped per APP, not globally per depot
    -- a depot shared between two tracked apps gets one row per app, each
    potentially recording a different manifest state (e.g. one app's
    prefill is more current than the other's)."""
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=900, manifestid="1",
            chunk_count=1, total_bytes=10, recorded_at="2026-08-06T00:00:00Z",
            source="prefill_bin",
        )
        upsert_depot_manifest(
            conn, appid=730, containing_appid=730, depotid=900, manifestid="2",
            chunk_count=2, total_bytes=20, recorded_at="2026-08-06T00:00:01Z",
            source="prefill_bin",
        )

        rows = list_depot_manifests(conn)
    finally:
        conn.close()

    by_app = {row["appid"]: row for row in rows if row["depotid"] == 900}
    assert set(by_app) == {440, 730}
    assert by_app[440]["manifestid"] == "1"
    assert by_app[730]["manifestid"] == "2"


def test_containing_appid_is_nullable(tmp_path) -> None:
    """Cache-stored manifests carry no containing_appid distinction."""
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=None, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10, recorded_at="2026-08-06T00:00:00Z",
            source="cache_manifest",
        )
        row = get_depot_manifest(conn, appid=440, depotid=441)
    finally:
        conn.close()

    assert row["containing_appid"] is None


# ==========================================================================
# WP 4h.1: upsert's change-frequency bookkeeping
# ==========================================================================


def test_first_seen_at_is_set_on_first_insert(tmp_path) -> None:
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10, recorded_at="2026-08-06T00:00:00Z",
            source="prefill_bin",
        )
        row = get_depot_manifest(conn, appid=440, depotid=441)
    finally:
        conn.close()

    assert row["first_seen_at"] == "2026-08-06T00:00:00Z"
    assert row["manifest_changed_at"] == "2026-08-06T00:00:00Z"
    assert row["observation_count"] == 1


def test_first_seen_at_never_moves_across_repeated_upserts(tmp_path) -> None:
    """The observation window's anchor -- written once, at INSERT, and left
    out of the upsert's DO UPDATE set list entirely afterwards."""
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10, recorded_at="2026-08-06T00:00:00Z",
            source="prefill_bin",
        )
        for day in range(1, 4):
            upsert_depot_manifest(
                conn, appid=440, containing_appid=440, depotid=441, manifestid=str(day + 1),
                chunk_count=1, total_bytes=10, recorded_at=f"2026-08-{6 + day:02d}T00:00:00Z",
                source="prefill_bin",
            )
        row = get_depot_manifest(conn, appid=440, depotid=441)
    finally:
        conn.close()

    assert row["first_seen_at"] == "2026-08-06T00:00:00Z"


def test_reingesting_the_same_manifestid_does_not_advance_manifest_changed_at(tmp_path) -> None:
    """A confirmed-current, non-forced run re-ingests the SAME manifest --
    that must not look like a change (ADR-0006 tier 1)."""
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10, recorded_at="2026-08-06T00:00:00Z",
            source="prefill_bin",
        )
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10, recorded_at="2026-08-07T00:00:00Z",
            source="prefill_bin",
        )
        row = get_depot_manifest(conn, appid=440, depotid=441)
    finally:
        conn.close()

    assert row["recorded_at"] == "2026-08-07T00:00:00Z"  # this DID advance
    assert row["manifest_changed_at"] == "2026-08-06T00:00:00Z"  # this did NOT
    assert row["observation_count"] == 2  # ingest count still advances


def test_a_real_manifest_change_advances_manifest_changed_at(tmp_path) -> None:
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10, recorded_at="2026-08-06T00:00:00Z",
            source="prefill_bin",
        )
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="2",
            chunk_count=2, total_bytes=20, recorded_at="2026-08-07T00:00:00Z",
            source="prefill_bin",
        )
        row = get_depot_manifest(conn, appid=440, depotid=441)
    finally:
        conn.close()

    assert row["manifest_changed_at"] == "2026-08-07T00:00:00Z"
    assert row["observation_count"] == 2


def test_observation_count_keeps_incrementing_across_many_ingests(tmp_path) -> None:
    conn = _conn(tmp_path)
    try:
        for i in range(5):
            upsert_depot_manifest(
                conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
                chunk_count=1, total_bytes=10, recorded_at=f"2026-08-{6 + i:02d}T00:00:00Z",
                source="prefill_bin",
            )
        row = get_depot_manifest(conn, appid=440, depotid=441)
    finally:
        conn.close()

    assert row["observation_count"] == 5


# ==========================================================================
# WP 4h.1: change_frequency_for_app / change_frequency_by_app
#
# The two honesty pins THIS work package exists for. Each has its own named
# test, and each is mutation-killed (see the coder's final report for the
# exact mutation applied and reverted against each one).
# ==========================================================================

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_pin_no_manifest_history_at_all_is_none_not_insufficient_data(tmp_path) -> None:
    """'Never looked' and 'looked, but not enough' are DIFFERENT honest
    answers (pin 2's distinction) -- a game with zero depot_manifests rows
    gets None, never the CATEGORY_INSUFFICIENT_DATA string."""
    conn = _conn(tmp_path)
    try:
        result = change_frequency_for_app(conn, 440, now=NOW)
        bulk = change_frequency_by_app(conn, now=NOW)
    finally:
        conn.close()

    assert result == ChangeFrequency(category=None, observation_days=None)
    assert 440 not in bulk  # bulk variant: simply absent, not a null-valued entry


def test_pin_exactly_one_observation_is_insufficient_data_however_old(tmp_path) -> None:
    """WP 4h.1's explicit boundary case: one observation cannot support a
    frequency claim -- even dated 400 days in the past, which would clear
    the window bar on its own if only the window were checked."""
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=400)),
            source="prefill_bin",
        )
        result = change_frequency_for_app(conn, 440, now=NOW)
    finally:
        conn.close()

    assert result.category == CATEGORY_INSUFFICIENT_DATA
    assert result.observation_days == 400


def test_pin_a_young_vault_reports_insufficient_data_not_stable(tmp_path) -> None:
    """The work package's other named case: two solid observations, zero
    changes, but the vault has only been watching for 2 days -- must NOT
    read as 'stable'."""
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=2)),
            source="prefill_bin",
        )
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=1)),
            source="prefill_bin",
        )
        result = change_frequency_for_app(conn, 440, now=NOW)
    finally:
        conn.close()

    assert result.category == CATEGORY_INSUFFICIENT_DATA
    assert result.observation_days == 2


def test_enough_old_unchanged_observations_is_stable(tmp_path) -> None:
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=30)),
            source="prefill_bin",
        )
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=1)),
            source="prefill_bin",
        )
        result = change_frequency_for_app(conn, 440, now=NOW)
    finally:
        conn.close()

    assert result.category == CATEGORY_STABLE
    assert result.observation_days == 30


def test_enough_old_observations_with_a_real_change_is_changed(tmp_path) -> None:
    """Renamed from '...is_changing' post-review: this category means 'has
    changed at least once, ever' -- NOT a rate (see the module docstring's
    'NOT a rate' correction). days_since_last_change is the field that
    carries the recency fact a frontend actually wants."""
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=30)),
            source="prefill_bin",
        )
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="2",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=1)),
            source="prefill_bin",
        )
        result = change_frequency_for_app(conn, 440, now=NOW)
    finally:
        conn.close()

    assert result.category == CATEGORY_CHANGED
    assert result.observation_days == 30
    assert result.days_since_last_change == 1


def test_stable_and_insufficient_data_never_carry_a_days_since_last_change(tmp_path) -> None:
    """days_since_last_change is populated ONLY for CATEGORY_CHANGED -- 'stable'
    never observed a change to date, and 'insufficient_data' never earned the
    right to say anything about one."""
    conn = _conn(tmp_path)
    try:
        # Two old, UNCHANGED observations -> stable.
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=30)),
            source="prefill_bin",
        )
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=1)),
            source="prefill_bin",
        )
        # One young observation -> insufficient_data.
        upsert_depot_manifest(
            conn, appid=730, containing_appid=730, depotid=900, manifestid="9",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=1)),
            source="prefill_bin",
        )

        stable = change_frequency_for_app(conn, 440, now=NOW)
        insufficient = change_frequency_for_app(conn, 730, now=NOW)
    finally:
        conn.close()

    assert stable.category == CATEGORY_STABLE
    assert stable.days_since_last_change is None
    assert insufficient.category == CATEGORY_INSUFFICIENT_DATA
    assert insufficient.days_since_last_change is None


def test_manifest_changed_at_keeps_advancing_across_repeated_changes(tmp_path) -> None:
    """Renamed post-review (B3): what this test actually pins is the
    upsert's SQL CASE advancing manifest_changed_at to the LATEST recorded_at
    on each successive change of ONE depot -- already covered in spirit by
    test_a_real_manifest_change_advances_manifest_changed_at for a single
    change, this extends it to a THIRD change. It does NOT, and cannot,
    exercise the aggregation's across-DEPOTS 'most recent wins' choice: one
    depot can only ever contribute one change_dates entry, so max()==min()
    here and a max-vs-min mutation in _aggregate_change_frequency is
    unobservable from this test alone (measured: it survives). See
    test_days_since_last_change_is_the_most_recent_across_depots_not_the_first
    below for the test that actually detects that mutation."""
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=100)),
            source="prefill_bin",
        )
        upsert_depot_manifest(  # changed 50 days ago
            conn, appid=440, containing_appid=440, depotid=441, manifestid="2",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=50)),
            source="prefill_bin",
        )
        upsert_depot_manifest(  # changed again, 5 days ago
            conn, appid=440, containing_appid=440, depotid=441, manifestid="3",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=5)),
            source="prefill_bin",
        )
        result = change_frequency_for_app(conn, 440, now=NOW)
    finally:
        conn.close()

    assert result.category == CATEGORY_CHANGED
    assert result.days_since_last_change == 5


def test_days_since_last_change_is_the_most_recent_across_depots_not_the_first(
    tmp_path,
) -> None:
    """The actual guarantee the field name promises: across MULTIPLE depots
    that changed at different times, the app-level answer is the LATEST
    change, never the first one encountered and never an average or the
    oldest. This is the only shape that can even observe a max-vs-min bug in
    _aggregate_change_frequency -- a single depot contributes exactly one
    change_dates entry, so max()==min() there by construction (see the
    renamed single-depot test above, and its docstring for the measured
    'mutation is unobservable' finding this test exists to fix).

    Three depots, all first seen 200 days ago (comfortably past both
    thresholds before any change is even considered): 441 changed 90 days
    ago, 442 changed 4 days ago, 443 never changed. The correct answer is 4
    -- min(change_dates) would answer 90 instead, which is exactly the wrong
    number a frontend would render as "last changed 90 days ago" for a game
    that actually changed 4 days ago.
    """
    conn = _conn(tmp_path)
    try:
        for depotid in (441, 442, 443):
            upsert_depot_manifest(
                conn, appid=440, containing_appid=440, depotid=depotid, manifestid="1",
                chunk_count=1, total_bytes=10,
                recorded_at=_iso(NOW - timedelta(days=200)),
                source="prefill_bin",
            )
        upsert_depot_manifest(  # 441 changed 90 days ago
            conn, appid=440, containing_appid=440, depotid=441, manifestid="2",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=90)),
            source="prefill_bin",
        )
        upsert_depot_manifest(  # 442 changed MORE RECENTLY, 4 days ago
            conn, appid=440, containing_appid=440, depotid=442, manifestid="2",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=4)),
            source="prefill_bin",
        )
        # depot 443 gets a SECOND observation (confirmed-current re-ingest,
        # same manifestid -- never changed) so its observation_count clears
        # MIN_OBSERVATIONS_FOR_FREQUENCY too; without this it would be the
        # weakest link at count=1 and force "insufficient_data" regardless
        # of the window, which is a different, already-pinned guarantee
        # (test_one_freshly_added_depot_pulls_the_whole_app_back_to_insufficient_data)
        # that this test does not want to exercise by accident.
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=443, manifestid="1",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=1)),
            source="prefill_bin",
        )

        single = change_frequency_for_app(conn, 440, now=NOW)
        bulk = change_frequency_by_app(conn, now=NOW)[440]
    finally:
        conn.close()

    assert single.category == CATEGORY_CHANGED
    assert single.days_since_last_change == 4  # the LATER change -- NOT 90
    assert bulk == single  # both readers must agree


def test_the_window_boundary_is_half_open_exactly_the_minimum_is_enough(tmp_path) -> None:
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=MIN_OBSERVATION_WINDOW_DAYS)),
            source="prefill_bin",
        )
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=1)),
            source="prefill_bin",
        )
        result = change_frequency_for_app(conn, 440, now=NOW)
    finally:
        conn.close()

    assert result.category == CATEGORY_STABLE  # exactly the minimum clears the bar
    assert result.observation_days == MIN_OBSERVATION_WINDOW_DAYS


def test_one_freshly_added_depot_pulls_the_whole_app_back_to_insufficient_data(tmp_path) -> None:
    """The weakest-link rule: one old, well-observed depot plus one
    brand-new one must not average out to a confident claim about the app as
    a whole."""
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=400)),
            source="prefill_bin",
        )
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=1)),
            source="prefill_bin",
        )
        # depot 442 just showed up, e.g. a newly mapped DLC depot -- one
        # observation, one day ago.
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=442, manifestid="1",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=1)),
            source="prefill_bin",
        )
        result = change_frequency_for_app(conn, 440, now=NOW)
    finally:
        conn.close()

    assert result.category == CATEGORY_INSUFFICIENT_DATA
    assert result.observation_days == 1  # bounded by depot 442, not depot 441


def test_change_frequency_by_app_matches_the_single_app_function(tmp_path) -> None:
    """Round-trip parity between the bulk and single-app readers -- they
    must never disagree, since GET /v1/games and GET /v1/games/{appid} use
    one each."""
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=30)),
            source="prefill_bin",
        )
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=1)),
            source="prefill_bin",
        )
        upsert_depot_manifest(
            conn, appid=730, containing_appid=730, depotid=900, manifestid="9",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=1)),
            source="prefill_bin",
        )

        single_440 = change_frequency_for_app(conn, 440, now=NOW)
        single_730 = change_frequency_for_app(conn, 730, now=NOW)
        bulk = change_frequency_by_app(conn, now=NOW)
    finally:
        conn.close()

    assert bulk[440] == single_440 == ChangeFrequency(CATEGORY_STABLE, 30)
    assert bulk[730] == single_730 == ChangeFrequency(CATEGORY_INSUFFICIENT_DATA, 1)


def test_a_corrupt_first_seen_at_degrades_to_no_data_not_a_crash(tmp_path) -> None:
    """A hand-edited database is hostile input too (docs/LEARNINGS.md
    Parsers): an unparseable timestamp must degrade like 'no usable value',
    same contract jobs.parse_utc_iso documents for its other callers."""
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10, recorded_at="2026-08-06T00:00:00Z",
            source="prefill_bin",
        )
        conn.execute(
            "UPDATE depot_manifests SET first_seen_at = 'not-a-timestamp' WHERE appid = 440"
        )
        conn.commit()
        result = change_frequency_for_app(conn, 440, now=NOW)
    finally:
        conn.close()

    assert result == ChangeFrequency(category=None, observation_days=None)


def test_change_frequency_by_app_skips_a_poisoned_appid_row_instead_of_raising(
    tmp_path,
) -> None:
    """Regression pin (review B2): SQLite enforces column AFFINITY, not
    type -- a hand-edited/corrupted database can hold a non-numeric string in
    depot_manifests.appid, exactly the poison
    tests/test_gc.py::test_load_recorded_manifests_skips_poisoned_rows already
    seeds against this same table. The first version of change_frequency_by_app
    did a bare int(row['appid']), which raised ValueError and would have taken
    down the WHOLE GET /v1/games listing over one bad row -- worse than the
    pre-existing behaviour it replaced (a poisoned row used to only degrade a
    GC report). change_frequency_for_app never touches the appid COLUMN at
    all (its caller supplies a trusted int), so it was never at risk -- this
    pins that the bulk reader now degrades the SAME way instead of crashing,
    restoring the 'the two readers must never disagree' property the sibling
    test above claims."""
    conn = _conn(tmp_path)
    try:
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=30)),
            source="prefill_bin",
        )
        upsert_depot_manifest(
            conn, appid=440, containing_appid=440, depotid=441, manifestid="1",
            chunk_count=1, total_bytes=10,
            recorded_at=_iso(NOW - timedelta(days=1)),
            source="prefill_bin",
        )
        conn.execute(
            """
            INSERT INTO depot_manifests
                (appid, containing_appid, depotid, manifestid, chunk_count,
                 total_bytes, recorded_at, source,
                 first_seen_at, manifest_changed_at, observation_count)
            VALUES ('not-an-appid', NULL, 900, '1', 1, 10, 'now', 'x', 'now', 'now', 1)
            """
        )
        conn.commit()

        bulk = change_frequency_by_app(conn, now=NOW)  # must not raise
    finally:
        conn.close()

    assert 440 in bulk
    assert bulk[440] == ChangeFrequency(CATEGORY_STABLE, 30)
    # The poisoned row contributed no entry of its own (there is no valid
    # appid to key it by) and did not corrupt app 440's real entry either.
    assert len(bulk) == 1


def test_change_frequency_by_app_is_one_statement_regardless_of_app_count(tmp_path) -> None:
    """The N+1-avoidance claim in change_frequency_by_app's own docstring,
    pinned the way tests/test_prefill_cached.py pins the analogous claim for
    _select_appids_with_cache_content: exactly ONE SQL statement, however
    many apps/depots exist."""
    conn = _conn(tmp_path)
    try:
        app_count = 50
        for i in range(app_count):
            upsert_depot_manifest(
                conn, appid=100_000 + i, containing_appid=100_000 + i,
                depotid=900_000 + i, manifestid="1",
                chunk_count=1, total_bytes=10,
                recorded_at=_iso(NOW - timedelta(days=30)),
                source="prefill_bin",
            )

        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        try:
            result = change_frequency_by_app(conn, now=NOW)
        finally:
            conn.set_trace_callback(None)

        assert len(result) == app_count
        # The whole point: exactly one SELECT, regardless of app_count.
        assert len(statements) == 1, statements
    finally:
        conn.close()
