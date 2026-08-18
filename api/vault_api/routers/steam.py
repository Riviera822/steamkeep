"""Opt-in Steam Web API relay endpoints (WP 4a.6r; ADR-0004 addendum).

``GET    /v1/steam/key``             — status only: configured + last 4 chars.
``PUT    /v1/steam/key``             — set the relay's Web API key.
``DELETE /v1/steam/key``             — clear it (idempotent).
``GET    /v1/steam/owned-games``     — relay ``GetOwnedGames`` for one steamid.
``GET    /v1/steam/player-summaries``— relay ``GetPlayerSummaries`` for one steamid.

Auth is attached at the router level (secure-by-default pattern, see
api/README.md "Auth") — every route added here is authenticated automatically,
same as every other router in this codebase.

**The relay is off until a key is configured.** ``owned-games`` and
``player-summaries`` answer ``409 Conflict`` in that state — a distinct,
documented shape a web UI can branch on (``response.status === 409``), never a
guess dressed up as an empty result. This is deliberately a status code, not a
``200`` with ``configured: false`` in the body: unlike the manifest oracle
(which answers ``200`` because "never asked" is a normal, informational state
about a game), a relay call while unconfigured is a request the server cannot
service at all, which is what ``409`` communicates. The key-status endpoint
(``GET /v1/steam/key``) is the one place ``configured: false`` appears as a
normal ``200`` body — that endpoint's whole job is answering exactly this
question.

**The key is never echoed back.** ``PUT``/``GET /v1/steam/key`` return
``key_last4`` only (``vault_api.steam_relay.mask_key``); no route, error
detail, or log line in this module or ``vault_api/steam_relay.py`` ever
carries the full key or a URL containing it (see that module's docstring).

**WP 4h.0 (ADR-0010): the privacy gate for ``playtime_forever`` /
``rtime_last_played``.** Two independent, env-only settings
(``Settings.relay_expose_playtime`` / ``relay_expose_last_played``,
``vault_api/config.py``) decide whether each field may leave this process at
all. The gate is applied HERE, in ``get_owned_games``, at the outermost
conversion from the internal ``steam_relay.OwnedGame`` record (which always
carries both fields, cached or not — the cache's contents are not what this
gate protects) to the wire-format ``OwnedGameOut``: when a key is off, that
field's constructor argument is simply never passed, and the route's
``response_model_exclude_unset=True`` then drops the JSON key entirely from
the response body — not a `null`/`0` a client could still observe, an
ABSENT key, exactly as the work package requires ("a switch that only hides
the number in the UI is defeated by opening the browser's dev tools"). Both
default OFF (``config.DEFAULT_RELAY_EXPOSE_PLAYTIME``/
``_LAST_PLAYED`` — see their own docstrings for the privacy argument) and
are deliberately NOT overridable via ``PATCH /v1/settings`` — ADR-0010
explains why, in short: the settings database lives in a Docker volume that
can be lost, and a privacy opt-out must not have "silently starts collecting
again" as its failure mode. ``GET /v1/settings`` still reports both keys as
informational, env-only rows (``settings_store.ENV_ONLY_INFO_KEYS``) so a UI
can show their state without offering a switch that would 422.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from vault_api import steam_relay
from vault_api.auth import require_api_key
from vault_api.config import Settings
from vault_api.deps import DbOpener, db_opener, get_steam_relay_cache
from vault_api.steam_relay import RelayCache

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_api_key)], tags=["steam"])

#: Query constraint shared by the two relay routes. A plain ``str`` (not
#: FastAPI's ``int`` path/query coercion) so ``steam_relay.valid_steamid64``
#: — not Pydantic's lax numeric parsing — is the one thing that decides what
#: counts as a usable SteamID64 (docs/LEARNINGS.md "Parsers").
SteamIdQuery = Query(min_length=1, max_length=32, description="SteamID64 (17 decimal digits)")

_NOT_CONFIGURED_DETAIL = (
    "Steam relay is not configured. Set a Web API key via PUT /v1/steam/key."
)
_UPSTREAM_ERROR_DETAIL = "Steam Web API request failed; see vault-api's server logs."


class KeyIn(BaseModel):
    key: str = Field(description="32-character hexadecimal Steam Web API key")


class KeyStatusOut(BaseModel):
    configured: bool
    #: Last 4 characters only — never the full key. ``null`` when unconfigured.
    key_last4: str | None


class OwnedGameOut(BaseModel):
    appid: int
    name: str
    #: Minutes, always present in Steam's own response whenever a game
    #: object appears at all (verified against the Steamworks Web API docs:
    #: appid+playtime_forever are the two fields returned even under the
    #: most restricted permission case) -- so the existing 0-default in
    #: ``steam_relay.parse_owned_games`` for a malformed/missing value only
    #: fires on a genuinely hostile upstream, not on a normal "never played"
    #: game (Steam represents that with an explicit 0 of its own, which is a
    #: real claim, not an absence). Left unchanged (additive-only, WP 4h.1):
    #: this field's TYPE is a cross-frontend contract this work package must
    #: not touch, even though its sibling ``rtime_last_played`` below gets
    #: the fully honest nullable treatment because it is a brand-new field.
    #: Defaults to ``0`` only so this field CAN be omitted from construction
    #: (WP 4h.0): when ``Settings.relay_expose_playtime`` is off, the router
    #: never passes this kwarg at all, and ``response_model_exclude_unset``
    #: then drops the JSON key entirely rather than sending a `0` a client
    #: could mistake for a real "never played" claim.
    playtime_forever: int = 0
    img_icon_url: str
    #: Unix-epoch seconds of this account's last recorded play session for
    #: this app, or ``null`` (WP 4h.1). ``null`` covers two DIFFERENT
    #: upstream situations, both collapsed to the same outcome on purpose: a
    #: private profile / restricted-permission view / a game truly never
    #: played, where Steam gives us NOTHING (key absent -- an absence, not a
    #: claim); and an explicit upstream ``0``, which per Steam's own
    #: convention DOES mean "never played" (a real claim, not noise) but is
    #: discarded here anyway because ``playtime_forever`` already carries
    #: that identical fact in this same response, and rendering it as a
    #: literal 1970-01-01 date would be worse than ``null`` (see
    #: ``steam_relay._coerce_last_played`` for the full reasoning). Never
    #: coerced the OTHER way (absence -> a manufactured ``0``) -- that would
    #: be inventing "never played" from data that never said so.
    #: api/README.md documents this field's full semantics and its privacy
    #: note. Defaults to ``None`` for the same WP 4h.0 "must be omittable"
    #: reason ``playtime_forever`` above defaults to ``0`` -- when
    #: ``Settings.relay_expose_last_played`` is off, this kwarg is never
    #: passed and the key disappears from the response body entirely,
    #: rather than merely rendering as ``null`` (which would already be
    #: indistinguishable from the honest "Steam gave us nothing" case, but
    #: the work package asks for the key itself to be absent).
    rtime_last_played: int | None = None


class OwnedGamesOut(BaseModel):
    configured: bool = True
    game_count: int
    games: list[OwnedGameOut]


class PlayerSummaryOut(BaseModel):
    steamid: str
    personaname: str
    avatar: str | None
    avatarmedium: str | None
    avatarfull: str | None
    personastate: int | None


class PlayerSummariesOut(BaseModel):
    configured: bool = True
    players: list[PlayerSummaryOut]


def _parse_steamid(raw: str) -> int:
    steamid = steam_relay.valid_steamid64(raw)
    if steamid is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="steamid must be a 17-digit SteamID64 in the individual-account range.",
        )
    return steamid


def _require_configured_key(open_db: DbOpener) -> str:
    with open_db() as conn:
        key = steam_relay.get_key(conn)
    if key is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_NOT_CONFIGURED_DETAIL)
    return key


# --------------------------------------------------------------------------
# Key management
# --------------------------------------------------------------------------


@router.get("/v1/steam/key", response_model=KeyStatusOut)
def get_key_status(open_db: DbOpener = Depends(db_opener)) -> KeyStatusOut:
    with open_db() as conn:
        configured, last4 = steam_relay.key_status(conn)
    return KeyStatusOut(configured=configured, key_last4=last4)


@router.put("/v1/steam/key", response_model=KeyStatusOut)
def set_key(
    body: KeyIn,
    open_db: DbOpener = Depends(db_opener),
    cache: RelayCache = Depends(get_steam_relay_cache),
) -> KeyStatusOut:
    """Set (or replace) the relay's Web API key.

    ``422`` for anything that is not exactly 32 hexadecimal characters —
    validated BEFORE the value ever reaches the database or a URL
    (docs/LEARNINGS.md "Parsers": everything from a caller is hostile input
    too). The stored value is normalised to uppercase; the response never
    echoes it back, only ``key_last4``.

    Clears the relay cache: a cached answer fetched under the PREVIOUS key
    must never keep being served (for up to the cache's TTL) after the
    operator rotates it — see ``vault_api/steam_relay.py``'s "Rate-limit
    friendliness" section.
    """
    key = steam_relay.valid_steam_web_api_key(body.key)
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Steam Web API key must be exactly 32 hexadecimal characters.",
        )
    with open_db() as conn:
        steam_relay.set_key(conn, key)
    cache.clear()
    return KeyStatusOut(configured=True, key_last4=steam_relay.mask_key(key))


@router.delete(
    "/v1/steam/key",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def delete_key(
    open_db: DbOpener = Depends(db_opener),
    cache: RelayCache = Depends(get_steam_relay_cache),
) -> None:
    """Clear the configured key. ``204`` whether or not one was set —
    idempotent "make sure it is gone", same shape as
    ``DELETE /v1/oracle/{appid}``.

    Also clears the relay cache — see ``set_key`` above for why a stale
    answer must never survive a key change.
    """
    with open_db() as conn:
        steam_relay.clear_key(conn)
    cache.clear()


# --------------------------------------------------------------------------
# Relay
# --------------------------------------------------------------------------


@router.get(
    "/v1/steam/owned-games",
    response_model=OwnedGamesOut,
    # WP 4h.0: lets a game entry OMIT playtime_forever/rtime_last_played
    # entirely (see OwnedGameOut's field docstrings and _build_owned_game_out
    # below) instead of sending them as 0/null. `configured`/`game_count`/
    # `games` are always passed explicitly in the return below, so this never
    # drops THEM -- exclude_unset only omits a field that genuinely was never
    # set on the specific model instance being serialised.
    response_model_exclude_unset=True,
)
def get_owned_games(
    request: Request,
    steamid: str = SteamIdQuery,
    open_db: DbOpener = Depends(db_opener),
    cache: RelayCache = Depends(get_steam_relay_cache),
) -> OwnedGamesOut:
    """Relay ``IPlayerService/GetOwnedGames/v1`` for one SteamID64.

    ``409`` when no key is configured, ``422`` for an unusable ``steamid``,
    ``502`` for any upstream failure (unreachable, timeout, garbage
    response) — never a crash, and never a response containing more than the
    whitelisted fields the library grid needs, further narrowed by the
    WP 4h.0 privacy gate (see this module's own docstring addendum) for
    ``playtime_forever``/``rtime_last_played`` specifically.
    """
    parsed_steamid = _parse_steamid(steamid)
    key = _require_configured_key(open_db)

    cached = cache.get("owned-games", parsed_steamid)
    if cached is not None:
        result = cached
    else:
        try:
            result = steam_relay.fetch_owned_games(parsed_steamid, key)
        except steam_relay.SteamRelayError as exc:
            logger.warning("steam relay: owned-games fetch failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=_UPSTREAM_ERROR_DETAIL
            ) from exc
        cache.set("owned-games", parsed_steamid, result)

    settings: Settings = request.app.state.settings
    games_out = [
        _build_owned_game_out(
            game,
            expose_playtime=settings.relay_expose_playtime,
            expose_last_played=settings.relay_expose_last_played,
        )
        for game in result.games
    ]
    return OwnedGamesOut(configured=True, game_count=result.game_count, games=games_out)


def _build_owned_game_out(
    game: steam_relay.OwnedGame, *, expose_playtime: bool, expose_last_played: bool
) -> OwnedGameOut:
    """One ``steam_relay.OwnedGame`` -> the wire-format ``OwnedGameOut``,
    applying the WP 4h.0 privacy gate.

    ``appid``/``name``/``img_icon_url`` are always passed. ``playtime_forever``
    and ``rtime_last_played`` are passed ONLY when their respective setting is
    on — omitting the keyword argument (rather than passing the value and
    relying on a filter afterwards) is what makes each field genuinely UNSET
    on the model instance, which is what
    ``get_owned_games``'s ``response_model_exclude_unset=True`` needs to see
    to drop the JSON key entirely instead of rendering ``0``/``null``.
    """
    fields: dict[str, Any] = {
        "appid": game.appid,
        "name": game.name,
        "img_icon_url": game.img_icon_url,
    }
    if expose_playtime:
        fields["playtime_forever"] = game.playtime_forever
    if expose_last_played:
        fields["rtime_last_played"] = game.rtime_last_played
    return OwnedGameOut(**fields)


@router.get("/v1/steam/player-summaries", response_model=PlayerSummariesOut)
def get_player_summaries(
    steamid: str = SteamIdQuery,
    open_db: DbOpener = Depends(db_opener),
    cache: RelayCache = Depends(get_steam_relay_cache),
) -> PlayerSummariesOut:
    """Relay ``ISteamUser/GetPlayerSummaries/v2`` for one SteamID64.

    Same error shape as ``get_owned_games`` above (``409``/``422``/``502``).
    """
    parsed_steamid = _parse_steamid(steamid)
    key = _require_configured_key(open_db)

    cached = cache.get("player-summaries", parsed_steamid)
    if cached is not None:
        result = cached
    else:
        try:
            result = steam_relay.fetch_player_summaries(parsed_steamid, key)
        except steam_relay.SteamRelayError as exc:
            logger.warning("steam relay: player-summaries fetch failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=_UPSTREAM_ERROR_DETAIL
            ) from exc
        cache.set("player-summaries", parsed_steamid, result)

    return PlayerSummariesOut(
        players=[
            PlayerSummaryOut(
                steamid=player.steamid,
                personaname=player.personaname,
                avatar=player.avatar,
                avatarmedium=player.avatarmedium,
                avatarfull=player.avatarfull,
                personastate=player.personastate,
            )
            for player in result.players
        ]
    )
