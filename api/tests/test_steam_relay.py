"""Opt-in Steam Web API relay (``vault_api/steam_relay.py`` +
``vault_api/routers/steam.py``, WP 4a.6r; ADR-0004 addendum, user decision
A+C).

**No test in this file touches the network.** Every upstream response is a
*recorded fixture* — a synthetic document modeled on Valve's documented
``GetOwnedGames``/``GetPlayerSummaries`` shapes — injected by monkeypatching
``steam_relay.http_fetch`` at the module level, exactly the pattern
``tests/test_oracle.py`` uses for the manifest oracle (``docs/LEARNINGS.md``:
fixtures are synthetic, modeled on real structure, never personal data).

The properties this file exists to pin, in order of how much damage getting
them wrong would do:

1. **Off by default, and a DISTINCT response while off.** No key configured
   means every relay call answers ``409``, never a silent empty success. This
   is the mutation pin the work package asks for: flip the router to default
   "configured" and ``test_relay_is_off_by_default_returns_409_not_success``
   dies.
2. **The key is never echoed, logged, or leaked into an error.** Grep-style
   pins across the JSON body, the raw response text and captured log output.
3. **Hostile input — from the caller (steamid) and from Valve (the JSON
   body) — never crashes and never reaches a client unfiltered.**
"""

from __future__ import annotations

import json
import logging
import urllib.error
from typing import Any, Callable
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api import steam_relay
from vault_api.db import get_connection, init_db

HEADERS = {"X-Api-Key": TEST_API_KEY}

#: A syntactically valid Steam Web API key. Not a real one — Steam Web API
#: keys are opaque 32-char hex strings with no meaningful "real" example
#: worth avoiding (docs/LEARNINGS.md fixtures rule is about personal data,
#: not about this).
VALID_KEY = "0123456789ABCDEF" * 2
OTHER_KEY = "FEDCBA9876543210" * 2

STEAMID_MIN = str(steam_relay.STEAM_ID64_BASE)
STEAMID_MAX = str(steam_relay.STEAM_ID64_MAX)
REQUESTED_STEAMID = steam_relay.STEAM_ID64_BASE + 100


def encode(document: Any) -> bytes:
    return json.dumps(document).encode("utf-8")


def fetcher(payload: bytes) -> Callable[[str, dict], bytes]:
    def fetch(path: str, params: dict, timeout: float | None = None) -> bytes:
        return payload

    return fetch


def failing_fetcher(message: str = "boom") -> Callable[[str, dict], bytes]:
    def fetch(path: str, params: dict, timeout: float | None = None) -> bytes:
        raise steam_relay.SteamRelayError(message)

    return fetch


RECORDED_OWNED_GAMES = {
    "response": {
        "game_count": 2,
        "games": [
            {
                "appid": 440,
                "name": "Team Fortress 2",
                "playtime_forever": 1234,
                "img_icon_url": "6b0312cda02f5f777efa2f3318c307ff9acafbb5",
                "playtime_windows_forever": 1234,
            },
            {
                "appid": 730,
                "name": "Counter-Strike 2",
                "playtime_forever": 0,
                "img_icon_url": "aaa111",
            },
        ],
    }
}

RECORDED_PLAYER_SUMMARIES = {
    "response": {
        "players": [
            {
                "steamid": str(REQUESTED_STEAMID),
                "communityvisibilitystate": 3,
                "personaname": "Robin",
                "avatar": "https://avatars.example/a.jpg",
                "avatarmedium": "https://avatars.example/b.jpg",
                "avatarfull": "https://avatars.example/c.jpg",
                "personastate": 1,
            }
        ]
    }
}


# ==========================================================================
# 1. Validators — hostile input from the CALLER
# ==========================================================================


class TestValidSteamWebApiKey:
    def test_accepts_a_well_formed_key(self) -> None:
        assert steam_relay.valid_steam_web_api_key(VALID_KEY) == VALID_KEY

    def test_normalises_lowercase_to_uppercase(self) -> None:
        assert steam_relay.valid_steam_web_api_key(VALID_KEY.lower()) == VALID_KEY

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "short",
            VALID_KEY + "0",  # 33 chars
            VALID_KEY[:-1],  # 31 chars
            VALID_KEY[:-1] + "G",  # non-hex character
            VALID_KEY[:-1] + " ",  # whitespace
            "٠" * 32,  # non-ASCII digit look-alikes
            123456,
            None,
            True,
        ],
    )
    def test_rejects_everything_else(self, value: object) -> None:
        assert steam_relay.valid_steam_web_api_key(value) is None

    def test_mask_key_reveals_only_the_last_four_characters(self) -> None:
        masked = steam_relay.mask_key(VALID_KEY)
        assert masked == VALID_KEY[-4:]
        assert VALID_KEY[:-4] not in masked
        assert len(masked) == 4


class TestValidSteamId64:
    def test_accepts_the_boundary_values(self) -> None:
        assert steam_relay.valid_steamid64(STEAMID_MIN) == steam_relay.STEAM_ID64_BASE
        assert steam_relay.valid_steamid64(STEAMID_MAX) == steam_relay.STEAM_ID64_MAX

    @pytest.mark.parametrize(
        "value",
        [
            str(steam_relay.STEAM_ID64_BASE - 1),  # one below the range
            str(steam_relay.STEAM_ID64_MAX + 1),  # one above the range
            "1234567890123456",  # 16 digits
            "123456789012345678",  # 18 digits
            " 76561197960265728",  # leading whitespace
            "76561197960265728 ",  # trailing whitespace
            "+7656119796026572",  # sign, wrong length either way
            "7656119796026572a",  # non-digit tail
            "٧٦٥٦١١٩٧٩٦٠٢٦٥٧٢٨",  # non-ASCII (Arabic-Indic) digits
            "",
            76561197960265728,  # an int, not a str
            None,
            True,
        ],
    )
    def test_rejects_everything_else(self, value: object) -> None:
        assert steam_relay.valid_steamid64(value) is None


# ==========================================================================
# 2. GetOwnedGames parsing — hostile input from VALVE
# ==========================================================================


def test_parses_a_well_formed_owned_games_response() -> None:
    result = steam_relay.parse_owned_games(encode(RECORDED_OWNED_GAMES))
    assert result.game_count == 2
    assert [g.appid for g in result.games] == [440, 730]
    assert result.games[0].name == "Team Fortress 2"
    assert result.games[0].playtime_forever == 1234
    assert result.games[0].img_icon_url == "6b0312cda02f5f777efa2f3318c307ff9acafbb5"


def test_only_whitelisted_fields_survive_owned_games() -> None:
    """A field outside the work package's boundary list must never reach the
    parsed result, however innocuous it looks.

    ``rtime_last_played`` moved INTO the whitelist in WP 4h.1 (it used to be
    the probe for this exact test) — it is asserted separately below in
    ``test_rtime_last_played_survives_when_present``. ``playtime_2weeks`` and
    ``has_community_visible_stats`` remain genuinely out of scope and are the
    probes here now.
    """
    doc = {
        "response": {
            "games": [
                {
                    "appid": 1,
                    "name": "n",
                    "playtime_forever": 1,
                    "img_icon_url": "i",
                    "playtime_2weeks": 999,
                    "has_community_visible_stats": True,
                }
            ]
        }
    }
    result = steam_relay.parse_owned_games(encode(doc))
    assert result.games == (steam_relay.OwnedGame(appid=1, name="n", playtime_forever=1, img_icon_url="i"),)


# ==========================================================================
# 2b. rtime_last_played — WP 4h.1 pin 1 ("absence is not zero")
# ==========================================================================


def test_rtime_last_played_survives_when_present() -> None:
    doc = {
        "response": {
            "games": [
                {
                    "appid": 1,
                    "name": "n",
                    "playtime_forever": 1,
                    "img_icon_url": "i",
                    "rtime_last_played": 1700000000,
                }
            ]
        }
    }
    result = steam_relay.parse_owned_games(encode(doc))
    assert result.games[0].rtime_last_played == 1700000000


def test_rtime_last_played_is_null_when_the_key_is_entirely_absent() -> None:
    """The boundary case pin 1 exists for: a private profile, a game never
    played, or an account viewed without full permissions all omit the key
    entirely — must surface as None, never a manufactured 0."""
    doc = {
        "response": {
            "games": [{"appid": 1, "name": "n", "playtime_forever": 0, "img_icon_url": ""}]
        }
    }
    result = steam_relay.parse_owned_games(encode(doc))
    assert result.games[0].rtime_last_played is None


def test_rtime_last_played_explicit_zero_is_null_not_a_real_timestamp() -> None:
    """0 would render as 1970-01-01 -- decades before Steam existed. An
    upstream 0 is treated exactly like an absent key (pin 1's 'absence is
    not zero' extended to 'an implausible sentinel is not a real value
    either')."""
    doc = {
        "response": {
            "games": [
                {"appid": 1, "name": "n", "playtime_forever": 0, "img_icon_url": "", "rtime_last_played": 0}
            ]
        }
    }
    result = steam_relay.parse_owned_games(encode(doc))
    assert result.games[0].rtime_last_played is None


@pytest.mark.parametrize(
    "value",
    [
        -1,  # negative
        True,  # bool must not sneak through as 1 (WP 2.4 house rule)
        "1700000000",  # a string, not Valve's native int
        1700000000.5,  # float, not int
        None,
    ],
)
def test_rtime_last_played_garbage_degrades_to_null_not_a_crash(value: object) -> None:
    doc = {
        "response": {
            "games": [
                {
                    "appid": 1,
                    "name": "n",
                    "playtime_forever": 0,
                    "img_icon_url": "",
                    "rtime_last_played": value,
                }
            ]
        }
    }
    result = steam_relay.parse_owned_games(encode(doc))
    assert result.games[0].rtime_last_played is None


def test_a_private_profile_answers_with_no_games_key_and_is_not_an_error() -> None:
    result = steam_relay.parse_owned_games(encode({"response": {"game_count": 0}}))
    assert result.game_count == 0
    assert result.games == ()
    assert result.warnings == ()


def test_missing_response_object_is_a_relay_error() -> None:
    with pytest.raises(steam_relay.SteamRelayError):
        steam_relay.parse_owned_games(encode({"nope": True}))


def test_a_json_array_at_the_top_level_is_a_relay_error() -> None:
    with pytest.raises(steam_relay.SteamRelayError):
        steam_relay.parse_owned_games(encode([1, 2, 3]))


def test_non_json_bytes_are_a_relay_error_not_a_crash() -> None:
    with pytest.raises(steam_relay.SteamRelayError):
        steam_relay.parse_owned_games(b"<html>not json</html>")


def test_deeply_nested_json_is_refused_not_a_stack_crash() -> None:
    """docs/LEARNINGS.md (WP 2.1/3.9): CPython's json scanner recurses per
    nesting level; a bounded body is not automatically a bounded parse."""
    payload = (b"[" * 20000) + (b"]" * 20000)
    with pytest.raises(steam_relay.SteamRelayError):
        steam_relay.parse_owned_games(payload)


def test_an_oversized_response_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(steam_relay, "MAX_RESPONSE_BYTES", 10)
    with pytest.raises(steam_relay.SteamRelayError):
        steam_relay.parse_owned_games(encode(RECORDED_OWNED_GAMES))


def test_a_game_entry_with_no_usable_appid_is_skipped_not_fatal() -> None:
    doc = {
        "response": {
            "games": [
                {"name": "no appid here"},
                {"appid": True, "name": "bool is not an id"},
                {"appid": 10, "name": "ok", "playtime_forever": 5, "img_icon_url": "x"},
            ]
        }
    }
    result = steam_relay.parse_owned_games(encode(doc))
    assert [g.appid for g in result.games] == [10]
    assert result.warnings


def test_a_non_object_game_entry_is_skipped_not_fatal() -> None:
    doc = {"response": {"games": ["not an object", 42, None]}}
    result = steam_relay.parse_owned_games(encode(doc))
    assert result.games == ()
    assert len(result.warnings) == 3


def test_more_than_max_games_is_truncated_with_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(steam_relay, "MAX_GAMES", 3)
    games = [
        {"appid": i, "name": f"g{i}", "playtime_forever": 0, "img_icon_url": ""}
        for i in range(1, 6)
    ]
    result = steam_relay.parse_owned_games(encode({"response": {"games": games}}))
    assert len(result.games) == 3
    assert any("more than" in warning for warning in result.warnings)


def test_a_missing_or_wrong_type_name_degrades_to_empty_string() -> None:
    doc = {
        "response": {
            "games": [
                {"appid": 1, "playtime_forever": 0, "img_icon_url": ""},
                {"appid": 2, "name": 12345, "playtime_forever": 0, "img_icon_url": ""},
            ]
        }
    }
    result = steam_relay.parse_owned_games(encode(doc))
    assert [g.name for g in result.games] == ["", ""]


def test_negative_playtime_degrades_to_zero_not_a_negative_number() -> None:
    doc = {"response": {"games": [{"appid": 1, "name": "n", "playtime_forever": -5, "img_icon_url": ""}]}}
    result = steam_relay.parse_owned_games(encode(doc))
    assert result.games[0].playtime_forever == 0


def test_bool_playtime_is_rejected_not_coerced_to_one() -> None:
    """docs/LEARNINGS.md (WP 2.4): True == 1 must never sneak through as a count."""
    doc = {"response": {"games": [{"appid": 1, "name": "n", "playtime_forever": True, "img_icon_url": ""}]}}
    result = steam_relay.parse_owned_games(encode(doc))
    assert result.games[0].playtime_forever == 0


def test_an_oversized_icon_hash_degrades_to_empty_string() -> None:
    doc = {
        "response": {
            "games": [{"appid": 1, "name": "n", "playtime_forever": 0, "img_icon_url": "x" * 999}]
        }
    }
    result = steam_relay.parse_owned_games(encode(doc))
    assert result.games[0].img_icon_url == ""


# ==========================================================================
# 3. GetPlayerSummaries parsing — hostile input from VALVE
# ==========================================================================


def test_parses_a_well_formed_player_summaries_response() -> None:
    result = steam_relay.parse_player_summaries(encode(RECORDED_PLAYER_SUMMARIES), REQUESTED_STEAMID)
    assert len(result.players) == 1
    player = result.players[0]
    assert player.steamid == str(REQUESTED_STEAMID)
    assert player.personaname == "Robin"
    assert player.avatar == "https://avatars.example/a.jpg"
    assert player.personastate == 1


def test_a_mismatched_steamid_is_dropped_not_trusted() -> None:
    """The cross-check ``oracle._app_object`` applies to appid, mirrored here:
    an answer that is not about the requested account must never be
    attributed to it."""
    other = steam_relay.STEAM_ID64_BASE + 999
    doc = {
        "response": {
            "players": [{**RECORDED_PLAYER_SUMMARIES["response"]["players"][0], "steamid": str(other)}]
        }
    }
    result = steam_relay.parse_player_summaries(encode(doc), REQUESTED_STEAMID)
    assert result.players == ()
    assert result.warnings


def test_a_non_http_avatar_url_is_dropped() -> None:
    doc = {
        "response": {
            "players": [
                {**RECORDED_PLAYER_SUMMARIES["response"]["players"][0], "avatar": "javascript:alert(1)"}
            ]
        }
    }
    result = steam_relay.parse_player_summaries(encode(doc), REQUESTED_STEAMID)
    assert result.players[0].avatar is None


def test_empty_players_list_is_not_an_error() -> None:
    result = steam_relay.parse_player_summaries(encode({"response": {"players": []}}), REQUESTED_STEAMID)
    assert result.players == ()


def test_missing_response_object_is_a_relay_error_for_summaries() -> None:
    with pytest.raises(steam_relay.SteamRelayError):
        steam_relay.parse_player_summaries(encode({}), REQUESTED_STEAMID)


def test_a_non_int_personastate_degrades_to_none() -> None:
    doc = {
        "response": {
            "players": [{**RECORDED_PLAYER_SUMMARIES["response"]["players"][0], "personastate": "online"}]
        }
    }
    result = steam_relay.parse_player_summaries(encode(doc), REQUESTED_STEAMID)
    assert result.players[0].personastate is None


# ==========================================================================
# 4. Fetching internals — bounded, no redirects, key never in an error message
# ==========================================================================


def test_redirects_are_never_followed() -> None:
    handler = steam_relay._RefuseRedirects()
    result = handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/steal")
    assert result is None


def test_redacted_url_never_contains_a_query_string() -> None:
    redacted = steam_relay._redacted_url(steam_relay.OWNED_GAMES_PATH)
    assert "?" not in redacted
    assert redacted == steam_relay.STEAM_API_BASE + steam_relay.OWNED_GAMES_PATH


def test_http_fetch_error_messages_never_contain_the_key_or_query_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom_open(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(steam_relay._OPENER, "open", boom_open)

    with pytest.raises(steam_relay.SteamRelayError) as excinfo:
        steam_relay.http_fetch(
            steam_relay.OWNED_GAMES_PATH, {"key": VALID_KEY, "steamid": STEAMID_MIN}
        )

    message = str(excinfo.value)
    assert VALID_KEY not in message
    assert "key=" not in message
    assert STEAMID_MIN not in message


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self, n: int) -> bytes:
        return self._body[:n]

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def test_http_fetch_enforces_the_response_size_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(steam_relay, "MAX_RESPONSE_BYTES", 10)
    monkeypatch.setattr(
        steam_relay._OPENER, "open", lambda request, timeout=None: _FakeResponse(b"x" * 100)
    )
    with pytest.raises(steam_relay.SteamRelayError):
        steam_relay.http_fetch(steam_relay.OWNED_GAMES_PATH, {"key": VALID_KEY})


def test_http_fetch_raises_on_a_non_200_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        steam_relay._OPENER, "open", lambda request, timeout=None: _FakeResponse(b"{}", status=403)
    )
    with pytest.raises(steam_relay.SteamRelayError, match="403"):
        steam_relay.http_fetch(steam_relay.OWNED_GAMES_PATH, {"key": VALID_KEY})


@pytest.mark.parametrize(
    "path, expected_path",
    [
        (steam_relay.OWNED_GAMES_PATH, "/IPlayerService/GetOwnedGames/v1/"),
        (steam_relay.PLAYER_SUMMARIES_PATH, "/ISteamUser/GetPlayerSummaries/v2/"),
    ],
)
def test_http_fetch_pins_scheme_host_and_path_against_string_literals(
    monkeypatch: pytest.MonkeyPatch, path: str, expected_path: str
) -> None:
    """The request ``http_fetch`` actually hands to ``urllib`` must be HTTPS,
    to ``api.steampowered.com``, and to exactly the documented Valve path —
    asserted against STRING LITERALS, not the module's own
    ``STEAM_API_HOST``/``STEAM_API_BASE`` constants, so a mutation of either
    constant (e.g. ``STEAM_API_HOST = "attacker.example"``, or the scheme
    silently downgrading from ``https`` to ``http``) is caught here instead
    of trivially passing because the assertion and the mutated value drift
    together.
    """
    captured: dict[str, object] = {}

    def capture_open(request: object, timeout: float | None = None) -> _FakeResponse:
        captured["request"] = request
        return _FakeResponse(json.dumps({"response": {}}).encode("utf-8"))

    monkeypatch.setattr(steam_relay._OPENER, "open", capture_open)

    steam_relay.http_fetch(path, {"key": VALID_KEY, "steamid": STEAMID_MIN})

    request = captured["request"]
    parts = urlsplit(request.full_url)  # type: ignore[attr-defined]
    assert parts.scheme == "https"
    assert parts.hostname == "api.steampowered.com"
    assert parts.path == expected_path


# ==========================================================================
# 5. Key storage — re-validated on the way out (LEARNINGS: the db is a file
#    an operator can edit)
# ==========================================================================


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    connection = get_connection(db_path)
    try:
        yield connection
    finally:
        connection.close()


def test_key_is_unconfigured_by_default(conn) -> None:
    assert steam_relay.get_key(conn) is None
    assert steam_relay.key_status(conn) == (False, None)


def test_key_round_trips(conn) -> None:
    steam_relay.set_key(conn, VALID_KEY)
    assert steam_relay.get_key(conn) == VALID_KEY
    assert steam_relay.key_status(conn) == (True, VALID_KEY[-4:])

    steam_relay.clear_key(conn)
    assert steam_relay.get_key(conn) is None
    assert steam_relay.key_status(conn) == (False, None)


def test_setting_twice_replaces_not_duplicates(conn) -> None:
    steam_relay.set_key(conn, VALID_KEY)
    steam_relay.set_key(conn, OTHER_KEY)
    assert steam_relay.get_key(conn) == OTHER_KEY
    (count,) = conn.execute("SELECT COUNT(*) FROM steam_relay_key").fetchone()
    assert count == 1


def test_clearing_when_nothing_is_set_is_a_no_op(conn) -> None:
    steam_relay.clear_key(conn)  # must not raise
    assert steam_relay.get_key(conn) is None


def test_a_hand_edited_invalid_key_is_treated_as_not_configured(conn) -> None:
    """The database is a file an operator can edit; ``get_key`` re-validates
    on the way out (mirrors ``oracle.gc_keepset_gids``'s reasoning) rather
    than sending a corrupted value to Valve."""
    conn.execute(
        "INSERT INTO steam_relay_key (id, api_key, updated_at) VALUES (1, ?, ?)",
        ("not-a-valid-hex-key", "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    assert steam_relay.get_key(conn) is None
    assert steam_relay.key_status(conn) == (False, None)


# ==========================================================================
# 6. The HTTP surface
# ==========================================================================


def test_a_missing_steamid_query_param_is_a_422(client: TestClient) -> None:
    client.put("/v1/steam/key", headers=HEADERS, json={"key": VALID_KEY})
    assert client.get("/v1/steam/owned-games", headers=HEADERS).status_code == 422
    assert client.get("/v1/steam/player-summaries", headers=HEADERS).status_code == 422


def test_all_five_routes_require_the_api_key(client: TestClient) -> None:
    assert client.get("/v1/steam/key").status_code == 401
    assert client.put("/v1/steam/key", json={"key": VALID_KEY}).status_code == 401
    assert client.delete("/v1/steam/key").status_code == 401
    assert client.get("/v1/steam/owned-games", params={"steamid": STEAMID_MIN}).status_code == 401
    assert (
        client.get("/v1/steam/player-summaries", params={"steamid": STEAMID_MIN}).status_code
        == 401
    )


def test_key_status_defaults_to_unconfigured(client: TestClient) -> None:
    resp = client.get("/v1/steam/key", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"configured": False, "key_last4": None}


def test_set_get_delete_key_round_trip_over_http(client: TestClient) -> None:
    put = client.put("/v1/steam/key", headers=HEADERS, json={"key": VALID_KEY})
    assert put.status_code == 200
    assert put.json() == {"configured": True, "key_last4": VALID_KEY[-4:]}
    assert VALID_KEY not in put.text

    status_resp = client.get("/v1/steam/key", headers=HEADERS)
    assert status_resp.json() == {"configured": True, "key_last4": VALID_KEY[-4:]}
    assert VALID_KEY not in status_resp.text

    delete_resp = client.delete("/v1/steam/key", headers=HEADERS)
    assert delete_resp.status_code == 204

    final = client.get("/v1/steam/key", headers=HEADERS)
    assert final.json() == {"configured": False, "key_last4": None}


@pytest.mark.parametrize(
    "bad_key",
    ["", "short", VALID_KEY[:-1], VALID_KEY + "0", VALID_KEY[:-1] + "G", "  " + VALID_KEY[2:]],
)
def test_setting_an_invalid_key_is_a_422(client: TestClient, bad_key: str) -> None:
    resp = client.put("/v1/steam/key", headers=HEADERS, json={"key": bad_key})
    assert resp.status_code == 422


def test_relay_is_off_by_default_returns_409_not_success(client: TestClient) -> None:
    """The mutation pin: if the "is a key configured" check is ever skipped
    or defaulted to True, this test dies."""
    owned = client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": STEAMID_MIN})
    assert owned.status_code == 409

    summaries = client.get(
        "/v1/steam/player-summaries", headers=HEADERS, params={"steamid": STEAMID_MIN}
    )
    assert summaries.status_code == 409


def test_clearing_the_key_immediately_reverts_to_409_even_with_a_warm_cache(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cached answer from before the key was cleared must never make an
    unconfigured relay look configured — the router checks the key BEFORE
    consulting the cache."""
    client.put("/v1/steam/key", headers=HEADERS, json={"key": VALID_KEY})
    monkeypatch.setattr(steam_relay, "http_fetch", fetcher(encode(RECORDED_OWNED_GAMES)))

    ok = client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": STEAMID_MIN})
    assert ok.status_code == 200

    client.delete("/v1/steam/key", headers=HEADERS)

    after = client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": STEAMID_MIN})
    assert after.status_code == 409


@pytest.mark.parametrize(
    "steamid",
    [
        "0",
        "abc",
        "1" * 16,
        "1" * 18,
        str(steam_relay.STEAM_ID64_BASE - 1),
        "",
    ],
)
def test_a_bad_steamid_is_a_422_even_with_a_key_configured(client: TestClient, steamid: str) -> None:
    client.put("/v1/steam/key", headers=HEADERS, json={"key": VALID_KEY})
    resp = client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": steamid})
    assert resp.status_code == 422
    resp2 = client.get(
        "/v1/steam/player-summaries", headers=HEADERS, params={"steamid": steamid}
    )
    assert resp2.status_code == 422


def test_owned_games_end_to_end_with_a_recorded_fixture(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.put("/v1/steam/key", headers=HEADERS, json={"key": VALID_KEY})
    monkeypatch.setattr(steam_relay, "http_fetch", fetcher(encode(RECORDED_OWNED_GAMES)))

    resp = client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": STEAMID_MIN})
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["game_count"] == 2
    assert body["games"][0] == {
        "appid": 440,
        "name": "Team Fortress 2",
        "playtime_forever": 1234,
        "img_icon_url": "6b0312cda02f5f777efa2f3318c307ff9acafbb5",
        # RECORDED_OWNED_GAMES's first entry has no rtime_last_played key at
        # all -> null, never a manufactured 0 (WP 4h.1 pin 1).
        "rtime_last_played": None,
    }
    assert VALID_KEY not in resp.text


def test_owned_games_rtime_last_played_round_trips_over_http(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end pin against the REAL Pydantic response model
    (``routers.steam.OwnedGameOut``), not a hand-written dict: a present
    value survives, an absent one is null -- both in the SAME response."""
    client.put("/v1/steam/key", headers=HEADERS, json={"key": VALID_KEY})
    doc = {
        "response": {
            "game_count": 2,
            "games": [
                {
                    "appid": 1,
                    "name": "Played",
                    "playtime_forever": 10,
                    "img_icon_url": "",
                    "rtime_last_played": 1700000000,
                },
                {
                    "appid": 2,
                    "name": "Never touched",
                    "playtime_forever": 0,
                    "img_icon_url": "",
                },
            ],
        }
    }
    monkeypatch.setattr(steam_relay, "http_fetch", fetcher(encode(doc)))

    resp = client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": STEAMID_MIN})
    assert resp.status_code == 200
    games = resp.json()["games"]
    assert games[0]["rtime_last_played"] == 1700000000
    assert games[1]["rtime_last_played"] is None


def test_player_summaries_end_to_end_with_a_recorded_fixture(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.put("/v1/steam/key", headers=HEADERS, json={"key": VALID_KEY})
    steamid = str(REQUESTED_STEAMID)
    monkeypatch.setattr(steam_relay, "http_fetch", fetcher(encode(RECORDED_PLAYER_SUMMARIES)))

    resp = client.get("/v1/steam/player-summaries", headers=HEADERS, params={"steamid": steamid})
    assert resp.status_code == 200
    body = resp.json()
    assert body["players"][0]["personaname"] == "Robin"
    assert body["players"][0]["steamid"] == steamid
    assert VALID_KEY not in resp.text


def test_an_unreachable_upstream_is_a_clean_502_not_a_crash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.put("/v1/steam/key", headers=HEADERS, json={"key": VALID_KEY})
    monkeypatch.setattr(steam_relay, "http_fetch", failing_fetcher("simulated: unreachable"))

    resp = client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": STEAMID_MIN})
    assert resp.status_code == 502
    assert VALID_KEY not in resp.text


def test_hostile_upstream_json_is_a_clean_502_not_a_crash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.put("/v1/steam/key", headers=HEADERS, json={"key": VALID_KEY})
    monkeypatch.setattr(steam_relay, "http_fetch", fetcher(b"<not json at all>"))

    resp = client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": STEAMID_MIN})
    assert resp.status_code == 502


def test_upstream_failure_never_leaks_the_key_into_logs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    client.put("/v1/steam/key", headers=HEADERS, json={"key": VALID_KEY})
    monkeypatch.setattr(steam_relay, "http_fetch", failing_fetcher("simulated failure"))

    with caplog.at_level(logging.WARNING):
        client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": STEAMID_MIN})

    assert VALID_KEY not in caplog.text


def test_repeated_requests_within_the_ttl_reuse_the_cache(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.put("/v1/steam/key", headers=HEADERS, json={"key": VALID_KEY})
    calls = {"n": 0}

    def counting_fetch(path: str, params: dict, timeout: float | None = None) -> bytes:
        calls["n"] += 1
        return encode(RECORDED_OWNED_GAMES)

    monkeypatch.setattr(steam_relay, "http_fetch", counting_fetch)

    first = client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": STEAMID_MIN})
    second = client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": STEAMID_MIN})

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls["n"] == 1


def test_owned_games_and_player_summaries_cache_independently(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.put("/v1/steam/key", headers=HEADERS, json={"key": VALID_KEY})
    calls = {"owned": 0, "summaries": 0}

    def fetch(path: str, params: dict, timeout: float | None = None) -> bytes:
        if path == steam_relay.OWNED_GAMES_PATH:
            calls["owned"] += 1
            return encode(RECORDED_OWNED_GAMES)
        calls["summaries"] += 1
        return encode(RECORDED_PLAYER_SUMMARIES)

    monkeypatch.setattr(steam_relay, "http_fetch", fetch)

    client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": str(REQUESTED_STEAMID)})
    client.get(
        "/v1/steam/player-summaries", headers=HEADERS, params={"steamid": str(REQUESTED_STEAMID)}
    )

    assert calls == {"owned": 1, "summaries": 1}


def test_distinct_steamids_are_fetched_and_cached_independently(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cache key must include the steamid, not just the endpoint: mutating
    ``RelayCache``'s key to drop the steamid (e.g. ``(endpoint, 0)`` for
    every lookup) would make the SECOND distinct steamid silently reuse the
    FIRST one's cached answer — this test fails in exactly that case, on both
    the call count and the returned payload identity.
    """
    client.put("/v1/steam/key", headers=HEADERS, json={"key": VALID_KEY})

    steamid_a = STEAMID_MIN
    steamid_b = STEAMID_MAX
    payloads = {
        steamid_a: encode(
            {"response": {"game_count": 1, "games": [{"appid": 111, "name": "A", "playtime_forever": 0, "img_icon_url": ""}]}}
        ),
        steamid_b: encode(
            {"response": {"game_count": 1, "games": [{"appid": 222, "name": "B", "playtime_forever": 0, "img_icon_url": ""}]}}
        ),
    }
    calls = {"n": 0}

    def fetch(path: str, params: dict, timeout: float | None = None) -> bytes:
        calls["n"] += 1
        return payloads[params["steamid"]]

    monkeypatch.setattr(steam_relay, "http_fetch", fetch)

    first = client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": steamid_a})
    second = client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": steamid_b})

    assert calls["n"] == 2
    assert first.json()["games"][0]["appid"] == 111
    assert second.json()["games"][0]["appid"] == 222


def test_relay_cache_entries_expire_after_the_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit-level pin, isolated from HTTP: deleting the expiry check inside
    ``RelayCache.get`` (``time.monotonic() >= expires_at``) would make this
    test fail because the second ``get`` would still return the value.
    """
    fake_time = {"t": 1000.0}
    monkeypatch.setattr(steam_relay.time, "monotonic", lambda: fake_time["t"])

    cache = steam_relay.RelayCache(ttl_seconds=60.0)
    cache.set("owned-games", 1, "first-answer")
    assert cache.get("owned-games", 1) == "first-answer"

    fake_time["t"] += 61.0
    assert cache.get("owned-games", 1) is None


def test_ttl_expiry_causes_a_fresh_upstream_call_over_http(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same property as ``test_relay_cache_entries_expire_after_the_ttl``,
    exercised through the full HTTP stack so the wiring (not just the class)
    is pinned: after the TTL elapses, a request must reach ``http_fetch``
    again rather than serving the stale cached answer forever.
    """
    client.put("/v1/steam/key", headers=HEADERS, json={"key": VALID_KEY})
    calls = {"n": 0}

    def counting_fetch(path: str, params: dict, timeout: float | None = None) -> bytes:
        calls["n"] += 1
        return encode(RECORDED_OWNED_GAMES)

    monkeypatch.setattr(steam_relay, "http_fetch", counting_fetch)

    fake_time = {"t": 1000.0}
    monkeypatch.setattr(steam_relay.time, "monotonic", lambda: fake_time["t"])

    client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": STEAMID_MIN})
    assert calls["n"] == 1

    fake_time["t"] += steam_relay.DEFAULT_CACHE_TTL_SECONDS + 1.0
    client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": STEAMID_MIN})
    assert calls["n"] == 2


def test_relay_cache_evicts_the_oldest_entry_once_full(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(steam_relay, "MAX_CACHE_ENTRIES", 2)
    cache = steam_relay.RelayCache(ttl_seconds=60.0)

    cache.set("owned-games", 1, "a")
    cache.set("owned-games", 2, "b")
    cache.set("owned-games", 3, "c")  # must evict steamid=1, the oldest

    assert cache.get("owned-games", 1) is None
    assert cache.get("owned-games", 2) == "b"
    assert cache.get("owned-games", 3) == "c"


def test_relay_cache_updating_an_existing_key_does_not_evict_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(steam_relay, "MAX_CACHE_ENTRIES", 2)
    cache = steam_relay.RelayCache(ttl_seconds=60.0)

    cache.set("owned-games", 1, "a")
    cache.set("owned-games", 2, "b")
    cache.set("owned-games", 1, "a-updated")  # re-set of an existing key: no eviction

    assert cache.get("owned-games", 1) == "a-updated"
    assert cache.get("owned-games", 2) == "b"


def test_setting_a_different_key_clears_the_cache(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing the ``cache.clear()`` call from ``PUT /v1/steam/key`` would
    make this test fail: the second request would still be served the
    answer fetched under the OLD key instead of triggering a fresh call.
    """
    client.put("/v1/steam/key", headers=HEADERS, json={"key": VALID_KEY})
    calls = {"n": 0}

    def counting_fetch(path: str, params: dict, timeout: float | None = None) -> bytes:
        calls["n"] += 1
        return encode(RECORDED_OWNED_GAMES)

    monkeypatch.setattr(steam_relay, "http_fetch", counting_fetch)

    client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": STEAMID_MIN})
    assert calls["n"] == 1

    client.put("/v1/steam/key", headers=HEADERS, json={"key": OTHER_KEY})
    client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": STEAMID_MIN})
    assert calls["n"] == 2


def test_deleting_the_key_clears_the_cache_not_just_masks_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``test_clearing_the_key_immediately_reverts_to_409_even_with_a_warm_cache``
    already pins that DELETE makes the NEXT call answer 409 regardless of a
    warm cache. This test pins the independent fact that DELETE actually
    clears the cache (not just that the 409 gate masks it): reconfiguring the
    SAME key afterwards must still trigger a fresh upstream call rather than
    silently resurrecting the pre-delete cached answer.
    """
    client.put("/v1/steam/key", headers=HEADERS, json={"key": VALID_KEY})
    calls = {"n": 0}

    def counting_fetch(path: str, params: dict, timeout: float | None = None) -> bytes:
        calls["n"] += 1
        return encode(RECORDED_OWNED_GAMES)

    monkeypatch.setattr(steam_relay, "http_fetch", counting_fetch)

    client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": STEAMID_MIN})
    assert calls["n"] == 1

    client.delete("/v1/steam/key", headers=HEADERS)
    client.put("/v1/steam/key", headers=HEADERS, json={"key": VALID_KEY})  # same key, reconfigured

    client.get("/v1/steam/owned-games", headers=HEADERS, params={"steamid": STEAMID_MIN})
    assert calls["n"] == 2
