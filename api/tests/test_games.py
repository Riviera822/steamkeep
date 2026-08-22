from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api import depot_manifests
from vault_api.config import Settings
from vault_api.db import get_connection
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}


def _seed_mapping(client: TestClient, depotid: int, appid: int, app_name: str | None) -> None:
    response = client.put(
        f"/v1/mapping/{depotid}", json={"appid": appid, "app_name": app_name}, headers=AUTH
    )
    assert response.status_code == 200


def _write(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_manifest(
    settings: Settings, *, appid: int, depotid: int, manifestid: str, recorded_at: str
) -> None:
    """Write one depot_manifests row directly, bypassing HTTP -- these tests
    are about GET /v1/games'/{appid}'s change-frequency FIELDS, not about the
    ingestion path that normally populates this table (covered elsewhere,
    tests/test_manifest_ingest.py)."""
    conn = get_connection(settings.db_path)
    try:
        depot_manifests.upsert_depot_manifest(
            conn,
            appid=appid,
            containing_appid=appid,
            depotid=depotid,
            manifestid=manifestid,
            chunk_count=1,
            total_bytes=10,
            recorded_at=recorded_at,
            source="prefill_bin",
        )
    finally:
        conn.close()


def test_list_games_without_key_is_rejected(client: TestClient) -> None:
    response = client.get("/v1/games")
    assert response.status_code == 401


def test_list_games_is_empty_when_no_apps_tracked(client: TestClient) -> None:
    response = client.get("/v1/games", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == []


def test_list_games_reports_depot_count_and_null_size(client: TestClient) -> None:
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")
    _seed_mapping(client, depotid=442, appid=440, app_name=None)

    response = client.get("/v1/games", headers=AUTH)
    assert response.status_code == 200
    games = response.json()
    assert len(games) == 1
    game = games[0]
    assert game["appid"] == 440
    assert game["name"] == "Team Fortress 2"
    assert game["status"] == "idle"
    assert game["depot_count"] == 2
    # Mapped, but nothing has ever been written to disk for this app yet
    # (VAULT_CACHE_ROOT doesn't even exist) -> "uncached", reported as null,
    # not a guessed 0 (WP 1.5, vault_api.sizes.app_size_bytes).
    assert game["size_bytes"] is None


def test_list_games_exposes_needs_force_defaulting_true(client: TestClient) -> None:
    # Schema v5 default (WP 3.4, ADR-0006 decision 2): a never-filled app
    # needs its first prefill forced.
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")

    game = client.get("/v1/games", headers=AUTH).json()[0]
    assert game["needs_force"] is True


def test_get_game_detail_exposes_needs_force(client: TestClient) -> None:
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")

    body = client.get("/v1/games/440", headers=AUTH).json()
    assert body["needs_force"] is True


# -- last_manifest_check (WP 4c: surfacing apps.last_manifest_check) --------
#
# Write-path semantics (worker.py, pinned end-to-end in test_worker.py) are
# NOT this file's concern -- these tests only check that GameSummary/
# GameDetail expose whatever is already in the apps row, with the same
# null-when-never-set behavior last_prefill_at already has.


def test_list_games_reports_null_last_manifest_check_for_a_never_checked_app(
    client: TestClient,
) -> None:
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")

    game = client.get("/v1/games", headers=AUTH).json()[0]
    assert game["last_manifest_check"] is None


def test_get_game_detail_reports_null_last_manifest_check_for_a_never_checked_app(
    client: TestClient,
) -> None:
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")

    body = client.get("/v1/games/440", headers=AUTH).json()
    assert body["last_manifest_check"] is None


def test_list_games_round_trips_the_exact_last_manifest_check_value(
    client: TestClient, settings: Settings
) -> None:
    """Pin against timezone/format mangling: what is written to the DB
    column is exactly what the API returns, byte for byte -- no re-parsing
    through a datetime type that could normalize/shift it."""
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")
    from vault_api.db import get_connection

    # A deliberately odd-looking but well-formed stamp (not "now") so a bug
    # that silently substituted the current time, or ran the value through
    # something that re-normalizes/shifts it, would be caught.
    stamp = "2026-08-06T13:37:05Z"
    conn = get_connection(settings.db_path)
    try:
        conn.execute(
            "UPDATE apps SET last_manifest_check = ? WHERE appid = 440", (stamp,)
        )
        conn.commit()
    finally:
        conn.close()

    game = client.get("/v1/games", headers=AUTH).json()[0]
    detail = client.get("/v1/games/440", headers=AUTH).json()
    assert game["last_manifest_check"] == stamp
    assert detail["last_manifest_check"] == stamp


def test_get_game_without_key_is_rejected(client: TestClient) -> None:
    response = client.get("/v1/games/440")
    assert response.status_code == 401


def test_get_game_returns_404_for_unknown_appid(client: TestClient) -> None:
    response = client.get("/v1/games/999999", headers=AUTH)
    assert response.status_code == 404


def test_get_game_detail_lists_depots_not_shared(client: TestClient) -> None:
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")

    response = client.get("/v1/games/440", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["appid"] == 440
    assert body["name"] == "Team Fortress 2"
    assert body["depots"] == [{"depotid": 441, "shared": False, "size_bytes": None}]
    assert body["size_bytes"] is None


def test_get_game_detail_flags_shared_depot(client: TestClient) -> None:
    # plan §4 shared-depot semantics: a depot mapped to two tracked apps
    # must be reported as shared on both.
    _seed_mapping(client, depotid=999, appid=440, app_name="Team Fortress 2")
    _seed_mapping(client, depotid=999, appid=730, app_name="Counter-Strike 2")

    tf2_response = client.get("/v1/games/440", headers=AUTH)
    cs2_response = client.get("/v1/games/730", headers=AUTH)

    assert tf2_response.json()["depots"] == [
        {"depotid": 999, "shared": True, "size_bytes": None}
    ]
    assert cs2_response.json()["depots"] == [
        {"depotid": 999, "shared": True, "size_bytes": None}
    ]


# -- size wiring (WP 1.5): real files under VAULT_CACHE_ROOT ----------------


def _client_with_cache_root(tmp_path: Path) -> tuple[TestClient, Path]:
    cache_root = tmp_path / "cache"
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(cache_root),
        log_level="INFO",
    )
    return TestClient(create_app(settings)), cache_root


def test_list_games_reports_a_real_size_once_depots_are_on_disk(tmp_path: Path) -> None:
    client, cache_root = _client_with_cache_root(tmp_path)
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")
    _seed_mapping(client, depotid=442, appid=440, app_name=None)
    _write(cache_root / "depot" / "441" / "chunk" / "a", b"1" * 10)
    _write(cache_root / "depot" / "442" / "chunk" / "a", b"1" * 5)

    game = client.get("/v1/games", headers=AUTH).json()[0]

    assert game["size_bytes"] == 15


def test_get_game_detail_reports_per_depot_and_total_size(tmp_path: Path) -> None:
    client, cache_root = _client_with_cache_root(tmp_path)
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")
    _seed_mapping(client, depotid=442, appid=440, app_name=None)
    _write(cache_root / "depot" / "441" / "chunk" / "a", b"1" * 10)
    # 442 mapped but nothing written for it yet -> its own size_bytes is
    # null, while the app total still reports the 441 bytes it DOES have.

    body = client.get("/v1/games/440", headers=AUTH).json()

    depots_by_id = {d["depotid"]: d for d in body["depots"]}
    assert depots_by_id[441]["size_bytes"] == 10
    assert depots_by_id[442]["size_bytes"] is None
    assert body["size_bytes"] == 10


def test_shared_depot_counts_fully_into_both_apps(tmp_path: Path) -> None:
    client, cache_root = _client_with_cache_root(tmp_path)
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")
    _seed_mapping(client, depotid=900, appid=440, app_name="Team Fortress 2")
    _seed_mapping(client, depotid=900, appid=730, app_name="Counter-Strike 2")
    _write(cache_root / "depot" / "441" / "chunk" / "a", b"1" * 10)
    _write(cache_root / "depot" / "900" / "chunk" / "a", b"1" * 50)

    tf2 = client.get("/v1/games/440", headers=AUTH).json()
    cs2 = client.get("/v1/games/730", headers=AUTH).json()

    # Shared depot 900's 50 bytes counted fully into BOTH apps: per-app sizes
    # (60 + 50 = 110) sum to more than the 60 bytes actually on disk — see
    # vault_api/sizes.py::app_size_bytes and GET /v1/cache/summary's
    # total_bytes for the "counted once" figure.
    assert tf2["size_bytes"] == 60
    assert cs2["size_bytes"] == 50


# -- Change frequency (WP 4h.1, review B1/B2) --------------------------------
#
# The deliverable WP 4h.2 (the web panel) actually consumes: field presence
# and all four states over the REAL HTTP routes, for both GameSummary and
# GameDetail, plus the B2 regression (a poisoned depot_manifests.appid row
# must not 500 the whole listing).


def test_list_games_reports_null_change_frequency_when_no_manifest_data(
    client: TestClient,
) -> None:
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")

    game = client.get("/v1/games", headers=AUTH).json()[0]

    assert game["manifest_change_frequency"] is None
    assert game["manifest_observation_days"] is None
    assert game["manifest_days_since_last_change"] is None


def test_get_game_detail_reports_null_change_frequency_when_no_manifest_data(
    client: TestClient,
) -> None:
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")

    body = client.get("/v1/games/440", headers=AUTH).json()

    assert body["manifest_change_frequency"] is None
    assert body["manifest_observation_days"] is None
    assert body["manifest_days_since_last_change"] is None


def test_list_games_reports_insufficient_data_with_a_non_null_observation_days(
    client: TestClient, settings: Settings
) -> None:
    """pin 2's whole point: 'insufficient_data' still carries a REAL day
    count, not a bare label with nothing behind it."""
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")
    now = datetime.now(timezone.utc)
    _seed_manifest(
        settings, appid=440, depotid=441, manifestid="1",
        recorded_at=_iso(now - timedelta(days=1)),
    )

    game = client.get("/v1/games", headers=AUTH).json()[0]
    detail = client.get("/v1/games/440", headers=AUTH).json()

    for body in (game, detail):
        assert body["manifest_change_frequency"] == "insufficient_data"
        assert body["manifest_observation_days"] == 1  # non-null, exact
        assert body["manifest_days_since_last_change"] is None


def test_list_games_reports_stable_change_frequency(
    client: TestClient, settings: Settings
) -> None:
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")
    now = datetime.now(timezone.utc)
    _seed_manifest(
        settings, appid=440, depotid=441, manifestid="1",
        recorded_at=_iso(now - timedelta(days=30)),
    )
    _seed_manifest(  # second observation, SAME manifestid -> no change
        settings, appid=440, depotid=441, manifestid="1",
        recorded_at=_iso(now - timedelta(days=1)),
    )

    game = client.get("/v1/games", headers=AUTH).json()[0]
    detail = client.get("/v1/games/440", headers=AUTH).json()

    for body in (game, detail):
        assert body["manifest_change_frequency"] == "stable"
        assert body["manifest_observation_days"] == 30
        assert body["manifest_days_since_last_change"] is None


def test_get_game_detail_reports_changed_with_days_since_last_change(
    client: TestClient, settings: Settings
) -> None:
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")
    now = datetime.now(timezone.utc)
    _seed_manifest(
        settings, appid=440, depotid=441, manifestid="1",
        recorded_at=_iso(now - timedelta(days=30)),
    )
    _seed_manifest(  # different manifestid -> a real, observed change
        settings, appid=440, depotid=441, manifestid="2",
        recorded_at=_iso(now - timedelta(days=3)),
    )

    game = client.get("/v1/games", headers=AUTH).json()[0]
    detail = client.get("/v1/games/440", headers=AUTH).json()

    for body in (game, detail):
        assert body["manifest_change_frequency"] == "changed"
        assert body["manifest_observation_days"] == 30
        assert body["manifest_days_since_last_change"] == 3


def test_list_games_survives_a_poisoned_depot_manifests_appid_row(
    client: TestClient, settings: Settings
) -> None:
    """Review B2 regression: a hand-edited/corrupted database can hold a
    non-numeric appid in depot_manifests (SQLite enforces column affinity,
    not type) -- the exact poison tests/test_gc.py already seeds against this
    same table. Before the fix, this 500'd the WHOLE library listing; now it
    degrades the poisoned row out and leaves every real app's listing intact."""
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")
    now = datetime.now(timezone.utc)
    _seed_manifest(
        settings, appid=440, depotid=441, manifestid="1",
        recorded_at=_iso(now - timedelta(days=30)),
    )

    conn = get_connection(settings.db_path)
    try:
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
    finally:
        conn.close()

    response = client.get("/v1/games", headers=AUTH)

    assert response.status_code == 200
    game = response.json()[0]
    assert game["appid"] == 440
    assert game["manifest_change_frequency"] == "insufficient_data"
    assert game["manifest_observation_days"] == 30


# ---------------------------------------------------------------------------
# installed_on (WP AG-1): which fresh agent reports claim an app installed
# ---------------------------------------------------------------------------


def _report(client: TestClient, client_id: str, appids: list[int]) -> None:
    response = client.post(
        "/v1/agent/installed",
        json={"client_id": client_id, "appids": appids},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text


def test_installed_on_is_empty_for_a_vault_with_zero_agents(client: TestClient) -> None:
    """No agent has ever reported -- installed_on must be an empty list, not
    absent and not null, on both the list and the detail endpoint (the work
    package's "do not degrade for zero agents" requirement)."""
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")

    list_body = client.get("/v1/games", headers=AUTH).json()
    assert list_body[0]["installed_on"] == []

    detail_body = client.get("/v1/games/440", headers=AUTH).json()
    assert detail_body["installed_on"] == []


def test_installed_on_lists_the_one_client_that_reported_it(client: TestClient) -> None:
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")
    _report(client, "gaming-pc", [440])

    game = client.get("/v1/games", headers=AUTH).json()[0]
    assert len(game["installed_on"]) == 1
    entry = game["installed_on"][0]
    assert entry["client_id"] == "gaming-pc"
    assert entry["reported_at"].endswith("Z")

    detail = client.get("/v1/games/440", headers=AUTH).json()
    assert detail["installed_on"] == game["installed_on"]


def test_installed_on_lists_every_fresh_client_that_reported_it(client: TestClient) -> None:
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")
    _report(client, "gaming-pc", [440, 730])
    _report(client, "steam-deck", [440])

    game = client.get("/v1/games", headers=AUTH).json()[0]
    assert {entry["client_id"] for entry in game["installed_on"]} == {
        "gaming-pc",
        "steam-deck",
    }


def test_installed_on_ignores_a_client_that_never_reported_this_app(
    client: TestClient,
) -> None:
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")
    _seed_mapping(client, depotid=442, appid=730, app_name="CS")
    _report(client, "gaming-pc", [730])  # never reports 440

    games = {game["appid"]: game for game in client.get("/v1/games", headers=AUTH).json()}
    assert games[440]["installed_on"] == []
    assert games[730]["installed_on"][0]["client_id"] == "gaming-pc"


def test_installed_on_follows_a_runtime_override_of_the_stale_days_setting(
    client: TestClient, settings: Settings
) -> None:
    """WP AG-1 review round 1 blocker B1: the freshness gate must resolve
    against the LIVE db>env>default settings (``PATCH /v1/settings``), not a
    boot-time snapshot -- the scheduler's own tick already re-resolves
    ``schedule_client_stale_days`` on every tick, and this endpoint
    disagreeing with it after a PATCH would be exactly the "stale claim
    shown as current" dishonesty this field's own docstring promises not to
    commit. Reverting the fix to read the boot snapshot directly makes this
    test fail: 2 days old still reads as fresh under the never-updated
    default of 7 even after the PATCH below."""
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")

    conn = get_connection(settings.db_path)
    try:
        two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        conn.execute(
            "INSERT INTO agent_reports (client_id, reported_at, appids) "
            "VALUES ('zeus', ?, '[440]')",
            (two_days_ago,),
        )
        conn.commit()
    finally:
        conn.close()

    # Baseline: 2 days is fresh under the default 7-day window, on both the
    # list and the detail endpoint.
    game = client.get("/v1/games", headers=AUTH).json()[0]
    assert [entry["client_id"] for entry in game["installed_on"]] == ["zeus"]
    detail = client.get("/v1/games/440", headers=AUTH).json()
    assert [entry["client_id"] for entry in detail["installed_on"]] == ["zeus"]

    patch_response = client.patch(
        "/v1/settings", json={"schedule_client_stale_days": "1"}, headers=AUTH
    )
    assert patch_response.status_code == 200, patch_response.text

    # After the override, 2 days is stale under the new 1-day window -- and
    # this must show up WITHOUT restarting the process, on both endpoints.
    game = client.get("/v1/games", headers=AUTH).json()[0]
    assert game["installed_on"] == []
    detail = client.get("/v1/games/440", headers=AUTH).json()
    assert detail["installed_on"] == []


@pytest.mark.parametrize(
    "reported_at, appids_json",
    [
        pytest.param("2026-08-20T10:00:00Z", "not json{", id="unreadable-snapshot"),
        pytest.param("not-a-timestamp", "[440]", id="unparseable-timestamp"),
    ],
)
def test_installed_on_excludes_the_other_two_fresh_client_snapshots_arms(
    client: TestClient, settings: Settings, reported_at: str, appids_json: str
) -> None:
    """``fresh_client_snapshots`` excludes on THREE grounds (stale, unreadable
    snapshot, unparseable timestamp) -- the staleness tests above only cover
    the first. A corrupt row or a garbage timestamp must degrade
    ``installed_on`` the exact same way, not merely staleness."""
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")

    conn = get_connection(settings.db_path)
    try:
        conn.execute(
            "INSERT INTO agent_reports (client_id, reported_at, appids) "
            "VALUES ('broken-pc', ?, ?)",
            (reported_at, appids_json),
        )
        conn.commit()
    finally:
        conn.close()

    game = client.get("/v1/games", headers=AUTH).json()[0]
    assert game["installed_on"] == []


def test_installed_on_excludes_a_stale_clients_report(
    client: TestClient, settings: Settings
) -> None:
    """A report older than VAULT_SCHEDULE_CLIENT_STALE_DAYS must not appear --
    the exact same freshness gate the scheduler itself refuses to trust for
    sweep targeting (config.DEFAULT_SCHEDULE_CLIENT_STALE_DAYS = 7 days).
    Presenting it anyway would be exactly the "stale claim shown as current"
    dishonesty the work package brief calls out."""
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")

    conn = get_connection(settings.db_path)
    try:
        stale_at = (datetime.now(timezone.utc) - timedelta(days=40)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        conn.execute(
            "INSERT INTO agent_reports (client_id, reported_at, appids) "
            "VALUES ('ancient-pc', ?, '[440]')",
            (stale_at,),
        )
        conn.commit()
    finally:
        conn.close()

    game = client.get("/v1/games", headers=AUTH).json()[0]
    assert game["installed_on"] == []


def test_installed_on_keeps_a_fresh_report_alongside_an_excluded_stale_one(
    client: TestClient, settings: Settings
) -> None:
    """Two clients report the same app: one fresh, one 40 days stale. Only the
    fresh one shows up -- proves the staleness gate is applied per CLIENT, not
    all-or-nothing for the app."""
    _seed_mapping(client, depotid=441, appid=440, app_name="Team Fortress 2")
    _report(client, "gaming-pc", [440])

    conn = get_connection(settings.db_path)
    try:
        stale_at = (datetime.now(timezone.utc) - timedelta(days=40)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        conn.execute(
            "INSERT INTO agent_reports (client_id, reported_at, appids) "
            "VALUES ('ancient-pc', ?, '[440]')",
            (stale_at,),
        )
        conn.commit()
    finally:
        conn.close()

    game = client.get("/v1/games", headers=AUTH).json()[0]
    assert [entry["client_id"] for entry in game["installed_on"]] == ["gaming-pc"]


def test_installed_on_query_cost_scales_with_clients_not_apps(
    client: TestClient, monkeypatch
) -> None:
    """Cost must scale with the number of reporting CLIENTS, not the number of
    mapped apps -- the work package's explicit no-N+1-per-app requirement.
    ``routers/games._installed_on_by_appid`` calls
    ``scheduler.fresh_client_snapshots`` exactly once per request regardless
    of how many apps are being listed."""
    from vault_api import scheduler as scheduler_module

    for index in range(10):
        _seed_mapping(client, depotid=1000 + index, appid=100 + index, app_name=None)
    _report(client, "gaming-pc", list(range(100, 110)))

    calls: list[int] = []
    real = scheduler_module.fresh_client_snapshots

    def counting(conn, now, stale_after_days):
        calls.append(1)
        return real(conn, now, stale_after_days)

    monkeypatch.setattr(scheduler_module, "fresh_client_snapshots", counting)

    response = client.get("/v1/games", headers=AUTH)
    assert response.status_code == 200
    assert len(calls) == 1, "installed_on must not query once per app"
    for game in response.json():
        assert game["installed_on"][0]["client_id"] == "gaming-pc"
