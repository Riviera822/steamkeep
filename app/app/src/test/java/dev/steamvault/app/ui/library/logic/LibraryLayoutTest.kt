package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.storage.InMemoryLibraryPreferences
import org.junit.Assert.assertEquals
import org.junit.Test

class LibraryLayoutTest {

    @Test
    fun `default layout is grid2`() {
        assertEquals(LibraryLayout.GRID_2, LibraryLayout.DEFAULT)
    }

    @Test
    fun `round-trips every value through its pref string`() {
        for (layout in LibraryLayout.entries) {
            assertEquals(layout, LibraryLayout.fromPrefValue(layout.prefValue))
        }
    }

    @Test
    fun `unknown or null pref value falls back to the default, never crashes`() {
        assertEquals(LibraryLayout.DEFAULT, LibraryLayout.fromPrefValue(null))
        assertEquals(LibraryLayout.DEFAULT, LibraryLayout.fromPrefValue(""))
        assertEquals(LibraryLayout.DEFAULT, LibraryLayout.fromPrefValue("grid4"))
    }

    @Test
    fun `InMemoryLibraryPreferences persists a chosen layout across reads`() {
        val prefs = InMemoryLibraryPreferences()
        assertEquals(LibraryLayout.GRID_2, prefs.getLayout())

        prefs.setLayout(LibraryLayout.LIST)

        assertEquals(LibraryLayout.LIST, prefs.getLayout())
    }
}
