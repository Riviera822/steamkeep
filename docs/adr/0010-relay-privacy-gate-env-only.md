# ADR-0010: The Steam relay's playtime/last-played privacy gate is env-only

Date: 2026-08-18
Status: Accepted (operator decision, 2026-08-18, WP 4h.0)

## Context

WP 4h.1 added `rtime_last_played` to the Steam relay's `GetOwnedGames`
response (`vault_api/steam_relay.py`, `vault_api/routers/steam.py`), sitting
alongside `playtime_forever`, which the relay already carried. Phase 4h's
own privacy stance (`docs/PROJECT_PLAN.md`, user decision 2026-08-18) is
explicit that playtime "makes the UI judgemental" in a shared-household
vault and must ship "off by default or dismissible at any time, no
nagging". WP 4h.0 is the server-side half of that stance: two independent
switches, one per field, that decide whether `playtime_forever` and
`rtime_last_played` may leave vault-api's process boundary through the
relay's HTTP response at all. Independent because "when did this person
last play" is the sharper of the two facts — an operator may want the
aggregate hour count for the Phase 4h decision-support panel while still
refusing to ever surface the date.

The first draft of this package designed the two switches as an env
**ceiling** over a database-overridable runtime toggle, mirroring ADR-0009's
db > env > default precedence but inverting it at the top: an env value of
`false` would lock the field off regardless of what a `PATCH /v1/settings`
override said, while an env value of `true` (or unset) would leave the
database free to turn exposure on or off at runtime, exactly as ADR-0009
already allows for `sweep_include_cached`/`auto_gc`.

## Decision

**No database layer for either key.** `relay_expose_playtime` and
`relay_expose_last_played` are environment-only settings
(`VAULT_RELAY_EXPOSE_PLAYTIME` / `VAULT_RELAY_EXPOSE_LAST_PLAYED`,
`vault_api/config.py`), read once at startup into `Settings` and never
touched by `settings_store`. They join `settings_store.ENV_ONLY_INFO_KEYS` —
the same list ADR-0009 §5 already uses for `vault_api_key`, `db_path`,
`cache_root`, `steamprefill_path`, `steamprefill_cache_dir`,
`manifest_archive_dir`, `web_dir` and `settings_readonly` — so `PATCH
/v1/settings` refuses either key by name with the same distinct
"environment-only" `422` those keys already get, and `GET /v1/settings`
reports both as ordinary informational rows (`env_only: true`, `applies:
"restart-required"`).

**The rejected alternative: env-as-ceiling with a runtime toggle.** That
design would still have let the DATABASE be the thing that actually decides
the effective value whenever the environment did not explicitly lock it off
(the common case: an operator who never sets the env var at all). Both
switches would then have lived, in their "on" state, as rows in the
`settings` table.

## Why, stated plainly: where the settings table lives

`vault_api/db.py`'s `settings` table (ADR-0009) is stored in the SQLite
database file, which in the shipped stack is the `vault-db` **Docker
volume** (`deploy/compose.yaml`). `deploy/compose.yaml` itself — the file
that carries every environment variable — is not: it sits in the operator's
own persistent config directory, covered by whatever backs THAT up. A
`docker compose down -v`, a lost volume, or a rebuild on new hardware erases
every settings-table row and every other database-backed fact this project
has (jobs, mappings, agent reports) — ordinarily a recoverable, even
unremarkable event: a lost schedule-interval override reverts to a sane
built-in default, someone notices the sweep cadence looks different, and
moves on.

For an ordinary tuning knob that asymmetry between "survives in the volume"
and "survives in the operator's own backed-up config" is harmless. For a
privacy opt-out it is not, because the DIRECTION of the fallback decides
everything. Under the rejected ceiling design: environment says "on" (the
common, unset case), a deliberate database override says "off", the volume
is lost — the override is gone, the environment's "on" is now the only
remaining answer, and collection of playtime/last-played data silently
resumes. Nobody is notified. Nothing appears in a log. The only person
positioned to notice is the one whose data it is, and they have no reason to
go looking — they turned this off once, and as far as they know it is still
off. A control whose failure mode is "quietly starts collecting personal
data again, with no signal to anyone" is not a control.

This is a narrower, sharper version of the criterion ADR-0009 §5 already
uses for its own env-only allowlist ("bootstrap and security settings — how
the process finds its world or who may talk to it"). `relay_expose_playtime`
and `relay_expose_last_played` fit neither phrase — they are not about how
the process finds its world, and they are not an authentication/authorization
boundary. The criterion that actually applies here, and that this ADR adds
to the reasons a setting may belong on that list, is: **a setting whose loss
has a one-directional, silent failure mode where "lost" means "protection
resumes being off" does not belong in a store that can be lost independently
of the thing it is meant to control.** The environment file is the one
place SteamVault keeps that survives exactly the events that would otherwise
silently undo this decision — editing it and restarting is the same
deliberate, visible act as every other env-only setting on that list
requires.

## Consequences

- Two new `Settings` fields (`relay_expose_playtime`,
  `relay_expose_last_played`), each with its own `DEFAULT_RELAY_EXPOSE_*`
  constant, both `False` — see `vault_api/config.py`'s own docstring for the
  default argument (Phase 4h's privacy stance, echoing
  `DEFAULT_SWEEP_INCLUDE_CACHED`'s house style).
- `vault_api/routers/steam.py`'s `get_owned_games` applies the gate at the
  outermost conversion to the wire response: when a key is off, the
  corresponding `OwnedGameOut` field is never constructed with a value at
  all, and `response_model_exclude_unset=True` drops the JSON key entirely —
  not merely `0`/`null`, which a client could still read.
- `deploy/compose.yaml` forwards both variables directly (not left to a
  `compose.override.yaml` recipe): this project has three times now shipped
  a README sentence advising an operator to set a variable the stack never
  actually forwarded (`docs/LEARNINGS.md` "Containers"), and
  `tests/test_p1_compose_env_defaults.py` now fails if either forwarding
  line is ever removed.
- **What this costs, stated plainly, because a reader should not have to
  discover it themselves:** there is no runtime opt-out. An operator who
  decides right now that they want either field gone must edit
  `deploy/compose.yaml` (or `deploy/.env`) and restart the `vault-api`
  container — there is no `PATCH /v1/settings` call, and no web UI toggle,
  that can do it instead. This is a real ergonomics cost relative to every
  other boolean-shaped setting in this project (`sweep_include_cached`,
  `auto_gc`), which ARE one HTTP call away from changing. It is accepted
  because the alternative's failure mode — the database being the thing
  that decides, and the database being the thing that can vanish — was
  judged worse for these two specific keys than "changing your mind costs a
  restart".
- A reader of ADR-0009 alone, encountering `relay_expose_playtime`/
  `relay_expose_last_played` for the first time, would reasonably expect
  them to follow db > env > default like every other boolean setting in
  this codebase (`sweep_include_cached`, `auto_gc`). They do not: this ADR
  is the record of why these two specific keys are the exception, and
  `vault_api/settings_store.py`'s own module docstring cross-references it.
