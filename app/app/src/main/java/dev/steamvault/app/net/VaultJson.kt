package dev.steamvault.app.net

import kotlinx.serialization.json.Json

/**
 * The single kotlinx.serialization [Json] instance every DTO under
 * `dev.steamvault.app.net.model` is encoded/decoded with (WP 4b.2).
 *
 * `ignoreUnknownKeys = true` is DELIBERATE: vault-api's Pydantic response
 * models keep gaining fields as the schema evolves (api/README.md's
 * "Database schema" history alone runs v4 through v13 across the fields
 * this client reads — `last_manifest_check`, `needs_force`, `gc_execute`,
 * `paused_at`/`stop_request`, ...), and this client must not hard-fail the
 * moment a future api/ work package adds one more. The corresponding
 * safety net on the way OUT is that every model field this client does not
 * itself need has a sane Kotlin default, so decoding a response that is
 * missing a field this client's own vault-api version has never sent still
 * succeeds — see each data class under `net/model` for field-by-field
 * notes on which fields are load-bearing (no default) versus
 * forward/backward-compat (defaulted).
 *
 * `encodeDefaults = true` is equally deliberate on the ENCODING side:
 * kotlinx.serialization's own default (`false`) omits a property from the
 * output entirely when it equals its Kotlin default — so
 * `GcRequest(execute = false)` would encode as `{}`, not `{"execute":
 * false}`. That happens to be harmless against vault-api today (Pydantic's
 * own `execute: StrictBool = False` treats a missing field the same as an
 * explicit `false`), but relying on two independently-maintained defaults
 * staying in lockstep forever is exactly the kind of silent coupling
 * `docs/LEARNINGS.md` warns against elsewhere — every request body this
 * client sends is the FULL, explicit model instead.
 */
val VaultJson: Json = Json {
    ignoreUnknownKeys = true
    isLenient = false
    encodeDefaults = true
}
