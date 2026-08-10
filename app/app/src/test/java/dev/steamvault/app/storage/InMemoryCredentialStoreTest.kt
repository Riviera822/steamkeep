package dev.steamvault.app.storage

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Contract coverage for [CredentialStore], run against the JVM fake (WP 4b.2 brief). */
class InMemoryCredentialStoreTest {

    @Test
    fun `all values start unset, including the WP 4b3 Steam identity fields`() {
        val store = InMemoryCredentialStore()
        assertNull(store.getApiKey())
        assertNull(store.getBaseUrl())
        assertNull(store.getProfileKind())
        assertNull(store.getSteamId64())
        assertNull(store.getSteamPersonaName())
        assertNull(store.getSteamWebApiKey())
    }

    @Test
    fun `set then get round-trips each value independently`() {
        val store = InMemoryCredentialStore()
        store.setApiKey("test-key-123")
        store.setBaseUrl("http://192.168.1.50:8080")
        store.setProfileKind(ProfileKind.SYSTEM_VPN)
        store.setSteamId64("76561198042117903")
        store.setSteamPersonaName("Example Persona")
        store.setSteamWebApiKey("0123456789ABCDEF0123456789ABCDEF")

        assertEquals("test-key-123", store.getApiKey())
        assertEquals("http://192.168.1.50:8080", store.getBaseUrl())
        assertEquals(ProfileKind.SYSTEM_VPN, store.getProfileKind())
        assertEquals("76561198042117903", store.getSteamId64())
        assertEquals("Example Persona", store.getSteamPersonaName())
        assertEquals("0123456789ABCDEF0123456789ABCDEF", store.getSteamWebApiKey())
    }

    @Test
    fun `setting a value to null clears it, not just overwrites with an empty string`() {
        val store = InMemoryCredentialStore()
        store.setApiKey("test-key-123")

        store.setApiKey(null)

        assertNull(store.getApiKey())
    }

    @Test
    fun `clear removes every value, including Steam identity`() {
        val store = InMemoryCredentialStore()
        store.setApiKey("k")
        store.setBaseUrl("http://host")
        store.setProfileKind(ProfileKind.PUBLIC_DOMAIN)
        store.setSteamId64("76561198042117903")
        store.setSteamPersonaName("Example Persona")
        store.setSteamWebApiKey("0123456789ABCDEF0123456789ABCDEF")

        store.clear()

        assertNull(store.getApiKey())
        assertNull(store.getBaseUrl())
        assertNull(store.getProfileKind())
        assertNull(store.getSteamId64())
        assertNull(store.getSteamPersonaName())
        assertNull(store.getSteamWebApiKey())
    }

    @Test
    fun `clearSteamIdentity clears only the Steam fields, leaving the vault connection untouched`() {
        val store = InMemoryCredentialStore()
        store.setApiKey("k")
        store.setBaseUrl("http://host")
        store.setProfileKind(ProfileKind.SYSTEM_VPN)
        store.setSteamId64("76561198042117903")
        store.setSteamPersonaName("Example Persona")
        store.setSteamWebApiKey("0123456789ABCDEF0123456789ABCDEF")

        store.clearSteamIdentity()

        assertNull(store.getSteamId64())
        assertNull(store.getSteamPersonaName())
        assertNull(store.getSteamWebApiKey())
        assertEquals("k", store.getApiKey())
        assertEquals("http://host", store.getBaseUrl())
        assertEquals(ProfileKind.SYSTEM_VPN, store.getProfileKind())
    }
}
