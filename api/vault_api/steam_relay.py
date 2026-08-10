"""Opt-in Steam Web API relay (WP 4a.6r; ADR-0004 addendum, user decision A+C).

## What this is

The Steam Web API sends no CORS headers, so the Phase-4a **web UI** (unlike
the native Android app, which talks to it directly with a device-local key —
ADR-0004 decision 2, untouched) cannot call ``GetOwnedGames`` from the
browser. This module is vault-api's small, opt-in answer: it stores one
revocable, **read-scoped** Steam Web API key server-side and relays exactly
two public-profile read calls on the caller's behalf:

- ``IPlayerService/GetOwnedGames/v1`` — the library grid's game list;
- ``ISteamUser/GetPlayerSummaries/v2`` — persona name and avatar.

## The three rules everything here obeys (mirrors ``vault_api/oracle.py``)

**1. Off unless configured.** There is no ``Settings`` flag for this feature —
the enable switch is "does ``steam_relay_key`` have a row" (WP 3.9's oracle is
gated on an env var; this is gated on runtime, operator-entered state, per the
ADR-0004 addendum's decision to let the key be entered in the web UI rather
than in ``.env``). ``routers/steam.py`` checks ``get_key`` itself before doing
anything else, and answers a distinct ``409`` when it is unset — never a
guess, never a silent "treat it as configured".

**2. Fail-soft towards the client, but never towards the secret.** A relay
call that only reads public data is not allowed to fail the *caller's* request
in a way that leaks the key: every upstream failure (unreachable, timeout,
redirected, non-200, garbage JSON, wrong shape) becomes ``SteamRelayError``
with a message built ONLY from the endpoint's **path** — never the query
string, which is the only place the key or a steamid ever appears in a
constructed URL (see ``http_fetch``/``_redacted_url``). Nothing in this module
ever formats the query string into a log line or an exception message.

**3. Never echoed, never logged, redacted everywhere it could otherwise leak.**
``GET /v1/steam/key`` returns ``configured`` plus the **last four characters
only** (``mask_key``); ``PUT``/``DELETE`` never echo the value back either.
The value stored in ``steam_relay_key`` is read back through ``get_key``,
which re-validates it (the database is a file an operator can edit — same
posture as ``oracle.gc_keepset_gids``'s re-validation) rather than trusting it
by construction.

## Everything returned is hostile input

``docs/LEARNINGS.md``'s "Parsers" rules are binding here exactly as they are
for the manifest oracle: the response is attacker-shaped by definition (it
comes from a network call to a host this project does not run, over a
connection an on-path party could tamper with even though TLS makes that
hard) — the raw body is bounded before it is decoded, ``RecursionError`` from
deeply nested JSON is caught by name and converted to ``SteamRelayError``, the
number of games/players is capped, and every field that reaches a client is
individually type- and shape-checked before being copied into the whitelisted
response — only ``appid``, ``name``, ``playtime_forever``, ``img_icon_url``
(games) and ``steamid``, ``personaname``, ``avatar*``, ``personastate``
(players) ever cross that boundary, matching the library grid's stated needs
in the work package boundaries. A garbage or hostile upstream answer degrades
to a clean ``502`` (router) or a partial-but-valid record with the bad entries
dropped — never a crash, and never a value that skipped validation reaching a
client or the in-memory cache.

## SteamID64 is hostile input too

A caller-supplied ``steamid`` query parameter is validated with
``valid_steamid64`` — 17 ASCII decimal digits, and range-plausible (the
individual-account SteamID64 space: universe 1, account type 1, instance 1,
account number up to 32 bits) — before it goes anywhere near a URL. Anything
else is refused by the router with a ``422`` before this module is even
called (the same "reject at the edge" posture ``oracle.py``'s appid path
type applies).

## Rate-limit friendliness: a small in-memory TTL cache

``RelayCache`` holds the last answer per ``(endpoint, steamid)`` for a few
minutes (``DEFAULT_CACHE_TTL_SECONDS``). It exists so a web UI re-rendering
the library grid a few times in a short window does not turn into that many
Steam Web API calls — Valve's own rate limits are per-key, not documented as a
hard number, and this is cheap insurance. Deliberately **not** persisted and
**not** configurable via an env var (scope discipline: this work package adds
no new ``VAULT_*`` setting) — a restart empties it, which is fine, the next
request simply refetches. It stores validated, already-whitelisted result
objects, never raw upstream bytes. Entries are additionally bounded by
``MAX_CACHE_ENTRIES``, dropping the oldest once full — the same
bounded-growth shape ``webhooks.WebhookNotifier``'s delivery queue uses.

**Invalidated on every key change.** ``routers/steam.py``'s ``PUT``/``DELETE
/v1/steam/key`` both call ``RelayCache.clear()`` after writing the database:
a cached answer fetched under the *previous* key must never keep being served
for up to ``DEFAULT_CACHE_TTL_SECONDS`` after the operator rotates or revokes
it — the cache's lifetime is tied to the key that produced its contents, not
to a fixed wall-clock window regardless of configuration changes.

## Privacy

Exactly like the manifest oracle (``vault_api/oracle.py``'s own privacy
section), this is one of the few things in SteamVault that leaves the LAN —
and here it leaves it carrying the operator's own Steam Web API key and the
SteamID64 being looked up. **With the relay configured, library queries
originate from the SERVER** (they leave the LAN toward Valve), not from the
browser — see api/README.md "Steam Web API relay" and the ADR-0004 addendum,
which this note mirrors verbatim in spirit.
"""

from __future__ import annotations

import json
import logging
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlencode, urlsplit

from vault_api import deletion, jobs

logger = logging.getLogger(__name__)

#: The only host this module will ever talk to. Never derived from
#: configuration — unlike the manifest oracle's URL, there is no "point it at
#: your own mirror" story for a Valve-authenticated call, and pinning the
#: host is what makes "no redirects" a meaningful guarantee rather than a
#: guarantee about a host an operator never chose.
STEAM_API_HOST = "api.steampowered.com"
STEAM_API_BASE = f"https://{STEAM_API_HOST}"

#: The exactly two endpoints this relay is allowed to call (work package
#: boundary, ADR-0004 addendum: "ONLY public-profile read endpoints needed for
#: the library grid").
OWNED_GAMES_PATH = "/IPlayerService/GetOwnedGames/v1/"
PLAYER_SUMMARIES_PATH = "/ISteamUser/GetPlayerSummaries/v2/"

#: Steam Web API keys are a fixed-length hexadecimal string. Stored uppercase
#: (Valve's own convention) regardless of the case the operator pasted in.
STEAM_KEY_LENGTH = 32
_HEX_CHARS = frozenset("0123456789abcdefABCDEF")

#: SteamID64 range for an individual account: universe 1 (public), account
#: type 1 (individual), instance 1, account number 0..2**32-1. This is the
#: same "17 decimal digits, range-plausible" bound the work package asks for —
#: rejecting anything outside it means a fat-fingered or hostile steamid never
#: reaches a URL at all.
STEAM_ID64_BASE = 76561197960265728
STEAM_ID64_MAX = STEAM_ID64_BASE + 0xFFFFFFFF
STEAM_ID64_DIGITS = 17

#: Hard ceiling on the response body this module will even look at. A very
#: large Steam library (thousands of games) still renders to well under a
#: megabyte of JSON; this is generous headroom while refusing a body designed
#: to exhaust memory.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

#: Bounds on the parsed structure — large multiples of what a real account can
#: contain, so a hostile document turns into "the rest were ignored" (still a
#: usable partial answer) rather than a memory blow-up.
MAX_GAMES = 5000
MAX_PLAYERS = 32
MAX_NAME_LEN = 256
MAX_PERSONA_LEN = 128
MAX_ICON_LEN = 64
MAX_AVATAR_URL_LEN = 512

#: How many parse warnings one call carries (and how many are logged) — same
#: bound and reasoning as ``oracle.MAX_WARNINGS``.
MAX_WARNINGS = 10

#: Socket timeout for one relay request. Short on purpose, same reasoning as
#: the manifest oracle: this is a synchronous request on the caller's behalf,
#: and a slow third party must never turn into a slow vault-api.
DEFAULT_FETCH_TIMEOUT_SECONDS = 10.0

#: TTL for the in-memory per-(endpoint, steamid) answer cache. Not
#: configurable (see module docstring) — a few minutes is enough to absorb a
#: web UI re-rendering the library grid.
DEFAULT_CACHE_TTL_SECONDS = 300.0  # 5 minutes

#: Hard cap on how many distinct ``(endpoint, steamid)`` entries
#: ``RelayCache`` may hold before the OLDEST is dropped to make room — same
#: bounded-growth shape as ``webhooks.MAX_QUEUE_SIZE``. A homelab install
#: looks up a handful of accounts; this exists so a caller cycling through
#: many steamids cannot grow this dict without bound.
MAX_CACHE_ENTRIES = 256

USER_AGENT = "SteamVault-vault-api/0.1 (+https://github.com/, self-hosted relay)"


class SteamRelayError(Exception):
    """Anything that made a relay request/response unusable.

    The one exception type ``http_fetch``/``parse_owned_games``/
    ``parse_player_summaries`` are allowed to raise: no ``URLError``,
    ``socket.timeout``, ``JSONDecodeError``, ``UnicodeDecodeError``,
    ``RecursionError`` escapes them. Its message is built ONLY from the
    endpoint path (see ``_redacted_url``) — never from the query string that
    carries the key or a steamid — so it is always safe to log or return in an
    error detail verbatim.
    """


# --------------------------------------------------------------------------
# Validators (LEARNINGS "Parsers" — hostile input from the caller AND from
# Valve)
# --------------------------------------------------------------------------


def valid_steam_web_api_key(value: object) -> str | None:
    """A plausible Steam Web API key, or ``None``.

    Strict per the work package: exactly ``STEAM_KEY_LENGTH`` (32) ASCII
    hexadecimal characters, nothing else — no whitespace, no surrounding
    quotes, no non-ASCII look-alike digits. Normalises to uppercase (Valve's
    own rendering) so two operators pasting the same key in different case
    are stored identically. Rejecting is safe by construction: an unstorable
    value simply never reaches ``set_key``, and the router turns this into a
    ``422`` before anything is written.
    """
    if not isinstance(value, str):
        return None
    if len(value) != STEAM_KEY_LENGTH:
        return None
    if not value.isascii():
        return None
    if any(char not in _HEX_CHARS for char in value):
        return None
    return value.upper()


def mask_key(key: str) -> str:
    """The last 4 characters of a validated key — everything ``GET
    /v1/steam/key`` is ever allowed to reveal about it. Never called on an
    unvalidated value; a key shorter than 4 characters cannot occur once
    ``valid_steam_web_api_key`` has run, but the slice is safe either way.
    """
    return key[-4:]


def valid_steamid64(value: object) -> int | None:
    """A plausible individual-account SteamID64, or ``None``.

    Strict ASCII-digits-only (the same house rule ``deletion.coerce_positive_id``
    applies, WP 1.6/3.9): ``int()`` alone would accept ``" 1 "``, ``"+1"``,
    ``"1_0"`` and non-ASCII digits, none of which is a steamid this code ever
    produced. Exactly ``STEAM_ID64_DIGITS`` characters (real SteamID64 values
    for individual accounts are always exactly 17 digits — a shorter or
    longer numeral is not one, whatever its numeric value) and range-checked
    against the individual-account space so a syntactically numeric but
    nonsensical value (an OpenID URL fragment, a depot id, ``0``) never
    reaches a URL.
    """
    if not isinstance(value, str):
        return None
    if len(value) != STEAM_ID64_DIGITS:
        return None
    if not (value.isascii() and value.isdigit()):
        return None
    parsed = int(value)
    if not (STEAM_ID64_BASE <= parsed <= STEAM_ID64_MAX):
        return None
    return parsed


def _warn(warnings: list[str], message: str) -> None:
    """Append a warning, bounded — a junk document must not become a log
    flood (same shape as ``oracle._warn``)."""
    if len(warnings) < MAX_WARNINGS:
        warnings.append(message)
    elif len(warnings) == MAX_WARNINGS:
        warnings.append("... further warnings suppressed")


def _coerce_nonneg_int(value: object) -> int | None:
    """A non-negative ``int`` straight out of parsed JSON, or ``None``.

    Unlike ``deletion.coerce_positive_id`` this never parses a *string* —
    Valve's JSON numbers arrive as ``int``/``float`` already, and a
    string-typed count/playtime would itself be a shape defect worth
    rejecting rather than coercing. ``bool`` is rejected explicitly (WP 2.4's
    house rule: ``True == 1`` must never sneak through as a count).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    return None


def _valid_avatar_url(value: object) -> str | None:
    """A bounded, http(s) avatar URL, or ``None``.

    Steam's CDN always answers with a plain ``http(s)://...`` URL; anything
    else (a ``data:`` URI, a non-ASCII look-alike, an absurd length) is
    dropped rather than passed through to a client that will put it in an
    ``<img src>``.
    """
    if not isinstance(value, str):
        return None
    if not value.isascii() or len(value) > MAX_AVATAR_URL_LEN:
        return None
    scheme = urlsplit(value).scheme.lower()
    if scheme not in ("http", "https"):
        return None
    return value


# --------------------------------------------------------------------------
# Key storage — its own single-row table (schema v12)
# --------------------------------------------------------------------------


def get_key(conn: sqlite3.Connection) -> str | None:
    """The configured key, or ``None`` if none is set.

    Re-validates on the way OUT of the database, same reasoning as
    ``oracle.gc_keepset_gids``: only a validated key is ever written, but the
    database is a file an operator can edit by hand, and this value is about
    to be sent to Valve in a URL. An unusable stored value is treated exactly
    like "not configured" — fail-closed, never "send it anyway and let Valve
    reject it".
    """
    row = conn.execute("SELECT api_key FROM steam_relay_key WHERE id = 1").fetchone()
    if row is None:
        return None
    return valid_steam_web_api_key(row["api_key"])


def key_status(conn: sqlite3.Connection) -> tuple[bool, str | None]:
    """``(configured, last4)`` — everything ``GET /v1/steam/key`` may reveal."""
    key = get_key(conn)
    if key is None:
        return False, None
    return True, mask_key(key)


def set_key(conn: sqlite3.Connection, key: str) -> None:
    """Store ``key`` (already validated by the caller), replacing any
    previous one. Commits internally — the complete write unit for this one
    fact, same pattern as ``mapping.upsert_mapping``.
    """
    conn.execute(
        """
        INSERT INTO steam_relay_key (id, api_key, updated_at)
        VALUES (1, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            api_key    = excluded.api_key,
            updated_at = excluded.updated_at
        """,
        (key, jobs.utcnow_iso()),
    )
    conn.commit()


def clear_key(conn: sqlite3.Connection) -> None:
    """Forget the configured key. Idempotent (no error if none was set)."""
    conn.execute("DELETE FROM steam_relay_key WHERE id = 1")
    conn.commit()


# --------------------------------------------------------------------------
# Fetching (bounded, no redirects, HTTPS only, host pinned, key never logged)
# --------------------------------------------------------------------------


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Turn any redirect into an error instead of following it — same
    reasoning as ``oracle._RefuseRedirects``: the host is pinned to
    ``STEAM_API_HOST``, and a redirect is a hand-off to a different one this
    code must not silently make.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(_RefuseRedirects)


def _redacted_url(path: str) -> str:
    """``https://api.steampowered.com<path>`` — deliberately WITHOUT a query
    string. This is the only form of the URL that may ever appear in a log
    line or an exception message: the key and the steamid live exclusively in
    the query string, which no function in this module ever formats into
    text meant to be read or stored.
    """
    return f"{STEAM_API_BASE}{path}"


def http_fetch(
    path: str,
    params: Mapping[str, str],
    *,
    timeout: float = DEFAULT_FETCH_TIMEOUT_SECONDS,
) -> bytes:
    """``GET https://api.steampowered.com<path>?<params>``, bounded, no
    redirects, HTTPS only, host pinned.

    Raises ``SteamRelayError`` — and only ``SteamRelayError`` — for a
    connection/TLS failure, a timeout, a redirect, a non-200 status, or a body
    over the size bound. Every message it raises is built from
    ``_redacted_url(path)``, never from the full URL this function
    constructs internally — the key and steamid in ``params`` never reach a
    log line or an exception message.
    """
    url = f"{STEAM_API_BASE}{path}?{urlencode(params)}"
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )

    try:
        with _OPENER.open(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is not None and status != 200:
                raise SteamRelayError(f"{_redacted_url(path)} answered HTTP {status}")
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except SteamRelayError:
        raise
    except urllib.error.HTTPError as exc:
        raise SteamRelayError(f"{_redacted_url(path)} answered HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise SteamRelayError(f"{_redacted_url(path)} is unreachable: {exc.reason}") from exc
    except socket.timeout as exc:  # pragma: no cover - timing dependent
        raise SteamRelayError(f"{_redacted_url(path)} timed out after {timeout}s") from exc
    except (OSError, ValueError) as exc:
        raise SteamRelayError(f"{_redacted_url(path)} could not be fetched: {exc}") from exc

    if len(body) > MAX_RESPONSE_BYTES:
        raise SteamRelayError(
            f"{_redacted_url(path)} returned more than the {MAX_RESPONSE_BYTES}-byte bound"
        )
    return body


def _decode_json(payload: bytes, path: str) -> object:
    """Bytes → Python objects, with every failure as ``SteamRelayError``.

    ``RecursionError`` is caught explicitly — CPython's JSON scanner recurses
    per nesting level (``docs/LEARNINGS.md``, WP 2.1/3.9's finding) — so a
    bounded body is not automatically a bounded *parse*.
    """
    if len(payload) > MAX_RESPONSE_BYTES:
        raise SteamRelayError(
            f"{_redacted_url(path)} response of {len(payload)} bytes exceeds the "
            f"{MAX_RESPONSE_BYTES}-byte bound"
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SteamRelayError(f"{_redacted_url(path)} did not return valid UTF-8: {exc}") from exc
    try:
        return json.loads(text)
    except RecursionError as exc:
        raise SteamRelayError(
            f"{_redacted_url(path)} response nests too deeply to parse (rejected "
            "before it could exhaust the stack)"
        ) from exc
    except ValueError as exc:
        raise SteamRelayError(f"{_redacted_url(path)} did not return valid JSON: {exc}") from exc


# --------------------------------------------------------------------------
# GetOwnedGames
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OwnedGame:
    """One library-grid row — exactly the fields the work package names."""

    appid: int
    name: str
    playtime_forever: int
    img_icon_url: str


@dataclass(frozen=True)
class OwnedGamesResult:
    game_count: int
    games: tuple[OwnedGame, ...]
    warnings: tuple[str, ...] = ()


def parse_owned_games(payload: bytes, path: str = OWNED_GAMES_PATH) -> OwnedGamesResult:
    """Validate a raw ``GetOwnedGames`` response into an ``OwnedGamesResult``.

    Raises ``SteamRelayError`` when the document is unusable as a whole (not
    JSON, no ``response`` object). Anything locally wrong — one unreadable
    game entry, a missing name, an oversized icon hash — is skipped (with a
    bounded warning) rather than discarding the whole answer: a private
    profile or an empty library legitimately has no ``games`` key at all,
    which is a normal empty answer, not a defect.
    """
    document = _decode_json(payload, path)
    if not isinstance(document, dict):
        raise SteamRelayError(f"{_redacted_url(path)} did not return a JSON object")

    response = document.get("response")
    if not isinstance(response, dict):
        raise SteamRelayError(f"{_redacted_url(path)} response has no usable 'response' object")

    warnings: list[str] = []
    games: list[OwnedGame] = []
    raw_games = response.get("games")
    if raw_games is None:
        pass  # private profile / empty library: a normal, empty answer.
    elif not isinstance(raw_games, list):
        _warn(warnings, "'games' is not a list: treated as empty")
    else:
        for index, raw_game in enumerate(raw_games):
            if index >= MAX_GAMES:
                _warn(warnings, f"more than {MAX_GAMES} games: the rest were ignored")
                break
            if not isinstance(raw_game, dict):
                _warn(warnings, f"ignored game entry {index}: not an object")
                continue
            appid = deletion.coerce_positive_id(raw_game.get("appid"))
            if appid is None:
                _warn(warnings, f"ignored game entry {index}: no usable appid")
                continue
            raw_name = raw_game.get("name")
            if isinstance(raw_name, str):
                name = raw_name[:MAX_NAME_LEN]
            else:
                name = ""
            playtime = _coerce_nonneg_int(raw_game.get("playtime_forever"))
            if playtime is None:
                playtime = 0
            raw_icon = raw_game.get("img_icon_url")
            if isinstance(raw_icon, str) and raw_icon.isascii() and len(raw_icon) <= MAX_ICON_LEN:
                icon = raw_icon
            else:
                icon = ""
            games.append(
                OwnedGame(appid=appid, name=name, playtime_forever=playtime, img_icon_url=icon)
            )

    game_count = _coerce_nonneg_int(response.get("game_count"))
    if game_count is None:
        game_count = len(games)

    return OwnedGamesResult(game_count=game_count, games=tuple(games), warnings=tuple(warnings))


def fetch_owned_games(
    steamid: int,
    api_key: str,
    *,
    timeout: float = DEFAULT_FETCH_TIMEOUT_SECONDS,
) -> OwnedGamesResult:
    """Fetch and validate one account's owned-games list. Raises
    ``SteamRelayError`` on any upstream failure — the router turns that into a
    clean ``502``.

    Always calls the module-level ``http_fetch`` — there is no injectable
    ``fetch`` parameter here (unlike ``oracle.refresh_app``'s ``fetch``):
    every test in this project replaces ``steam_relay.http_fetch`` itself via
    ``monkeypatch.setattr``, so a second injection point would be dead code
    nothing ever used.
    """
    params = {
        "key": api_key,
        "steamid": str(steamid),
        "format": "json",
        "include_appinfo": "1",
        "include_played_free_games": "1",
    }
    payload = http_fetch(OWNED_GAMES_PATH, params, timeout=timeout)
    return parse_owned_games(payload)


# --------------------------------------------------------------------------
# GetPlayerSummaries
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlayerSummary:
    steamid: str
    personaname: str
    avatar: str | None
    avatarmedium: str | None
    avatarfull: str | None
    personastate: int | None


@dataclass(frozen=True)
class PlayerSummariesResult:
    players: tuple[PlayerSummary, ...]
    warnings: tuple[str, ...] = ()


def parse_player_summaries(
    payload: bytes, requested_steamid: int, path: str = PLAYER_SUMMARIES_PATH
) -> PlayerSummariesResult:
    """Validate a raw ``GetPlayerSummaries`` response.

    Cross-checks every returned ``steamid`` against ``requested_steamid`` —
    the same corruption cross-check ``oracle._app_object`` applies to appid:
    an answer that is not about the account asked for must never be attributed
    to it.
    """
    document = _decode_json(payload, path)
    if not isinstance(document, dict):
        raise SteamRelayError(f"{_redacted_url(path)} did not return a JSON object")

    response = document.get("response")
    if not isinstance(response, dict):
        raise SteamRelayError(f"{_redacted_url(path)} response has no usable 'response' object")

    warnings: list[str] = []
    players: list[PlayerSummary] = []
    raw_players = response.get("players")
    if raw_players is None:
        pass
    elif not isinstance(raw_players, list):
        _warn(warnings, "'players' is not a list: treated as empty")
    else:
        for index, raw_player in enumerate(raw_players):
            if index >= MAX_PLAYERS:
                _warn(warnings, f"more than {MAX_PLAYERS} players: the rest were ignored")
                break
            if not isinstance(raw_player, dict):
                _warn(warnings, f"ignored player entry {index}: not an object")
                continue
            steamid = valid_steamid64(raw_player.get("steamid"))
            if steamid is None:
                _warn(warnings, f"ignored player entry {index}: no usable steamid")
                continue
            if steamid != requested_steamid:
                _warn(
                    warnings,
                    f"ignored player entry: steamid {steamid} does not match the "
                    "requested account",
                )
                continue
            raw_persona = raw_player.get("personaname")
            personaname = raw_persona[:MAX_PERSONA_LEN] if isinstance(raw_persona, str) else ""
            personastate = raw_player.get("personastate")
            if isinstance(personastate, bool) or not isinstance(personastate, int):
                personastate = None
            players.append(
                PlayerSummary(
                    steamid=str(steamid),
                    personaname=personaname,
                    avatar=_valid_avatar_url(raw_player.get("avatar")),
                    avatarmedium=_valid_avatar_url(raw_player.get("avatarmedium")),
                    avatarfull=_valid_avatar_url(raw_player.get("avatarfull")),
                    personastate=personastate,
                )
            )

    return PlayerSummariesResult(players=tuple(players), warnings=tuple(warnings))


def fetch_player_summaries(
    steamid: int,
    api_key: str,
    *,
    timeout: float = DEFAULT_FETCH_TIMEOUT_SECONDS,
) -> PlayerSummariesResult:
    """Fetch and validate one account's player summary. Raises
    ``SteamRelayError`` on any upstream failure — the router turns that into a
    clean ``502``. See ``fetch_owned_games`` for why there is no injectable
    ``fetch`` parameter.
    """
    params = {"key": api_key, "steamids": str(steamid), "format": "json"}
    payload = http_fetch(PLAYER_SUMMARIES_PATH, params, timeout=timeout)
    return parse_player_summaries(payload, steamid)


# --------------------------------------------------------------------------
# Rate-limit friendliness: a tiny in-memory TTL cache
# --------------------------------------------------------------------------


class RelayCache:
    """In-memory TTL cache keyed by ``(endpoint, steamid)`` — see the module
    docstring's "Rate-limit friendliness" section.

    One instance lives on ``app.state`` (mirrors ``SizeCache``'s pattern in
    ``vault_api/sizes.py``): constructed unconditionally, cheap, and shared
    across requests so repeated web-UI renders reuse one Steam Web API call.
    Stores already-validated result objects (``OwnedGamesResult`` /
    ``PlayerSummariesResult``), never raw bytes — nothing unvalidated is ever
    cached.
    """

    def __init__(self, ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._store: dict[tuple[str, int], tuple[float, object]] = {}

    def get(self, endpoint: str, steamid: int) -> object | None:
        key = (endpoint, steamid)
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.monotonic() >= expires_at:
                del self._store[key]
                return None
            return value

    def set(self, endpoint: str, steamid: int, value: object) -> None:
        key = (endpoint, steamid)
        with self._lock:
            if key not in self._store and len(self._store) >= MAX_CACHE_ENTRIES:
                # dict preserves insertion order (CPython 3.7+): the first key
                # is the oldest one still present, same "drop the OLDEST to
                # make room" rule webhooks.WebhookNotifier.enqueue applies to
                # its queue.
                del self._store[next(iter(self._store))]
            self._store[key] = (time.monotonic() + self._ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
