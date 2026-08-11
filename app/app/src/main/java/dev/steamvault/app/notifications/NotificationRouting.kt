package dev.steamvault.app.notifications

import dev.steamvault.app.ui.nav.Destination

/**
 * Event-type -> channel + tap-destination mapping (WP 4b.8 brief: "one
 * channel per event class... tap -> open app at the right destination").
 * Pure and literal-pinned (`NotificationRoutingTest`) -- no derivation from
 * the enums under test, same "literal-vs-literal" rule
 * `docs/LEARNINGS.md`'s Android section requires for every cross-frontend
 * wire-format contract, applied here to a same-app contract that is just as
 * easy to silently break by refactoring an enum name.
 */
enum class NotificationChannelDef(val id: String) {
    /** `job_finished` / `job_failed` -- "finished downloads (Cached)... job
     * Failures" (mockup-notes.md round 5). */
    DOWNLOADS("downloads"),

    /** `update_ready`. */
    UPDATES("updates"),

    /** `bypass_suspected` / `bypass_resolved`. */
    BYPASS("bypass"),
}

object NotificationRouting {
    /** Intent extra key carrying a [Destination.name] string -- read by
     * `MainActivity.handleNotificationIntent`. */
    const val EXTRA_DESTINATION = "dev.steamvault.app.notification.destination"

    fun channelFor(event: NotificationEvent): NotificationChannelDef = when (event) {
        is NotificationEvent.JobFinished, is NotificationEvent.JobFailed -> NotificationChannelDef.DOWNLOADS
        is NotificationEvent.UpdateReady -> NotificationChannelDef.UPDATES
        is NotificationEvent.BypassSuspected, is NotificationEvent.BypassResolved -> NotificationChannelDef.BYPASS
    }

    /**
     * Where a tap should land. Job events -> Downloads (brief: "Downloads
     * for job events"). Bypass events -> Settings (brief: "Settings/clients
     * context for bypass" -- honest simplification: this app has no
     * dedicated clients sheet yet, unlike `web/js/views/clients.js`, so
     * Settings is the closest existing surface, same "keep it simple"
     * instruction the brief gives). `update_ready` -> Library: the brief
     * does not name a destination for it explicitly, and a per-game detail
     * deep link (what the mockup's bell panel does) does not exist as an
     * addressable destination in this app yet -- Library is the honest,
     * closest landing spot; a follow-up WP that adds a "focus this appid"
     * extra to the Library screen could sharpen this further.
     */
    fun destinationFor(event: NotificationEvent): Destination = when (event) {
        is NotificationEvent.JobFinished, is NotificationEvent.JobFailed -> Destination.DOWNLOADS
        is NotificationEvent.UpdateReady -> Destination.LIBRARY
        is NotificationEvent.BypassSuspected, is NotificationEvent.BypassResolved -> Destination.SETTINGS
    }

    /**
     * Stable per-event Android notification id, derived from
     * [NotificationEvent.key] (`"job:1:done"`, ...). Stability matters for
     * the idempotency design (`NotificationPollLogic`'s kdoc): if a crash
     * causes the SAME event to be posted twice across two worker runs, the
     * second POST must UPDATE the existing system notification (same id)
     * rather than stack a visible duplicate.
     *
     * N1: `String.hashCode()` collisions between two DIFFERENT event keys
     * are theoretically possible (32-bit hash over an unbounded key space)
     * -- accepted, not mitigated with a channel-scoped mask/offset. Worst
     * case is one event's notification overwriting another's tray entry
     * (e.g. a `job:1:done` and an unrelated key that happens to hash
     * identically); both events were still independently derived and
     * posted correctly by [NotificationPollLogic], the diff/idempotency
     * guarantees this WP actually cares about are untouched, and only the
     * user-visible tray entry could, in an astronomically unlikely case,
     * show the more recent of the two instead of both simultaneously.
     */
    fun notificationId(event: NotificationEvent): Int = event.key.hashCode()
}
