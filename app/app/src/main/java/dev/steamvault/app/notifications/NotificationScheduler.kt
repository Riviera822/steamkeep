package dev.steamvault.app.notifications

import android.content.Context
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import java.util.concurrent.TimeUnit

/**
 * Schedules [NotificationPollWorker] (WP 4b.8 brief: "PeriodicWorkRequest at
 * the 15-min WorkManager minimum, constrained to NETWORK_CONNECTED,
 * respecting Doze by design").
 *
 * **Doze, spelled out (brief: "document that WorkManager under Doze
 * batches/defers and that this is the accepted v1 behavior per the plan").**
 * This deliberately uses NEITHER exact alarms (`AlarmManager.setExactAndAllowWhileIdle`)
 * NOR a foreground service NOR a battery-optimization-exemption prompt --
 * all three would fight the platform's Doze/App-Standby power model instead
 * of living inside it, and the last one in particular would ask the user
 * for a permission most apps should not need. A plain constrained
 * `PeriodicWorkRequest` is the documented, Doze-cooperative API: Android
 * may batch this work into its periodic Doze maintenance windows and
 * defer/skip runs the network constraint cannot satisfy while the device is
 * idle -- so the REAL interval between notifications while the phone sits
 * untouched is "15 minutes, or considerably longer under deep Doze", never
 * a hard guarantee. That is accepted for v1 per
 * `docs/design/vault-app-mockup-NOTES.md` ("Notifications are a poll, not a
 * push") and `docs/WORKPACKAGES.md` Phase 4b's own wording ("polling via
 * WorkManager, respecting Doze") -- a v1 user who wants faster feedback
 * already has the foreground Library/Downloads screens' own 2-20s polling
 * loops (`polling/PollingIntervals.kt`) while the app is actually open.
 *
 * 15 minutes is `PeriodicWorkRequest.MIN_PERIODIC_INTERVAL_MILLIS`'s
 * documented floor -- WorkManager clamps a shorter request up to it
 * silently, so this uses the floor value directly rather than asking for
 * something WorkManager would rewrite anyway.
 *
 * **[ExistingPeriodicWorkPolicy.UPDATE], not `KEEP` (review fix S3).**
 * Calling [ensureScheduled] on every app process start (from
 * [dev.steamvault.app.VaultApplication.onCreate]) must NOT restart the
 * periodic window each time -- but this WP originally reached for `KEEP`
 * on a false KEEP-vs-REPLACE dichotomy (`KEEP` no-ops if already
 * scheduled, `REPLACE` resets WorkManager's internal next-run clock). That
 * framing missed a third option WorkManager has shipped since 2.8 (this
 * project pins 2.9.1): `UPDATE` applies a CHANGED request spec (a
 * different interval, constraints, or worker class in some future WP) to
 * the existing unique work WITHOUT resetting its period/next-run clock --
 * the best of both. With `KEEP`, once a device has this work scheduled
 * once, NO future change to [INTERVAL_MINUTES] or the network constraint
 * would ever reach it again (every subsequent [ensureScheduled] call would
 * silently no-op forever); `UPDATE` keeps every future app update capable
 * of actually changing the schedule on already-installed devices.
 */
object NotificationScheduler {
    const val UNIQUE_WORK_NAME = "vault-notification-poll"
    const val INTERVAL_MINUTES = 15L

    fun ensureScheduled(context: Context) {
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()
        val request = PeriodicWorkRequestBuilder<NotificationPollWorker>(INTERVAL_MINUTES, TimeUnit.MINUTES)
            .setConstraints(constraints)
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            UNIQUE_WORK_NAME,
            ExistingPeriodicWorkPolicy.UPDATE,
            request,
        )
    }
}
