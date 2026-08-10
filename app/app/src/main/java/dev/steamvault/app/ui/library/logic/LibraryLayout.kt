package dev.steamvault.app.ui.library.logic

/**
 * Library layout choice (WP 4b.4 brief: "grid 2/3/list, persisted choice") --
 * ports the mockup's `STATE.layout` three-way (docs/design/
 * vault-app-mockup-NOTES.md, "Library layout is configurable, round 4"):
 * 2 columns (default, artwork does the recognising), 3 columns (~50% more
 * titles per screen), and a list (small capsule, title, size, status per
 * row -- the only layout that never truncates a title).
 *
 * *"The real app stores this locally (DataStore / SharedPreferences), not
 * on the server: it is a per-device preference"* (mockup-notes.md) -- see
 * `storage/LibraryPreferences.kt` for the persistence side. This enum only
 * carries the pure wire-value mapping, so the string round-trip is
 * unit-testable without touching Android's `SharedPreferences` at all.
 */
enum class LibraryLayout(val prefValue: String) {
    GRID_2("grid2"),
    GRID_3("grid3"),
    LIST("list");

    companion object {
        val DEFAULT: LibraryLayout = GRID_2

        /** Falls back to [DEFAULT] for anything unrecognized (a future
         * layout value written by a newer app version, or corrupted prefs)
         * -- never crashes on an unknown persisted string. */
        fun fromPrefValue(value: String?): LibraryLayout =
            entries.firstOrNull { it.prefValue == value } ?: DEFAULT
    }
}
