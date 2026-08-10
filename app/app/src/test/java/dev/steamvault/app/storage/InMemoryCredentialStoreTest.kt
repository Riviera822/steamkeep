package dev.steamvault.app.storage

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Contract coverage for [CredentialStore], run against the JVM fake (WP 4b.2 brief). */
class InMemoryCredentialStoreTest {

    @Test
    fun `all three values start unset`() {
        val store = InMemoryCredentialStore()
        assertNull(store.getApiKey())
        assertNull(store.getBaseUrl())
        assertNull(store.getProfileKind())
    }

    @Test
    fun `set then get round-trips each value independently`() {
        val store = InMemoryCredentialStore()
        store.setApiKey("test-key-123")
        store.setBaseUrl("http://192.168.1.50:8080")
        store.setProfileKind(ProfileKind.SYSTEM_VPN)

        assertEquals("test-key-123", store.getApiKey())
        assertEquals("http://192.168.1.50:8080", store.getBaseUrl())
        assertEquals(ProfileKind.SYSTEM_VPN, store.getProfileKind())
    }

    @Test
    fun `setting a value to null clears it, not just overwrites with an empty string`() {
        val store = InMemoryCredentialStore()
        store.setApiKey("test-key-123")

        store.setApiKey(null)

        assertNull(store.getApiKey())
    }

    @Test
    fun `clear removes every value`() {
        val store = InMemoryCredentialStore()
        store.setApiKey("k")
        store.setBaseUrl("http://host")
        store.setProfileKind(ProfileKind.PUBLIC_DOMAIN)

        store.clear()

        assertNull(store.getApiKey())
        assertNull(store.getBaseUrl())
        assertNull(store.getProfileKind())
    }
}
