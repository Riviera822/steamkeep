package dev.steamvault.app.ui.detail.logic

/**
 * Which "confirmed current" line the detail sheet should show (WP 4b.6
 * review fix S4). `apps.last_manifest_check` DELIBERATELY survives
 * `DELETE /v1/cache/{appid}` while `last_prefill_at` is unconditionally
 * cleared (api/README.md "Per-game deletion": "`last_manifest_check` is
 * deliberately NOT cleared here... the value survives a cache deletion
 * unlike `last_prefill_at`"). Rendered naively, that produces a confusing
 * pair of lines right after a deletion -- "Never downloaded" directly above
 * "Confirmed current at <a real, but now stale-sounding, timestamp>" -- which
 * reads as a contradiction even though both lines are individually true.
 * [confirmedCurrentWording] names the three cases so the sheet can qualify
 * the timestamp instead of presenting it bare.
 *
 * Pure -- no Compose, no resources. Covered by `DetailWordingTest`.
 */
enum class ConfirmedCurrentWording {
    /** `last_manifest_check` is `null` -- this app has never had a run that
     * CONFIRMED it current (api/README.md "Job outcome honesty"). */
    NEVER_CONFIRMED,

    /** The normal case: a real timestamp, and the app still has (or once
     * had, tracked) prefill history alongside it -- "Confirmed current at X". */
    CONFIRMED,

    /** `last_manifest_check` is non-null but `last_prefill_at` is `null` --
     * exactly the post-deletion shape above: the confirmation is real and
     * historically accurate, but the cache that was confirmed current no
     * longer exists. Rendered as "Confirmed current at X (before the cache
     * was cleared)" so the two lines stop reading as a contradiction. */
    CONFIRMED_BEFORE_CACHE_CLEARED,
}

fun confirmedCurrentWording(lastPrefillAt: String?, lastManifestCheck: String?): ConfirmedCurrentWording = when {
    lastManifestCheck == null -> ConfirmedCurrentWording.NEVER_CONFIRMED
    lastPrefillAt == null -> ConfirmedCurrentWording.CONFIRMED_BEFORE_CACHE_CLEARED
    else -> ConfirmedCurrentWording.CONFIRMED
}
