package dev.steamvault.app.notifications

import dev.steamvault.app.net.model.ClientOut
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.JobSummary

/**
 * The pure decision core of [NotificationPollWorker] (WP 4b.8 brief:
 * "extract decisions so the untestable shell is thin"). Given the previous
 * persisted snapshot and one poll's freshly fetched raw API lists, computes
 * the events to post AND the snapshot that should be persisted afterwards.
 * No Android, no WorkManager, no network, no notification-posting code --
 * fully exercised on the plain JVM by `NotificationPollLogicTest`.
 *
 * ## Idempotency design (WP 4b.8 brief: "a crashed run must not
 * double-notify")
 *
 * [NotificationPollWorker.doWork] calls [evaluate] once per run, then (a)
 * posts [NotificationPollResult.events] through [NotificationPoster], THEN
 * (b) calls `NotificationSnapshotStore.save(NotificationPollResult.snapshotToPersist)`
 * -- notify-then-persist, in that order, never the reverse. The two crash
 * windows this pins:
 *
 * - **Crash between fetch and persist (after or during notify).** The next
 *   run's [evaluate] call still reads the OLD snapshot (save never
 *   happened), fetches fresh data again, and -- because [evaluate] is a
 *   pure function of (prev snapshot, fresh lists) -- deterministically
 *   RE-DERIVES the identical event set for anything that has not changed
 *   again server-side in the meantime. The user sees the same
 *   notification(s) reposted; [NotificationRouting.notificationId] being a
 *   stable hash of the event's own key means a repost UPDATES the existing
 *   system notification rather than stacking a duplicate. This is the
 *   documented, accepted trade-off for a crash mid-run: a possible re-post
 *   of the same information, never a silently DROPPED one.
 * - **Crash strictly after persist.** The next run's [evaluate] call reads
 *   the NEW snapshot; nothing looks different anymore (everything the
 *   previous run already saw is now the baseline), so zero events are
 *   derived -- no re-notification.
 *
 * The alternative order (persist-before-notify) was rejected: a crash
 * between persist and notify would silently DROP the event forever (the
 * next run's diff baseline already reflects the "new" state, so the
 * transition that already happened can never be re-derived) -- a strictly
 * worse failure mode than an occasional harmless repost of the same
 * notification.
 */
object NotificationPollLogic {
    fun evaluate(
        prevSnapshot: NotificationSnapshot?,
        jobs: List<JobSummary>,
        games: List<GameSummary>,
        clients: List<ClientOut>,
    ): NotificationPollResult {
        val currSnapshot = buildSnapshot(jobs, games, clients)
        val events = diffSnapshots(prevSnapshot, currSnapshot)
        return NotificationPollResult(events, currSnapshot)
    }
}

data class NotificationPollResult(
    val events: List<NotificationEvent>,
    val snapshotToPersist: NotificationSnapshot,
)
