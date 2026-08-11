package dev.steamvault.app.notifications

/**
 * Generic keyed-list differ (WP 4b.8) -- Kotlin port of
 * `web/js/diff-utils.js::diffByKey`. Pure, framework-free.
 *
 * One deliberate simplification versus the web version: `shallowJsonEqual`
 * there exists only because plain JS objects have no structural `equals` --
 * `JSON.stringify` is its stand-in. Kotlin `data class` already has a real,
 * field-by-field `equals()`/`hashCode()`, so [KeyDiff] compares items with
 * plain `==` instead of re-deriving a JSON-based equality; the diff
 * SEMANTICS (added/updated/removed/unchanged/isFirst) are otherwise
 * identical to the web module.
 *
 * @param T the compact snapshot entry type diffed (see
 *   `NotificationSnapshot.kt` -- this module is used ONLY against the
 *   compact per-domain entry types, not the raw API response models).
 */
data class KeyDiff<T>(
    val added: List<T>,
    val updated: List<Pair<T, T>>,
    val removed: List<T>,
    val unchanged: List<T>,
    /** `true` when [prevList] was `null` -- "no snapshot exists yet", the
     * first-ever poll. Distinct from an empty list (`[]`), which is a real,
     * previously-observed "nothing here" snapshot. */
    val isFirst: Boolean,
)

/**
 * @param prevList Previous snapshot, or `null` if this is the very first
 *   poll (no persisted snapshot exists at all).
 * @param currList Current snapshot (never null in practice here -- the
 *   caller always has a freshly fetched list, possibly empty).
 * @param keyFn Stable identity key for one item.
 */
fun <T, K> diffByKey(prevList: List<T>?, currList: List<T>, keyFn: (T) -> K): KeyDiff<T> {
    val currMap = LinkedHashMap<K, T>()
    for (item in currList) currMap[keyFn(item)] = item

    if (prevList == null) {
        return KeyDiff(
            added = currMap.values.toList(),
            updated = emptyList(),
            removed = emptyList(),
            unchanged = emptyList(),
            isFirst = true,
        )
    }

    val prevMap = LinkedHashMap<K, T>()
    for (item in prevList) prevMap[keyFn(item)] = item

    val added = mutableListOf<T>()
    val updated = mutableListOf<Pair<T, T>>()
    val unchanged = mutableListOf<T>()

    for ((key, item) in currMap) {
        val prevItem = prevMap[key]
        when {
            prevItem == null -> added.add(item)
            prevItem == item -> unchanged.add(item)
            else -> updated.add(prevItem to item)
        }
    }

    val removed = prevMap.filterKeys { it !in currMap }.values.toList()

    return KeyDiff(added = added, updated = updated, removed = removed, unchanged = unchanged, isFirst = false)
}
