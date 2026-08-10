package dev.steamvault.app.polling

import dev.steamvault.app.net.model.JobSummary

/**
 * Poll-interval decision functions (WP 4b.2 brief) — pure ports of
 * `web/js/store.js`'s `DEFAULT_INTERVALS` / `hasActiveJob` /
 * `nextJobsIntervalMs`, same numbers and the same active-status set, so
 * the Android app polls on the SAME cadence the web UI does rather than
 * inventing its own (mockup-notes.md's "poll + diff" model, shared by both
 * frontends). WorkManager wiring — the thing that actually calls these on
 * a schedule and respects Doze — is WP 4b.8, deliberately not this WP.
 */
object PollingIntervals {
    const val JOBS_FAST_MS = 2000L
    const val JOBS_SLOW_MS = 15000L
    const val GAMES_MS = 15000L
    const val CLIENTS_MS = 20000L

    private val JOB_ACTIVE_STATUSES = setOf("queued", "running", "paused")

    /** Pure: does this `GET /v1/jobs` snapshot contain a still-in-flight job? */
    fun hasActiveJob(jobs: List<JobSummary>): Boolean =
        jobs.any { it.status in JOB_ACTIVE_STATUSES }

    /**
     * Pure: which poll interval applies to the jobs loop given its most
     * recent snapshot (cadence: fast while anything is
     * queued/running/paused, slow otherwise).
     */
    fun nextJobsIntervalMs(
        jobs: List<JobSummary>,
        fastMs: Long = JOBS_FAST_MS,
        slowMs: Long = JOBS_SLOW_MS,
    ): Long = if (hasActiveJob(jobs)) fastMs else slowMs
}
