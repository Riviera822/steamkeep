"""WP 2.4: POST /v1/agent/installed — full-list snapshots + ADR-0002 diff."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api import agent_reports, validation
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}
ENDPOINT = "/v1/agent/installed"


def report(client: TestClient, client_id: str, appids: list[int]) -> httpx.Response:
    return client.post(
        ENDPOINT, json={"client_id": client_id, "appids": appids}, headers=AUTH
    )


def make_client(tmp_path, keep: int = 20) -> tuple[TestClient, Settings]:
    """A TestClient with an explicit retention setting (and its Settings)."""
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        agent_report_keep=keep,
    )
    return TestClient(create_app(settings)), settings


def stored_rows(settings: Settings, client_id: str) -> list[list[int]]:
    """Every retained snapshot for a client, oldest first."""
    conn = get_connection(settings.db_path)
    try:
        rows = conn.execute(
            "SELECT appids FROM agent_reports WHERE client_id = ? ORDER BY rowid",
            (client_id,),
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(row["appids"]) for row in rows]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_report_requires_api_key(client: TestClient) -> None:
    response = client.post(ENDPOINT, json={"client_id": "pc", "appids": [440]})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# The diff (ADR-0002)
# ---------------------------------------------------------------------------


def test_first_report_adds_everything_and_removes_nothing(client: TestClient) -> None:
    response = report(client, "gaming-pc", [730, 440, 570])

    assert response.status_code == 200, response.text
    assert response.json() == {
        "client_id": "gaming-pc",
        "received": 3,
        # sorted: the response is a set view, not the request's order
        "added": [440, 570, 730],
        "removed": [],
        "first_report": True,
    }


def test_consecutive_reports_diff_additions_and_removals(client: TestClient) -> None:
    assert report(client, "gaming-pc", [440, 730]).status_code == 200

    second = report(client, "gaming-pc", [440, 570]).json()
    assert second == {
        "client_id": "gaming-pc",
        "received": 2,
        "added": [570],
        "removed": [730],
        "first_report": False,
    }

    # An unchanged library: no news, and still not a "first report".
    third = report(client, "gaming-pc", [570, 440]).json()
    assert third["added"] == []
    assert third["removed"] == []
    assert third["first_report"] is False
    assert third["received"] == 2


def test_empty_library_is_a_legitimate_report_and_removes_everything(
    client: TestClient,
) -> None:
    assert report(client, "steam-deck", [440, 730]).status_code == 200

    response = report(client, "steam-deck", [])

    assert response.status_code == 200, response.text
    assert response.json() == {
        "client_id": "steam-deck",
        "received": 0,
        "added": [],
        "removed": [440, 730],
        "first_report": False,
    }

    # ... and the empty snapshot is really stored, so the NEXT report diffs
    # against it (a re-install shows up as an addition, not as "unchanged").
    back = report(client, "steam-deck", [440]).json()
    assert back == {
        "client_id": "steam-deck",
        "received": 1,
        "added": [440],
        "removed": [],
        "first_report": False,
    }


def test_empty_first_report_is_a_first_report(client: TestClient) -> None:
    response = report(client, "fresh-deck", [])
    assert response.status_code == 200
    assert response.json() == {
        "client_id": "fresh-deck",
        "received": 0,
        "added": [],
        "removed": [],
        "first_report": True,
    }


def test_duplicate_appids_are_deduped(client: TestClient, settings: Settings) -> None:
    response = report(client, "gaming-pc", [440, 440, 730, 440])

    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 2, "received counts DISTINCT app ids"
    assert body["added"] == [440, 730]
    assert stored_rows(settings, "gaming-pc") == [[440, 730]]

    # The dedupe must not look like a change on the next report.
    second = report(client, "gaming-pc", [730, 730, 440]).json()
    assert second["added"] == [] and second["removed"] == []


def test_reports_are_isolated_per_client(client: TestClient) -> None:
    assert report(client, "client-a", [440, 730]).json()["first_report"] is True
    # B's first report must not be diffed against A's snapshot.
    b_first = report(client, "client-b", [570]).json()
    assert b_first == {
        "client_id": "client-b",
        "received": 1,
        "added": [570],
        "removed": [],
        "first_report": True,
    }

    # A's next report diffs against A's own previous one, not against B's.
    a_second = report(client, "client-a", [440]).json()
    assert a_second["removed"] == [730]
    assert a_second["added"] == []

    b_second = report(client, "client-b", [570, 440]).json()
    assert b_second["added"] == [440]
    assert b_second["removed"] == []


# ---------------------------------------------------------------------------
# The ADR-0002 boundary: removals are surfaced, never acted on
# ---------------------------------------------------------------------------


def test_a_removal_is_logged_but_changes_no_app_state(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    client, settings = make_client(tmp_path)

    # A tracked app with a mapping row, a cached depot and status 'done' —
    # exactly the state a removal report must NOT touch (plan A9: deletion
    # stays a human/API decision).
    chunk = tmp_path / "cache" / "depot" / "441" / "chunk"
    chunk.mkdir(parents=True)
    (chunk / "aa").write_bytes(b"x" * 1000)
    conn = get_connection(settings.db_path)
    try:
        conn.execute(
            "INSERT INTO apps (appid, name, status, last_prefill_at) "
            "VALUES (440, 'Team Fortress 2', 'done', '2026-08-05T10:00:00Z')"
        )
        conn.execute("INSERT INTO depot_app_map (depotid, appid) VALUES (441, 440)")
        conn.commit()
    finally:
        conn.close()

    report(client, "gaming-pc", [440, 730])
    with caplog.at_level(logging.INFO, logger="vault_api.agent_reports"):
        response = report(client, "gaming-pc", [730])

    assert response.json()["removed"] == [440]

    audit = [rec.getMessage() for rec in caplog.records if "REMOVED" in rec.getMessage()]
    assert len(audit) == 1, caplog.text
    assert "[440]" in audit[0]
    assert "gaming-pc" in audit[0]
    assert "NOT deleted" in audit[0]

    # Nothing about the server's own state moved.
    detail = client.get("/v1/games/440", headers=AUTH).json()
    assert detail["status"] == "done"
    assert detail["last_prefill_at"] == "2026-08-05T10:00:00Z"
    assert detail["depots"] == [{"depotid": 441, "shared": False, "size_bytes": 1000}]
    assert (chunk / "aa").exists(), "cache content must survive a removal report"
    assert client.get("/v1/jobs", headers=AUTH).json() == [], "nothing may be queued"


def test_a_report_does_not_create_app_rows(client: TestClient) -> None:
    """An agent report is an observation about a CLIENT, not about the cache.

    Creating ``apps`` rows here would make every game installed anywhere show
    up as a tracked (uncached, unmapped) game. Phase 3's scheduler decides what
    to do with the installed list.
    """
    assert report(client, "gaming-pc", [440, 730]).status_code == 200

    assert client.get("/v1/games", headers=AUTH).json() == []
    assert client.get("/v1/games/440", headers=AUTH).status_code == 404


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"appids": [440]}, id="client_id-missing"),
        pytest.param({"client_id": "", "appids": [440]}, id="client_id-empty"),
        pytest.param({"client_id": "x" * 65, "appids": [440]}, id="client_id-too-long"),
        pytest.param({"client_id": "pc\npc", "appids": [440]}, id="client_id-newline"),
        pytest.param({"client_id": "pc\tpc", "appids": [440]}, id="client_id-tab"),
        pytest.param({"client_id": "pc\x00", "appids": [440]}, id="client_id-nul"),
        pytest.param({"client_id": " pc", "appids": [440]}, id="client_id-leading-space"),
        pytest.param({"client_id": "pc ", "appids": [440]}, id="client_id-trailing-space"),
        pytest.param({"client_id": "   ", "appids": [440]}, id="client_id-blank"),
        pytest.param({"client_id": ".", "appids": [440]}, id="client_id-dot"),
        pytest.param({"client_id": "..", "appids": [440]}, id="client_id-dotdot"),
        pytest.param({"client_id": 42, "appids": [440]}, id="client_id-not-a-string"),
        pytest.param({"client_id": "pc"}, id="appids-missing"),
        pytest.param({"client_id": "pc", "appids": None}, id="appids-null"),
        pytest.param({"client_id": "pc", "appids": 440}, id="appids-not-a-list"),
        pytest.param({"client_id": "pc", "appids": [0]}, id="appid-zero"),
        pytest.param({"client_id": "pc", "appids": [-5]}, id="appid-negative"),
        pytest.param({"client_id": "pc", "appids": [1.5]}, id="appid-float"),
        pytest.param({"client_id": "pc", "appids": [True]}, id="appid-true"),
        pytest.param({"client_id": "pc", "appids": [False]}, id="appid-false"),
        pytest.param({"client_id": "pc", "appids": [440, True]}, id="appid-true-mixed"),
        pytest.param({"client_id": "pc", "appids": ["abc"]}, id="appid-not-numeric"),
        pytest.param({"client_id": "pc", "appids": ["0"]}, id="appid-numeric-string-zero"),
        pytest.param({"client_id": "pc", "appids": [440, None]}, id="appid-null"),
        pytest.param(
            {"client_id": "pc", "appids": list(range(1, 10_002))}, id="appids-too-many"
        ),
        pytest.param(
            {"client_id": "pc", "appids": [440], "clientId": "pc"}, id="extra-field"
        ),
    ],
)
def test_invalid_bodies_are_rejected_with_422(client: TestClient, body: dict) -> None:
    response = client.post(ENDPOINT, json=body, headers=AUTH)
    assert response.status_code == 422, response.text


def test_a_missing_appids_field_never_reads_as_an_empty_library(
    client: TestClient, settings: Settings
) -> None:
    """The dangerous 422: omitting ``appids`` must not wipe the client's list.

    If ``appids`` defaulted to ``[]``, a broken agent build would report every
    installed game as removed.
    """
    assert report(client, "gaming-pc", [440, 730]).status_code == 200

    response = client.post(ENDPOINT, json={"client_id": "gaming-pc"}, headers=AUTH)

    assert response.status_code == 422
    assert stored_rows(settings, "gaming-pc") == [[440, 730]], "nothing was stored"


def test_a_numeric_string_appid_is_coerced_like_everywhere_else(
    client: TestClient,
) -> None:
    """Pydantic's default lax mode turns ``"440"`` into ``440``.

    Pinned rather than "fixed": ``POST /v1/prefill`` behaves exactly the same
    way, and an endpoint that is stricter than its neighbour for no stated
    reason is a surprise. A JSON agent cannot produce this anyway; the
    constraint that matters (``>= 1``) is still enforced after the coercion,
    which is why ``"0"`` is in the 422 list above.
    """
    assert report(client, "pc", ["440"]).json()["added"] == [440]  # type: ignore[list-item]
    assert client.post(
        "/v1/prefill", json={"appids": ["440"]}, headers=AUTH
    ).status_code == 202


def test_no_body_endpoint_accepts_a_boolean_as_an_app_id(client: TestClient) -> None:
    """WP 2.4 review (should-fix): ``true`` must not become app id 1.

    ``bool`` is an ``int`` subclass, so Pydantic's lax mode happily accepted a
    JSON ``true`` — and app id 1 is a real Steam app id, so nothing downstream
    would have looked wrong. It also made the WRITE path more permissive than
    this module's own READ path, which already drops booleans when decoding a
    stored snapshot (``test_non_integer_entries_in_a_stored_snapshot_are_ignored``).

    Asserted across **every** body endpoint that takes an app id, because they
    now share one ``AppId`` type (``vault_api/validation.py``) — that shared
    type, not three coincidentally identical annotations, is what keeps their
    coercion semantics from drifting apart again.
    """
    assert client.post(
        ENDPOINT, json={"client_id": "pc", "appids": [True]}, headers=AUTH
    ).status_code == 422
    assert client.post(
        "/v1/prefill", json={"appids": [True]}, headers=AUTH
    ).status_code == 422
    assert client.put(
        "/v1/mapping/441", json={"appid": True}, headers=AUTH
    ).status_code == 422


def test_reject_bool_passes_everything_else_through() -> None:
    assert validation.reject_bool(440) == 440
    assert validation.reject_bool("440") == "440"
    for value in (True, False):
        with pytest.raises(ValueError, match="not a boolean"):
            validation.reject_bool(value)


@pytest.mark.parametrize(
    "client_id",
    ["pc", "x" * 64, "steam-deck-01", "PC (Wohnzimmer)", "täglich-pc", "1", "..."],
)
def test_reasonable_client_ids_are_accepted(client: TestClient, client_id: str) -> None:
    response = report(client, client_id, [440])
    assert response.status_code == 200, response.text
    assert response.json()["client_id"] == client_id


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def test_retention_prunes_the_oldest_snapshots_and_keeps_the_diff_correct(
    tmp_path,
) -> None:
    keep = 3
    client, settings = make_client(tmp_path, keep=keep)

    # keep + 2 reports; each one distinguishable by its single app id.
    for index in range(keep + 2):
        body = report(client, "gaming-pc", [1000 + index]).json()
        assert body["first_report"] is (index == 0)
        if index:
            # The diff still works across a pruning boundary: it only ever
            # needs the immediately preceding snapshot.
            assert body["added"] == [1000 + index]
            assert body["removed"] == [1000 + index - 1]

    rows = stored_rows(settings, "gaming-pc")
    assert rows == [[1002], [1003], [1004]], "oldest two pruned, newest kept in order"


def test_retention_is_per_client(tmp_path) -> None:
    client, settings = make_client(tmp_path, keep=2)

    for index in range(4):
        report(client, "client-a", [1000 + index])
    report(client, "client-b", [7])

    assert stored_rows(settings, "client-a") == [[1002], [1003]]
    assert stored_rows(settings, "client-b") == [[7]], "B's single row is untouched"


def test_prune_reports_clamps_a_nonsense_keep_to_two(tmp_path) -> None:
    """Defense in depth: config enforces >= 2, the helper enforces it again.

    keep=1 would delete the previous snapshot in the same transaction that
    writes the new one, so EVERY report would look like a first report.
    """
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        for appid in (1, 2, 3):
            agent_reports.store_report(conn, "pc", [appid], keep=1)
        rows = conn.execute(
            "SELECT appids FROM agent_reports WHERE client_id = 'pc' ORDER BY rowid"
        ).fetchall()
        assert [json.loads(row["appids"]) for row in rows] == [[2], [3]]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Module-level units
# ---------------------------------------------------------------------------


def test_normalize_appids_sorts_and_dedupes() -> None:
    assert agent_reports.normalize_appids([730, 440, 730]) == [440, 730]
    assert agent_reports.normalize_appids([]) == []


def test_latest_snapshot_uses_insertion_order_not_the_timestamp(tmp_path) -> None:
    """Two reports inside one second must still form a chain.

    ``reported_at`` has second precision, so the second report of a burst can
    carry the SAME timestamp as the first — ordering by it would be ambiguous
    (and a backwards clock step would reorder the chain outright). This test
    writes rows with a deliberately non-monotonic timestamp: the newest row by
    insertion order must still win.
    """
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO agent_reports (client_id, reported_at, appids) "
            "VALUES ('pc', '2026-08-05T10:00:05Z', '[1]')"
        )
        conn.execute(
            "INSERT INTO agent_reports (client_id, reported_at, appids) "
            "VALUES ('pc', '2026-08-05T10:00:05Z', '[2]')"
        )
        conn.execute(
            "INSERT INTO agent_reports (client_id, reported_at, appids) "
            "VALUES ('pc', '2026-08-05T09:59:00Z', '[3]')"
        )
        conn.commit()

        latest = agent_reports.latest_snapshot(conn, "pc")
        assert latest is not None
        assert latest.appids == [3], "newest by rowid, not by timestamp"

        result = agent_reports.store_report(conn, "pc", [3, 4], keep=20)
        assert result.added == [4]
        assert result.removed == []
        assert result.first_report is False
    finally:
        conn.close()


def test_an_unreadable_previous_snapshot_degrades_to_a_first_report(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """A corrupt row must not wedge the endpoint forever.

    It cannot be diffed against, so the chain restarts (``first_report``) and
    the row is reported at WARNING rather than raising a 500 on every report
    from that client from then on.
    """
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO agent_reports (client_id, reported_at, appids) "
            "VALUES ('pc', '2026-08-05T10:00:00Z', 'not json{')"
        )
        conn.commit()

        with caplog.at_level(logging.WARNING, logger="vault_api.agent_reports"):
            result = agent_reports.store_report(conn, "pc", [440], keep=20)

        assert result.first_report is True
        assert result.added == [440]
        assert result.removed == []
        assert "unparseable JSON" in caplog.text

        # The next report has a healthy predecessor again.
        assert agent_reports.store_report(conn, "pc", [730], keep=20).removed == [440]
    finally:
        conn.close()


def test_non_integer_entries_in_a_stored_snapshot_are_ignored(tmp_path) -> None:
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO agent_reports (client_id, reported_at, appids) "
            'VALUES (\'pc\', \'2026-08-05T10:00:00Z\', \'[440, "730", true, 570]\')'
        )
        conn.commit()
        latest = agent_reports.latest_snapshot(conn, "pc")
        assert latest is not None
        # `true` must NOT become app id 1 (bool is an int subclass in Python).
        assert latest.appids == [440, 570]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Concurrency: the diff chain must not fork
# ---------------------------------------------------------------------------


def test_parallel_reports_from_one_client_form_one_unbroken_chain(tmp_path) -> None:
    """Racing reports from the SAME client_id must serialize deterministically.

    Each of the parallel requests posts a library of exactly one, distinct app
    id, so every response identifies itself (``added``) and names its
    predecessor (``removed``). Whatever order the requests land in, the
    outcome must be a single chain:

    * exactly ONE response is a first report (without ``BEGIN IMMEDIATE``
      around read-previous → insert, two requests can both see an empty table
      and both claim it),
    * every other response names exactly one predecessor, and no two responses
      name the SAME predecessor (that would be a fork — one snapshot diffed
      twice, another never diffed at all),
    * following the chain from the first report visits every request exactly
      once, and all N snapshots are on disk (nothing lost, nothing merged).
    """
    parallel = 8
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        agent_report_keep=parallel + 5,  # retention must not hide a lost row
    )
    app = create_app(settings)

    async def run() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as async_client:
            return await asyncio.gather(
                *(
                    async_client.post(
                        ENDPOINT,
                        json={"client_id": "gaming-pc", "appids": [1000 + index]},
                        headers=AUTH,
                    )
                    for index in range(parallel)
                )
            )

    responses = asyncio.run(run())

    assert all(response.status_code == 200 for response in responses), [
        response.text for response in responses
    ]
    bodies = [response.json() for response in responses]

    firsts = [body for body in bodies if body["first_report"]]
    assert len(firsts) == 1, f"exactly one first report expected, got {firsts}"
    assert firsts[0]["removed"] == []

    successor: dict[int, int] = {}
    for body in bodies:
        assert len(body["added"]) == 1, body
        own = body["added"][0]
        if body["first_report"]:
            continue
        assert len(body["removed"]) == 1, body
        predecessor = body["removed"][0]
        assert predecessor != own
        assert predecessor not in successor, f"snapshot {predecessor} diffed twice"
        successor[predecessor] = own

    # Walk the chain: start at the first report, visit every request once.
    visited = [firsts[0]["added"][0]]
    while visited[-1] in successor:
        visited.append(successor[visited[-1]])
    assert sorted(visited) == [1000 + index for index in range(parallel)]
    assert len(visited) == parallel, "the chain must not branch or stop early"

    assert sorted(stored_rows(settings, "gaming-pc")) == [
        [1000 + index] for index in range(parallel)
    ], "every snapshot must be stored exactly once"
