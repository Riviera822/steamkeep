package dev.steamvault.app.ui.clients.logic

import dev.steamvault.app.net.model.ClientOut
import kotlin.math.roundToInt

/**
 * Clients sheet presentation logic (WP 4b.10) — Kotlin port of
 * `web/js/lib/clients-view.js`'s pure transforms over `GET /v1/clients`
 * (`vault_api/routers/clients.py`'s `ClientOut`, WP 3.11's
 * `bypass_suspected`): which section a client belongs in (the mockup's
 * round-5 "Bypassing" / "Healthy" grouping) and the per-row hit-rate math.
 *
 * **Wording keeps the backend's own "fails toward NOT accusing" posture**
 * (routers/clients.py's module docstring: "a false positive here sends an
 * operator hunting a network fault that does not exist"). Per
 * app/README.md's string-resource convention, the actual sentences (which
 * state what was OBSERVED, never a verdict) live in `strings.xml` and are
 * assembled in `ClientsSheet.kt`'s `@Composable`s from the plain data this
 * file computes — see `ClientsCrossFrontendContractTest` for the literal
 * pin against `clients-view.js`'s own wording (`BYPASS_EXPLANATION`,
 * `describeBypassClient`, the section headings) so the two frontends
 * cannot silently drift apart on this wording, same bar
 * `docs/LEARNINGS.md`'s Android section holds every other cross-frontend
 * contract to.
 *
 * **Verified-against-the-actual-DOM correction (LEARNINGS "verify
 * empirically over believing docs").** `BYPASS_EXPLANATION` is shown under
 * EVERY bypass-suspected row, not once per section — `clients-view.js`'s
 * own header comment claims the opposite ("shown once per section rather
 * than repeated per row"), but `clients-sheet.js::buildRow` actually
 * appends a fresh hint paragraph inside its `if (bypass)` branch for EACH
 * card it builds, i.e. once per bypassing client. `ClientsSheet.kt` ports
 * the code, not the stale comment.
 *
 * Pure only — no Android/Compose dependency. Covered in `ClientsViewTest`.
 */

/** @see [partitionClients] */
data class ClientsPartition(val bypassing: List<ClientOut>, val healthy: List<ClientOut>)

/**
 * Splits a `GET /v1/clients` snapshot into the mockup's two sections,
 * preserving order within each bucket — 1:1 port of `clients-view.js`'s
 * `partitionClients`.
 */
fun partitionClients(clients: List<ClientOut>): ClientsPartition = ClientsPartition(
    bypassing = clients.filter { it.bypass_suspected },
    healthy = clients.filter { !it.bypass_suspected },
)

/**
 * hits/(hits+misses) as a rounded whole-number percentage, or `null` when
 * there have been zero cache requests to compute a rate from — never
 * fabricate "0%" for "no data yet" (same "nothing honest to print" posture
 * `ui/library/logic/Format.kt::formatBytesGB` documents). A negative
 * counter (should not happen server-side, but `ClientOut`'s fields are
 * plain `Int`s decoded from untrusted JSON) is treated as 0 rather than
 * propagating a negative rate — mirrors `clients-view.js::safeCount`'s
 * NaN/negative guard, minus the NaN case Kotlin's non-nullable `Int` cannot
 * represent in the first place.
 */
fun hitRatePercent(client: ClientOut): Int? {
    val hits = safeCount(client.cache_hits)
    val misses = safeCount(client.cache_misses)
    val total = hits + misses
    if (total <= 0) return null
    return ((hits.toDouble() / total) * 100).roundToInt()
}

private fun safeCount(value: Int): Int = if (value > 0) value else 0

/** The volatile, poll-tick-derived half of [ClientRowModel] — see that
 * class's kdoc for why it is split out into its own nested value rather
 * than flattened. */
data class ClientRowStats(
    val gamesReported: Int?,
    val bytesServed: Long,
    val hitRatePercent: Int?,
)

/**
 * The Compose-list stability model for one client row (WP 4b.10 brief:
 * "follow the pattern the 4b.5 reviewer endorsed — a tick that changes only
 * a drift field must not rebuild the row", `ui/downloads/logic/JobCardModel.kt`'s
 * kdoc for `JobCardAction`).
 *
 * **Why this is a real, if differently-shaped, port of the web render-plan
 * (`clients-render-plan.js`).** That module's `full`/`patch`/`rebuild`
 * verdict exists to avoid an imperative DOM rebuild resetting scroll
 * position (its own kdoc: "no ANIMATED node at stake here... reused for a
 * different, real reason"). Compose has no equivalent imperative
 * "rebuild the DOM" step to avoid — a `LazyColumn` keyed by [clientId]
 * already reuses a row's composable slot across ticks by construction. What
 * *is* still a genuine, testable claim on this platform is the same shape
 * `JobCardModel`'s `action` field carries: [stats] is the ONLY field a poll
 * tick may change while [clientId]/[bypassSuspected]/[addresses] stay put —
 * i.e. a stats-only tick can never ALSO silently move a client to the other
 * section, and a section flip can never hide inside what looks like a
 * stats update. `ClientsViewTest`'s two `a stats-only diff changes ONLY
 * the stats field` / `a bypass_suspected flip changes ONLY that field`
 * tests pin both directions, the same way `JobCardModelTest` pins the
 * `stop_request`-only diff.
 */
data class ClientRowModel(
    val clientId: String,
    val bypassSuspected: Boolean,
    val addresses: List<String>,
    val stats: ClientRowStats,
)

fun buildClientRowModel(client: ClientOut): ClientRowModel = ClientRowModel(
    clientId = client.client_id,
    bypassSuspected = client.bypass_suspected,
    addresses = client.source_addrs,
    stats = ClientRowStats(
        gamesReported = client.app_count,
        bytesServed = client.bytes_served,
        hitRatePercent = hitRatePercent(client),
    ),
)
