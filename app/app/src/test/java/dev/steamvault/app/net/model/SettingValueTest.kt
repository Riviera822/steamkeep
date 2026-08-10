package dev.steamvault.app.net.model

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Pure JsonElement <-> typed-value helpers for `SettingInfoOut.effective`/`fallback`. */
class SettingValueTest {

    @Test
    fun `settingAsStringOrNull reads a string primitive`() {
        assertEquals("dry-run", JsonPrimitive("dry-run").settingAsStringOrNull())
    }

    @Test
    fun `settingAsStringOrNull is null for JsonNull`() {
        assertNull(JsonNull.settingAsStringOrNull())
    }

    @Test
    fun `settingAsStringOrNull is null for a non-scalar element`() {
        assertNull(JsonArray(emptyList()).settingAsStringOrNull())
        assertNull(JsonObject(emptyMap()).settingAsStringOrNull())
    }

    @Test
    fun `settingAsIntOrNull reads a numeric primitive`() {
        assertEquals(30, JsonPrimitive(30).settingAsIntOrNull())
    }

    @Test
    fun `settingAsIntOrNull is null for a non-numeric string`() {
        assertNull(JsonPrimitive("dry-run").settingAsIntOrNull())
    }

    @Test
    fun `settingAsBooleanOrNull reads a boolean primitive`() {
        assertEquals(true, JsonPrimitive(true).settingAsBooleanOrNull())
        assertEquals(false, JsonPrimitive(false).settingAsBooleanOrNull())
    }

    @Test
    fun `settingAsBooleanOrNull is null for a primitive whose content is neither true nor false`() {
        // kotlinx.serialization's JsonPrimitive.booleanOrNull matches on
        // CONTENT text ("true"/"false"), not the isString flag -- a quoted
        // JSON string "true" would actually parse as true. Pick content
        // that cannot be confused with a boolean literal either way.
        assertNull(JsonPrimitive("dry-run").settingAsBooleanOrNull())
    }

    @Test
    fun `settingAsStringListOrNull reads a homogeneous string array`() {
        val array = JsonArray(listOf(JsonPrimitive("job_finished"), JsonPrimitive("job_failed")))
        assertEquals(listOf("job_finished", "job_failed"), array.settingAsStringListOrNull())
    }

    @Test
    fun `settingAsStringListOrNull is null for JsonNull`() {
        assertNull(JsonNull.settingAsStringListOrNull())
    }

    @Test
    fun `settingAsStringListOrNull is null when any element is not a string`() {
        val array = JsonArray(listOf(JsonPrimitive("job_finished"), JsonPrimitive(1)))
        assertNull(array.settingAsStringListOrNull())
    }

    @Test
    fun `settingPatchValue string wraps a bare string`() {
        assertEquals(JsonPrimitive("dry-run"), settingPatchValue("dry-run"))
    }

    @Test
    fun `settingPatchValue list wraps a list of strings`() {
        val expected = JsonArray(listOf(JsonPrimitive("job_finished"), JsonPrimitive("job_failed")))
        assertEquals(expected, settingPatchValue(listOf("job_finished", "job_failed")))
    }

    @Test
    fun `buildSettingsPatch turns a null into a literal JSON null, not an omitted key`() {
        val patch = buildSettingsPatch(mapOf("vault_name" to null))
        assertEquals(JsonNull, patch["vault_name"])
        assertEquals(1, patch.size)
    }

    @Test
    fun `buildSettingsPatch keeps a set value as-is`() {
        val patch = buildSettingsPatch(mapOf("auto_gc" to settingPatchValue("dry-run")))
        assertEquals(JsonPrimitive("dry-run"), patch["auto_gc"])
    }
}
