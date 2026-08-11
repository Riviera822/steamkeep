package dev.steamvault.app.ui.settings.logic

import dev.steamvault.app.net.model.SettingInfoOut
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SettingsPresentationTest {

    private fun entry(
        key: String = "vault_name",
        effective: kotlinx.serialization.json.JsonElement = JsonPrimitive("vault-01"),
        source: String = "db",
        fallback: kotlinx.serialization.json.JsonElement = JsonNull,
        applies: String = "immediately",
        envOnly: Boolean = false,
    ) = SettingInfoOut(key, effective, source, fallback, applies, envOnly)

    // ---- parseSettingsApplies -----------------------------------------------

    @Test
    fun `parses all three known applies values`() {
        assertEquals(SettingsApplies.IMMEDIATELY, parseSettingsApplies("immediately"))
        assertEquals(SettingsApplies.NEXT_SWEEP, parseSettingsApplies("next_sweep"))
        assertEquals(SettingsApplies.RESTART_REQUIRED, parseSettingsApplies("restart-required"))
    }

    @Test
    fun `MUTATION PIN -- an unrecognised applies value falls back to UNSPECIFIED, not a silent default`() {
        assertEquals(SettingsApplies.UNSPECIFIED, parseSettingsApplies("whenever-i-feel-like-it"))
    }

    // ---- parseSettingsSource ------------------------------------------------

    @Test
    fun `parses all three known source values`() {
        assertEquals(SettingsSource.DB, parseSettingsSource("db"))
        assertEquals(SettingsSource.ENV, parseSettingsSource("env"))
        assertEquals(SettingsSource.DEFAULT, parseSettingsSource("default"))
    }

    @Test
    fun `MUTATION PIN -- an unrecognised source value falls back to UNKNOWN`() {
        assertEquals(SettingsSource.UNKNOWN, parseSettingsSource("mystery"))
    }

    // ---- canResetSetting ------------------------------------------------------

    @Test
    fun `a db-sourced, non-env-only entry can be reset`() {
        assertTrue(canResetSetting(entry(source = "db", envOnly = false)))
    }

    @Test
    fun `MUTATION PIN -- an env-sourced entry cannot be reset even if source string is db-like elsewhere`() {
        assertFalse(canResetSetting(entry(source = "env")))
        assertFalse(canResetSetting(entry(source = "default")))
    }

    @Test
    fun `MUTATION PIN -- an env-only entry can never be reset, even if it somehow reports source db`() {
        assertFalse(canResetSetting(entry(source = "db", envOnly = true)))
    }

    // ---- effectiveAsFieldText -------------------------------------------------

    @Test
    fun `a string effective value round-trips as-is`() {
        assertEquals("vault-01", effectiveAsFieldText(entry(effective = JsonPrimitive("vault-01"))))
    }

    @Test
    fun `an int effective value becomes its decimal text`() {
        assertEquals("30", effectiveAsFieldText(entry(effective = JsonPrimitive(30))))
    }

    @Test
    fun `a JSON null effective value becomes an empty string`() {
        assertEquals("", effectiveAsFieldText(entry(effective = JsonNull)))
    }

    @Test
    fun `a webhook_events array becomes a comma-joined string`() {
        val effective = JsonArray(listOf(JsonPrimitive("job.done"), JsonPrimitive("job.error")))
        assertEquals("job.done,job.error", effectiveAsFieldText(entry(key = "webhook_events", effective = effective)))
    }

    @Test
    fun `an empty webhook_events array becomes an empty string, not a stray comma`() {
        assertEquals("", effectiveAsFieldText(entry(key = "webhook_events", effective = JsonArray(emptyList()))))
    }
}
