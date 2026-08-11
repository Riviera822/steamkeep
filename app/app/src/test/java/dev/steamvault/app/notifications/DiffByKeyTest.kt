package dev.steamvault.app.notifications

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Direct pins for [diffByKey]'s bucket semantics -- the primitive every
 * [NotificationDiffer] function is built on. */
class DiffByKeyTest {

    private data class Item(val id: Int, val value: String)

    @Test
    fun `null prevList is isFirst with everything in added`() {
        val curr = listOf(Item(1, "a"), Item(2, "b"))
        val diff = diffByKey(null, curr) { it.id }
        assertTrue(diff.isFirst)
        assertEquals(curr, diff.added)
        assertEquals(emptyList<Item>(), diff.updated)
        assertEquals(emptyList<Item>(), diff.removed)
        assertEquals(emptyList<Item>(), diff.unchanged)
    }

    @Test
    fun `empty prevList (not null) is NOT isFirst`() {
        val diff = diffByKey(emptyList(), listOf(Item(1, "a"))) { it.id }
        assertTrue(!diff.isFirst)
        assertEquals(listOf(Item(1, "a")), diff.added)
    }

    @Test
    fun `structurally identical item lands in unchanged, not updated`() {
        val prev = listOf(Item(1, "a"))
        val curr = listOf(Item(1, "a"))
        val diff = diffByKey(prev, curr) { it.id }
        assertEquals(listOf(Item(1, "a")), diff.unchanged)
        assertEquals(emptyList<Pair<Item, Item>>(), diff.updated)
    }

    @Test
    fun `a changed field moves the item to updated with the real prev`() {
        val prev = listOf(Item(1, "a"))
        val curr = listOf(Item(1, "b"))
        val diff = diffByKey(prev, curr) { it.id }
        assertEquals(listOf(Item(1, "a") to Item(1, "b")), diff.updated)
        assertEquals(emptyList<Item>(), diff.unchanged)
    }

    @Test
    fun `a key present only in prev lands in removed`() {
        val prev = listOf(Item(1, "a"), Item(2, "b"))
        val curr = listOf(Item(1, "a"))
        val diff = diffByKey(prev, curr) { it.id }
        assertEquals(listOf(Item(2, "b")), diff.removed)
    }
}
