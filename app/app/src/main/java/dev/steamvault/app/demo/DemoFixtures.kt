package dev.steamvault.app.demo

import dev.steamvault.app.net.model.ClientOut
import java.time.Instant

/**
 * The curated seed data for a fresh demo session (WP APP-DEMO brief:
 * "invented fixtures, clearly labelled, exercising every screen and
 * state"). Every appid/name/client id below is fictional -- modeled on
 * `web/js/demo-data.js`'s tone and shape (per the WP brief's own pointer),
 * not copied from it, so this app's demo library reads as its own fixture
 * rather than a re-skin. None of it is real Steam data
 * (docs/LEARNINGS.md "Testing discipline": fixtures are synthetic, modeled
 * on real structure, never personal data).
 *
 * Six games chosen to exercise the same handful of states a screenshot or
 * a first look needs:
 *  - Duskfall Array: a plain `done` game, fully confirmed current.
 *  - Cobalt Isthmus: `idle` + `needs_force` with a SURVIVING
 *    `last_manifest_check` -- the post-cache-deletion shape
 *    (api/README.md), exercising `ui/detail/logic/DetailWording.kt`'s
 *    "confirmed before the cache was cleared" branch.
 *  - Wrenfield Static: `running` -- backed by the one seed job that starts
 *    already in flight, so the Downloads screen has something live to show
 *    immediately.
 *  - Halcyon Ledger / Pale Meridian: a genuine SHARED depot between two
 *    `done` games -- deleting either alone must skip that one depot (the
 *    other is still cached), exercising `ui/library/logic/MultiPlan.kt`'s
 *    sharing arithmetic end to end.
 *  - Marrowlight: `error` + `needs_force`, with a non-zero GC dry-run
 *    result (`gcReclaimableBytes`/`gcHeldBackBytes`) so the detail sheet's
 *    GC flow has something to find on the very first check.
 */

private fun isoAgo(secondsAgo: Long): String = Instant.now().minusSeconds(secondsAgo).toString()

internal fun seedGames(): MutableList<DemoGame> = mutableListOf(
    DemoGame(
        appid = 4_010_010,
        name = "Duskfall Array",
        status = DemoState.STATUS_DONE,
        needsForce = false,
        depots = mutableListOf(DemoDepot(depotid = 4_010_011, shared = false, sizeBytes = 3_800_000_000L)),
        lastPrefillAt = isoAgo(3 * 3_600L),
        lastManifestCheck = isoAgo(3_600L),
        gcReclaimableBytes = 90_000_000L,
        gcHeldBackBytes = 0L,
    ),
    DemoGame(
        appid = 4_010_020,
        name = "Cobalt Isthmus",
        status = DemoState.STATUS_IDLE,
        needsForce = true,
        depots = mutableListOf(),
        lastPrefillAt = null,
        // Survives cache deletion (api/README.md) -- CONFIRMED_BEFORE_CACHE_CLEARED.
        lastManifestCheck = isoAgo(2 * 86_400L),
        gcReclaimableBytes = 0L,
        gcHeldBackBytes = 0L,
    ),
    DemoGame(
        appid = 4_010_030,
        name = "Wrenfield Static",
        status = DemoState.STATUS_RUNNING,
        needsForce = false,
        depots = mutableListOf(DemoDepot(depotid = 4_010_031, shared = false, sizeBytes = 900_000_000L)),
        lastPrefillAt = null,
        lastManifestCheck = null,
        gcReclaimableBytes = 0L,
        gcHeldBackBytes = 0L,
    ),
    DemoGame(
        appid = 4_010_040,
        name = "Halcyon Ledger",
        status = DemoState.STATUS_DONE,
        needsForce = false,
        depots = mutableListOf(
            DemoDepot(depotid = 4_010_041, shared = false, sizeBytes = 700_000_000L),
            DemoDepot(depotid = 4_010_060, shared = true, sizeBytes = 250_000_000L),
        ),
        lastPrefillAt = isoAgo(4 * 3_600L),
        lastManifestCheck = null,
        gcReclaimableBytes = 0L,
        gcHeldBackBytes = 0L,
    ),
    DemoGame(
        appid = 4_010_050,
        name = "Pale Meridian",
        status = DemoState.STATUS_DONE,
        needsForce = false,
        depots = mutableListOf(DemoDepot(depotid = 4_010_060, shared = true, sizeBytes = 250_000_000L)),
        lastPrefillAt = isoAgo(3_600L),
        lastManifestCheck = isoAgo(3_600L),
        gcReclaimableBytes = 0L,
        gcHeldBackBytes = 0L,
    ),
    DemoGame(
        appid = 4_010_070,
        name = "Marrowlight",
        status = DemoState.STATUS_ERROR,
        needsForce = true,
        depots = mutableListOf(DemoDepot(depotid = 4_010_071, shared = false, sizeBytes = 1_500_000_000L)),
        lastPrefillAt = isoAgo(3 * 3_600L),
        lastManifestCheck = null,
        gcReclaimableBytes = 30_000_000L,
        gcHeldBackBytes = 10_000_000L,
    ),
)

internal fun seedJobs(): MutableList<DemoJob> = mutableListOf(
    DemoJob(
        id = 900_099,
        appid = 4_010_010,
        type = "prefill",
        status = DemoState.STATUS_DONE,
        createdAt = isoAgo(6 * 3_600L),
        startedAt = isoAgo(6 * 3_600L - 5L),
        finishedAt = isoAgo(6 * 3_600L - 1L),
        updated = 8,
        upToDate = 0,
        summaryParseOk = true,
        logExcerpt = "[vault-api] worker claimed job 900099\nPrefilled 8 apps. Done.",
        ticksLeft = 0,
    ),
    DemoJob(
        id = 900_100,
        appid = 4_010_070,
        type = "prefill",
        status = DemoState.STATUS_ERROR,
        createdAt = isoAgo(3 * 3_600L),
        startedAt = isoAgo(3 * 3_600L - 5L),
        finishedAt = isoAgo(3 * 3_600L - 1L),
        updated = 0,
        upToDate = 0,
        summaryParseOk = true,
        logExcerpt = "[vault-api] SteamPrefill exited 1 -- see attached log.",
        ticksLeft = 0,
    ),
    DemoJob(
        id = 900_101,
        appid = 4_010_030,
        type = "prefill",
        status = DemoState.STATUS_RUNNING,
        createdAt = isoAgo(60L),
        startedAt = isoAgo(45L),
        logExcerpt = "[vault-api] worker claimed job 900101\nDownloading depot 4010031 ...",
        ticksLeft = 2,
    ),
)

internal fun seedClients(): List<ClientOut> = listOf(
    ClientOut(
        client_id = "demo-livingroom-pc",
        first_seen = Instant.now().minusSeconds(30L * 86_400L).toString(),
        last_reported_at = Instant.now().minusSeconds(600L).toString(),
        app_count = 3,
        source_addrs = listOf("10.0.0.42"),
        cache_hits = 812,
        cache_misses = 14,
        bytes_served = 42_000_000_000L,
        last_seen_in_cache_log = Instant.now().minusSeconds(600L).toString(),
        bypass_suspected = false,
    ),
    ClientOut(
        client_id = "demo-steamdeck",
        first_seen = Instant.now().minusSeconds(10L * 86_400L).toString(),
        last_reported_at = Instant.now().minusSeconds(7_200L).toString(),
        app_count = 1,
        source_addrs = listOf("10.0.0.57"),
        cache_hits = 96,
        cache_misses = 40,
        bytes_served = 3_100_000_000L,
        last_seen_in_cache_log = Instant.now().minusSeconds(7_200L).toString(),
        bypass_suspected = true,
    ),
)
