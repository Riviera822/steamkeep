package dev.steamvault.app.ui.downloads.logic

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Mirrors `web/tests/log-excerpt.test.js` against `selectExcerptDisplay`'s
 * branch order, plus [ExcerptCache]'s own state-machine (first-expand
 * fetch, cached re-expand, failed fetch allows retry) -- the stateful half
 * `log-excerpt.js` only ever demonstrated inline inside `downloads.js`. */
class LogExcerptTest {

    // -----------------------------------------------------------------
    // selectExcerptDisplay -- pure presentation, no fetch involved.
    // -----------------------------------------------------------------

    @Test
    fun `collapsed when not expanded, regardless of other fields`() {
        val display = selectExcerptDisplay(ExcerptFetchState(expanded = false, excerpt = "some text"))
        assertEquals(ExcerptState.COLLAPSED, display.state)
    }

    @Test
    fun `loading takes priority once expanded`() {
        val display = selectExcerptDisplay(ExcerptFetchState(expanded = true, loading = true))
        assertEquals(ExcerptState.LOADING, display.state)
    }

    @Test
    fun `error state carries the message`() {
        val display = selectExcerptDisplay(ExcerptFetchState(expanded = true, error = "boom"))
        assertEquals(ExcerptState.ERROR, display.state)
        assertEquals("boom", display.message)
    }

    @Test
    fun `empty when the excerpt is blank`() {
        val display = selectExcerptDisplay(ExcerptFetchState(expanded = true, excerpt = ""))
        assertEquals(ExcerptState.EMPTY, display.state)
    }

    @Test
    fun `empty when the excerpt has never been fetched (null, not loading, no error)`() {
        val display = selectExcerptDisplay(ExcerptFetchState(expanded = true))
        assertEquals(ExcerptState.EMPTY, display.state)
    }

    @Test
    fun `ready splits the excerpt into lines`() {
        val display = selectExcerptDisplay(ExcerptFetchState(expanded = true, excerpt = "line one\nline two"))
        assertEquals(ExcerptState.READY, display.state)
        assertEquals(listOf("line one", "line two"), display.lines)
        assertFalse(display.truncated)
    }

    @Test
    fun `ready strips the truncation marker and flags truncated`() {
        val display = selectExcerptDisplay(
            ExcerptFetchState(expanded = true, excerpt = "[...truncated...]\nthe rest\nof it"),
        )
        assertEquals(ExcerptState.READY, display.state)
        assertTrue(display.truncated)
        assertEquals(listOf("the rest", "of it"), display.lines)
    }

    // MUTATION PIN: the truncation check is `startsWith`, anchored to
    // position 0 (vault-api only ever prefixes a cut excerpt with the
    // marker, api/README.md "Queue semantics") -- a mutation to `contains`
    // would still pass every other test in this file, since all of them
    // either have the marker at position 0 or omit it entirely. A real
    // SteamPrefill/vault-api log line can legitimately quote the literal
    // string mid-stream (e.g. echoing a previous run's own truncated
    // excerpt back through some diagnostic path) without the CURRENT fetch
    // actually being cut -- that must NOT be reported as truncated, and the
    // marker text must NOT be stripped out of the middle of the body.
    @Test
    fun `a truncation marker appearing mid-stream, not at position 0, is not treated as truncation`() {
        val body = "first line\n[...truncated...]\nlast line"
        val display = selectExcerptDisplay(ExcerptFetchState(expanded = true, excerpt = body))
        assertEquals(ExcerptState.READY, display.state)
        assertFalse(display.truncated) // MUTATION TARGET: startsWith -> contains would flip this
        assertEquals(listOf("first line", "[...truncated...]", "last line"), display.lines)
    }

    // -----------------------------------------------------------------
    // ExcerptCache -- the lazy-fetch state machine (WP brief: "one JobDetail
    // fetch per job on first expand, cached for the session").
    // -----------------------------------------------------------------

    @Test
    fun `first expand triggers exactly one fetch`() = runTest {
        var fetchCount = 0
        val cache = ExcerptCache { _ ->
            fetchCount++
            Result.success("hello")
        }
        cache.toggleExpanded(1)
        cache.ensureLoaded(1)
        assertEquals(1, fetchCount)
        assertEquals("hello", cache.stateFor(1).excerpt)
    }

    @Test
    fun `a cached re-expand does not refetch (MUTATION TARGET)`() = runTest {
        var fetchCount = 0
        val cache = ExcerptCache { _ ->
            fetchCount++
            Result.success("hello")
        }
        cache.toggleExpanded(1) // expand
        cache.ensureLoaded(1)
        cache.toggleExpanded(1) // collapse
        cache.toggleExpanded(1) // expand again
        cache.ensureLoaded(1)
        assertEquals(1, fetchCount) // still exactly one network call
    }

    @Test
    fun `a genuinely empty log is cached too, not re-fetched`() = runTest {
        var fetchCount = 0
        val cache = ExcerptCache { _ ->
            fetchCount++
            Result.success("")
        }
        cache.toggleExpanded(1)
        cache.ensureLoaded(1)
        cache.ensureLoaded(1) // second call, e.g. a spurious re-invoke
        assertEquals(1, fetchCount)
        assertEquals(ExcerptState.EMPTY, selectExcerptDisplay(cache.stateFor(1)).state)
    }

    @Test
    fun `a failed fetch state is observable and allows a retry on next load`() = runTest {
        var fetchCount = 0
        val cache = ExcerptCache { _ ->
            fetchCount++
            if (fetchCount == 1) Result.failure(RuntimeException("network down")) else Result.success("recovered")
        }
        cache.toggleExpanded(1)
        cache.ensureLoaded(1)
        val afterFailure = cache.stateFor(1)
        assertEquals("network down", afterFailure.error)
        assertEquals(ExcerptState.ERROR, selectExcerptDisplay(afterFailure).state)
        assertFalse(afterFailure.loading)

        // Retry (e.g. collapse/re-expand, or a manual retry action) --
        // excerpt is still null, so the guard must allow a second attempt.
        cache.ensureLoaded(1)
        val afterRetry = cache.stateFor(1)
        assertEquals("recovered", afterRetry.excerpt)
        assertEquals(2, fetchCount)
    }

    @Test
    fun `different jobs have independent cache entries`() = runTest {
        val cache = ExcerptCache { jobId -> Result.success("excerpt for $jobId") }
        cache.toggleExpanded(1)
        cache.ensureLoaded(1)
        cache.toggleExpanded(2)
        cache.ensureLoaded(2)
        assertEquals("excerpt for 1", cache.stateFor(1).excerpt)
        assertEquals("excerpt for 2", cache.stateFor(2).excerpt)
    }

    @Test
    fun `a never-toggled job reports the default collapsed state`() {
        val cache = ExcerptCache { Result.success("unused") }
        val state = cache.stateFor(42)
        assertFalse(state.expanded)
        assertEquals(ExcerptState.COLLAPSED, selectExcerptDisplay(state).state)
    }
}
