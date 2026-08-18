"""WP 4h.0 (ADR-0010): the privacy gate for the Steam relay's
``playtime_forever`` / ``rtime_last_played`` fields.

Two settings, ``VAULT_RELAY_EXPOSE_PLAYTIME`` / ``VAULT_RELAY_EXPOSE_LAST_PLAYED``
(``vault_api/config.py``), decide whether each field may ever leave
vault-api's process boundary through ``GET /v1/steam/owned-games``. Both are
env-only (ADR-0010: the ``settings`` table lives in a Docker volume that can
be lost, and a privacy opt-out must not have "silently starts collecting
again" as its failure mode) -- there is deliberately no ``PATCH
/v1/settings`` path for either, tested here as a 422 with the distinct
"environment-only" detail (mirroring every other row in
``settings_store.ENV_ONLY_KEYS``).

This file's job, in order of how much damage getting it wrong would do:

1. **Both off by default** -- the field must be genuinely ABSENT from the
   JSON body (dev-tools-proof), not present as ``0``/``null``.
2. **The two keys are independent** -- turning one on must not turn the
   other on, in EITHER direction (a wiring swap between the two settings
   would pass a test that only ever moves them together).
3. **PATCH cannot touch either key.**

``tests/test_steam_relay.py`` keeps its own whitelisting/absence-handling
pins (WP 4a.6r/4h.1) unchanged, using a client with both keys explicitly
turned on (``client_with_relay_exposure_on``) so they stay pins on THAT
pipeline, not on this file's default.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api import steam_relay
from vault_api.config import Settings
from vault_api.main import create_app
from vault_api.routers.steam import OwnedGameOut, OwnedGamesOut

HEADERS = {"X-Api-Key": TEST_API_KEY}
VALID_KEY = "0123456789ABCDEF" * 2
STEAMID = str(steam_relay.STEAM_ID64_BASE)

RECORDED_GAME = {
    "appid": 440,
    "name": "Team Fortress 2",
    "playtime_forever": 1234,
    "img_icon_url": "6b0312cda02f5f777efa2f3318c307ff9acafbb5",
    "rtime_last_played": 1700000000,
}


def _fetcher(document: dict) -> Callable[..., bytes]:
    payload = json.dumps(document).encode("utf-8")

    def fetch(path: str, params: dict, timeout: float | None = None) -> bytes:
        return payload

    return fetch


def _make_client(tmp_path, **overrides: Any) -> TestClient:
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        **overrides,
    )
    return TestClient(create_app(settings))


def _fetch_body(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> dict:
    client.put("/v1/steam/key", headers=HEADERS, json={"key": VALID_KEY})
    monkeypatch.setattr(
        steam_relay,
        "http_fetch",
        _fetcher({"response": {"game_count": 1, "games": [RECORDED_GAME]}}),
    )
    resp = client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": STEAMID})
    assert resp.status_code == 200
    return resp.json()


def _fetch_first_game(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> dict:
    return _fetch_body(client, monkeypatch)["games"][0]


# ==========================================================================
# 1. Off by default -- and genuinely ABSENT, not 0/null
# ==========================================================================


def test_both_fields_are_absent_by_default(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The primary WP 4h.0 pin. A fresh install (no env vars set) must not
    carry either field at all -- ``"playtime_forever" in game`` is the
    assertion a `.get(..., None)` read would NOT catch (a present ``null``
    would also pass that weaker check), which is exactly the dev-tools-proof
    shape the work package asks for.
    """
    game = _fetch_first_game(client, monkeypatch)
    assert "playtime_forever" not in game
    assert "rtime_last_played" not in game
    # Fields never touched by this gate are unaffected.
    assert game["appid"] == 440
    assert game["name"] == "Team Fortress 2"


def test_default_settings_object_has_both_keys_off() -> None:
    """Unit-level pin one layer down, independent of any HTTP/router
    plumbing -- flip either ``DEFAULT_RELAY_EXPOSE_*`` constant and this
    dies without needing a TestClient at all."""
    settings = Settings(vault_api_key="x", db_path="x", cache_root="x", log_level="INFO")
    assert settings.relay_expose_playtime is False
    assert settings.relay_expose_last_played is False


# ==========================================================================
# 2. Independence -- each key gates ONLY its own field, both directions
# ==========================================================================


def test_playtime_on_last_played_off(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(
        tmp_path, relay_expose_playtime=True, relay_expose_last_played=False
    )
    game = _fetch_first_game(client, monkeypatch)
    assert game["playtime_forever"] == 1234
    assert "rtime_last_played" not in game


def test_last_played_on_playtime_off(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The mirror image of the test above -- a test suite that only ever
    flipped both keys TOGETHER would not notice a wiring swap (e.g. the
    router accidentally reading ``relay_expose_playtime`` to decide whether
    to include ``rtime_last_played``, or vice versa); this and the test
    above each move exactly one key.
    """
    client = _make_client(
        tmp_path, relay_expose_playtime=False, relay_expose_last_played=True
    )
    game = _fetch_first_game(client, monkeypatch)
    assert "playtime_forever" not in game
    assert game["rtime_last_played"] == 1700000000


def test_both_on(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(
        tmp_path, relay_expose_playtime=True, relay_expose_last_played=True
    )
    game = _fetch_first_game(client, monkeypatch)
    assert game["playtime_forever"] == 1234
    assert game["rtime_last_played"] == 1700000000


def test_both_off_explicitly(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same outcome as the fresh-install default, but with both
    ``Settings`` fields explicitly passed as ``False`` rather than left to
    their dataclass default -- proves the router reads the FIELD VALUE
    (whichever way it got there), not e.g. "was anything passed to
    ``Settings(...)`` at all". This does NOT exercise ``_env_bool`` or any
    other env-parsing grammar -- ``_make_client`` builds a ``Settings``
    object directly, no environment variable is read. The env-parsing
    coverage for these two keys (accepted spellings, rejection, defaulting)
    lives in ``test_config.py``'s
    ``test_relay_expose_playtime_accepts_false_spellings``/
    ``test_relay_expose_last_played_accepts_false_spellings`` and their
    sibling parametrisations.
    """
    client = _make_client(
        tmp_path, relay_expose_playtime=False, relay_expose_last_played=False
    )
    game = _fetch_first_game(client, monkeypatch)
    assert "playtime_forever" not in game
    assert "rtime_last_played" not in game


# ==========================================================================
# 3. Structural pin against the response_model_exclude_unset=True blast
#    radius (reviewer S2)
#
# `get_owned_games`'s `response_model_exclude_unset=True` drops ANY field
# nobody explicitly passed when constructing the response models -- not
# just the two gated ones. `OwnedGamesOut.configured: bool = True` is a
# live example: the pre-fix code never passed it explicitly, so it would
# have silently vanished from the JSON body too, and nothing but one
# incidental `body["configured"] is True` assertion would have caught it.
# The tests above only check the two gated fields by name; they would NOT
# notice a THIRD field going missing. This asserts the actual response's
# key set against the Pydantic models' OWN field sets (`model_fields`),
# derived, never hand-typed -- so a newly added defaulted field the router
# forgets to pass explicitly fails this test the day it lands, rather than
# silently shipping a body one key short of its own schema.
# ==========================================================================


@pytest.mark.parametrize(
    "expose_playtime,expose_last_played",
    [(False, False), (True, False), (False, True), (True, True)],
)
def test_response_key_sets_match_the_models_exactly(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    expose_playtime: bool,
    expose_last_played: bool,
) -> None:
    client = _make_client(
        tmp_path,
        relay_expose_playtime=expose_playtime,
        relay_expose_last_played=expose_last_played,
    )
    body = _fetch_body(client, monkeypatch)

    # Top-level: configured/game_count/games are ALWAYS passed explicitly by
    # get_owned_games (never gated) -- the full model field set, no fewer.
    assert set(body.keys()) == set(OwnedGamesOut.model_fields)

    gated_off = set()
    if not expose_playtime:
        gated_off.add("playtime_forever")
    if not expose_last_played:
        gated_off.add("rtime_last_played")
    expected_game_keys = set(OwnedGameOut.model_fields) - gated_off
    assert set(body["games"][0].keys()) == expected_game_keys


# ==========================================================================
# 4. Neither key is DB-overridable
# ==========================================================================


@pytest.mark.parametrize("key", ["relay_expose_playtime", "relay_expose_last_played"])
def test_patch_is_rejected_as_environment_only(client: TestClient, key: str) -> None:
    response = client.patch("/v1/settings", json={key: "true"}, headers=HEADERS)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "environment-only" in detail
    assert "not a recognised setting" not in detail


@pytest.mark.parametrize("key", ["relay_expose_playtime", "relay_expose_last_played"])
def test_get_settings_reports_the_key_as_env_only_and_off_by_default(
    client: TestClient, key: str
) -> None:
    body = client.get("/v1/settings", headers=HEADERS).json()
    row = next(row for row in body["settings"] if row["key"] == key)
    assert row["env_only"] is True
    assert row["effective"] is False
    assert row["source"] == "default"
    assert row["applies"] == "restart-required"


def test_get_settings_reports_env_value_when_one_key_is_turned_on(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates an operator who set ``VAULT_RELAY_EXPOSE_PLAYTIME=true`` --
    ``source`` must flip to ``"env"`` for that key while the untouched
    sibling stays ``"default"``, pinning independence at the settings-API
    layer too (not only at the router, see the tests above).

    Unlike the other tests in this file, this goes through
    ``Settings.from_env()`` (via real OS environment variables) rather than
    hand-constructing a ``Settings`` object: ``describe_settings``'s
    ``source`` for an ``ENV_ONLY_INFO_KEYS`` row is computed from
    ``os.environ`` directly (``settings_store._env_var_is_set``), not from
    comparing the resolved value to the built-in default -- a hand-built
    ``Settings(relay_expose_playtime=True)`` with no matching env var set
    would still (correctly) report ``"default"`` here.
    """
    monkeypatch.setenv("VAULT_API_KEY", TEST_API_KEY)
    monkeypatch.setenv("VAULT_DB_PATH", str(tmp_path / "vault.db"))
    monkeypatch.setenv("VAULT_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("VAULT_RELAY_EXPOSE_PLAYTIME", "true")
    monkeypatch.delenv("VAULT_RELAY_EXPOSE_LAST_PLAYED", raising=False)

    client = TestClient(create_app(Settings.from_env()))
    body = client.get("/v1/settings", headers=HEADERS).json()

    playtime_row = next(
        row for row in body["settings"] if row["key"] == "relay_expose_playtime"
    )
    assert playtime_row["effective"] is True
    assert playtime_row["source"] == "env"

    last_played_row = next(
        row for row in body["settings"] if row["key"] == "relay_expose_last_played"
    )
    assert last_played_row["effective"] is False
    assert last_played_row["source"] == "default"
