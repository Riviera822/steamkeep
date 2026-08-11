package dev.steamvault.app.notifications

/**
 * The notification event taxonomy (WP 4b.8 brief) -- Kotlin port of
 * `web/js/notifications.js`'s five event types, onto the same real
 * `GET /v1/jobs` / `GET /v1/games` / `GET /v1/clients` shapes this app
 * already models (`net/model/Jobs.kt`, `Games.kt`, `Clients.kt`).
 *
 * Mirrors the web module's event taxonomy 1:1, including
 * [BypassResolved] -- not literally named in
 * `docs/design/vault-app-mockup-NOTES.md`'s bell-panel list, but present
 * per `docs/LEARNINGS.md`'s Transition-detector rule ("persist state
 * changes in BOTH directions... or enabling an event later fires falsely
 * on first sight", carried over verbatim from `web/js/notifications.js`'s
 * own kdoc for the same reasoning).
 *
 * `key` is a stable per-event identity string (`"job:1:done"`,
 * `"game:42:stale"`, `"client:workshop-pc:suspected"`, same shape as the
 * web module's `key` field) -- used as the seed for
 * [NotificationRouting.notificationId] so a duplicate POST of the exact
 * same event (the idempotency crash-recovery case documented on
 * [NotificationPollLogic]) updates the same Android system notification
 * instead of stacking a second one.
 */
sealed class NotificationEvent {
    abstract val key: String

    data class JobFinished(val jobId: Int, val appid: Int, val jobType: String) : NotificationEvent() {
        override val key: String get() = "job:$jobId:done"
    }

    data class JobFailed(val jobId: Int, val appid: Int, val jobType: String) : NotificationEvent() {
        override val key: String get() = "job:$jobId:error"
    }

    data class UpdateReady(val appid: Int, val name: String?) : NotificationEvent() {
        override val key: String get() = "game:$appid:stale"
    }

    data class BypassSuspected(val clientId: String) : NotificationEvent() {
        override val key: String get() = "client:$clientId:suspected"
    }

    data class BypassResolved(val clientId: String) : NotificationEvent() {
        override val key: String get() = "client:$clientId:resolved"
    }
}
