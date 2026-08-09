"""WP 2.4: GET /v1/clients — minimal v1 (agent-report side only)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_agent_reports import make_client, report
from vault_api.db import get_connection, init_db
from vault_api import agent_reports

AUTH = {"X-Api-Key": TEST_API_KEY}


def test_clients_requires_api_key(client: TestClient) -> None:
    assert client.get("/v1/clients").status_code == 401


def test_no_reports_yet_is_an_empty_list(client: TestClient) -> None:
    assert client.get("/v1/clients", headers=AUTH).json() == []


def test_lists_one_row_per_client_with_the_latest_snapshot_size(
    client: TestClient,
) -> None:
    report(client, "gaming-pc", [440, 730, 570])
    report(client, "steam-deck", [440])
    # A second report for one client must not add a second row — and the
    # app_count must follow the LATEST snapshot, not the first one.
    report(client, "gaming-pc", [440, 730])

    body = client.get("/v1/clients", headers=AUTH).json()

    assert [row["client_id"] for row in body] == ["gaming-pc", "steam-deck"]
    gaming_pc, steam_deck = body
    assert gaming_pc["app_count"] == 2
    assert steam_deck["app_count"] == 1
    # first_seen is the oldest retained report, last_reported_at the newest;
    # both are the second-precision ISO strings the rest of the DB uses.
    for row in body:
        assert row["first_seen"].endswith("Z")
        assert row["last_reported_at"] >= row["first_seen"]
    # WP 3.11 (ADR-0008) added the cache-side fields plan §6 always promised.
    # The WP 2.4 fields are all still here, unchanged and in place — that is
    # the forward-compatibility claim that row shape was chosen for.
    assert set(gaming_pc) == {
        "client_id",
        "first_seen",
        "last_reported_at",
        "app_count",
        "source_addrs",
        "cache_hits",
        "cache_misses",
        "bytes_served",
        "last_seen_in_cache_log",
        "bypass_suspected",
    }
    # With no event feed configured there is nothing to correlate, and nobody
    # is accused of anything.
    for row in body:
        assert row["cache_hits"] == 0
        assert row["last_seen_in_cache_log"] is None
        assert row["bypass_suspected"] is False


def test_an_empty_library_reports_app_count_zero(client: TestClient) -> None:
    report(client, "fresh-deck", [])
    row = client.get("/v1/clients", headers=AUTH).json()[0]
    assert row["app_count"] == 0


def test_first_seen_is_the_oldest_RETAINED_report(tmp_path) -> None:
    """Documented consequence of retention, pinned so it can't drift silently."""
    client, settings = make_client(tmp_path, keep=2)

    conn = get_connection(settings.db_path)
    try:
        # Three reports with hand-set timestamps so the pruning is visible.
        for index, stamp in enumerate(
            ("2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z", "2026-08-03T00:00:00Z")
        ):
            conn.execute(
                "INSERT INTO agent_reports (client_id, reported_at, appids) "
                "VALUES ('gaming-pc', ?, ?)",
                (stamp, f"[{440 + index}]"),
            )
        conn.commit()
        agent_reports.prune_reports(conn, "gaming-pc", keep=2)
        conn.commit()
    finally:
        conn.close()

    row = client.get("/v1/clients", headers=AUTH).json()[0]
    assert row["first_seen"] == "2026-08-02T00:00:00Z", "the 08-01 row was pruned"
    assert row["last_reported_at"] == "2026-08-03T00:00:00Z"
    assert row["app_count"] == 1


def test_app_count_is_null_when_the_latest_snapshot_is_unreadable(tmp_path) -> None:
    client, settings = make_client(tmp_path)
    db_path = settings.db_path
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO agent_reports (client_id, reported_at, appids) "
            "VALUES ('gaming-pc', '2026-08-05T10:00:00Z', 'not json{')"
        )
        conn.commit()
    finally:
        conn.close()

    row = client.get("/v1/clients", headers=AUTH).json()[0]
    assert row["app_count"] is None
    assert row["last_reported_at"] == "2026-08-05T10:00:00Z"
