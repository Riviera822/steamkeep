package dev.steamvault.app.ui.settings.logic

import dev.steamvault.app.net.model.SettingInfoOut
import dev.steamvault.app.net.model.settingAsStringListOrNull
import dev.steamvault.app.net.model.settingAsStringOrNull

/**
 * Pure presentation mapping for one `GET /v1/settings` entry (WP 4b.7;
 * ADR-0009) -- a port of `web/js/lib/settings-presentation.js`. Returns
 * small enums/booleans/strings rather than pre-resolved English text: the
 * actual wording lives in `strings.xml` (app/README.md's "String resources"
 * convention -- this is static UI chrome, resolved via `stringResource` at
 * the `SettingsScreen.kt` call site), kept separate so the "what does this
 * `applies`/`source` value actually mean" MAPPING is unit-testable without
 * any Android/Compose dependency.
 */
enum class SettingsApplies { IMMEDIATELY, NEXT_SWEEP, RESTART_REQUIRED, UNSPECIFIED }

/** @param wire one of `"immediately"`/`"next_sweep"`/`"restart-required"` (or an unrecognised value). */
fun parseSettingsApplies(wire: String): SettingsApplies = when (wire) {
    "immediately" -> SettingsApplies.IMMEDIATELY
    "next_sweep" -> SettingsApplies.NEXT_SWEEP
    "restart-required" -> SettingsApplies.RESTART_REQUIRED
    else -> SettingsApplies.UNSPECIFIED
}

enum class SettingsSource { DB, ENV, DEFAULT, UNKNOWN }

/** @param wire one of `"db"`/`"env"`/`"default"` (or an unrecognised value). */
fun parseSettingsSource(wire: String): SettingsSource = when (wire) {
    "db" -> SettingsSource.DB
    "env" -> SettingsSource.ENV
    "default" -> SettingsSource.DEFAULT
    else -> SettingsSource.UNKNOWN
}

/**
 * Whether a "revert to default/env" action is meaningful for this entry --
 * ADR-0009 decision 2: only a `db`-sourced value has an override row to
 * clear at all. Env-only rows never offer this (there is no override
 * concept for them).
 */
fun canResetSetting(entry: SettingInfoOut): Boolean = !entry.env_only && entry.source == "db"

/**
 * A settings entry's `effective` value as the string a plain text field
 * should be pre-filled with. `webhook_events` (the one list-typed value
 * `GET` returns) becomes a comma-joined string; `null`/JSON-null (the blank
 * "disabled" state `schedule_window`/`webhook_url` share, ADR-0009) becomes
 * an empty string, matching what typing nothing and submitting would mean.
 */
fun effectiveAsFieldText(entry: SettingInfoOut): String =
    entry.effective.settingAsStringListOrNull()?.joinToString(",")
        ?: entry.effective.settingAsStringOrNull()
        ?: ""
