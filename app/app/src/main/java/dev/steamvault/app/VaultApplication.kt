package dev.steamvault.app

import android.app.Application
import dev.steamvault.app.notifications.NotificationChannels
import dev.steamvault.app.notifications.NotificationScheduler

/**
 * Process-wide `Application` (WP 4b.8). The app had no custom `Application`
 * class before this WP (the manifest's `<application>` used the framework
 * default) -- this is the first thing this app needs that must run exactly
 * once per process start regardless of which entry point (launcher icon,
 * notification tap, OpenID redirect) created the task, which is precisely
 * what `Application.onCreate` guarantees and `MainActivity.onCreate` does
 * not (an Activity can be recreated multiple times per process, e.g. on
 * configuration change).
 *
 * Both calls are idempotent (see [NotificationChannels.ensureChannels] and
 * [NotificationScheduler.ensureScheduled]'s own kdocs), so running them on
 * every process start -- including ones where the user never finished
 * onboarding, or has since disconnected -- is safe:
 * [dev.steamvault.app.notifications.NotificationPollWorker] itself is what
 * checks whether a vault-api connection actually exists and succeeds
 * silently if not (see its kdoc's "Fail-soft rules").
 */
class VaultApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        NotificationChannels.ensureChannels(this)
        NotificationScheduler.ensureScheduled(this)
    }
}
