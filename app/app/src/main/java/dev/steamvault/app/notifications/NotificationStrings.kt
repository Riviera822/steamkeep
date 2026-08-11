package dev.steamvault.app.notifications

import android.content.res.Resources
import dev.steamvault.app.R

/**
 * Title/body text for a posted [NotificationEvent] (WP 4b.8). Same
 * escape-hatch pattern as `ui/downloads/DownloadsStrings.kt` (see
 * app/README.md's "String resources" convention): [AndroidNotificationPoster]
 * is a plain, non-`@Composable` class running from a [NotificationPollWorker]
 * background thread, so it cannot call `stringResource(...)`; this interface
 * is resolved through plain `Resources.getString` and injected the same way.
 *
 * Not itself unit-tested (it is a thin `Resources.getString` pass-through,
 * same untested-by-design category as `AndroidDownloadsStrings`) -- what IS
 * tested is everything that decides WHICH text/channel/destination an event
 * maps to ([NotificationRouting], `NotificationRoutingTest`).
 */
interface NotificationStrings {
    fun jobFinishedTitle(): String
    fun jobFinishedBody(appid: Int): String
    fun jobFailedTitle(): String
    fun jobFailedBody(appid: Int): String
    fun updateReadyTitle(): String
    fun updateReadyBody(name: String): String
    fun bypassSuspectedTitle(): String
    fun bypassSuspectedBody(clientId: String): String
    fun bypassResolvedTitle(): String
    fun bypassResolvedBody(clientId: String): String
}

class AndroidNotificationStrings(private val resources: Resources) : NotificationStrings {
    override fun jobFinishedTitle() = resources.getString(R.string.notif_job_finished_title)
    override fun jobFinishedBody(appid: Int) = resources.getString(R.string.notif_job_finished_body, appid)
    override fun jobFailedTitle() = resources.getString(R.string.notif_job_failed_title)
    override fun jobFailedBody(appid: Int) = resources.getString(R.string.notif_job_failed_body, appid)
    override fun updateReadyTitle() = resources.getString(R.string.notif_update_ready_title)
    override fun updateReadyBody(name: String) = resources.getString(R.string.notif_update_ready_body, name)
    override fun bypassSuspectedTitle() = resources.getString(R.string.notif_bypass_suspected_title)
    override fun bypassSuspectedBody(clientId: String) =
        resources.getString(R.string.notif_bypass_suspected_body, clientId)
    override fun bypassResolvedTitle() = resources.getString(R.string.notif_bypass_resolved_title)
    override fun bypassResolvedBody(clientId: String) =
        resources.getString(R.string.notif_bypass_resolved_body, clientId)
}

/** @return (title, body) for [event] -- the one place [NotificationEvent]'s
 * fields are mapped onto [NotificationStrings]' per-type methods. */
fun textsFor(event: NotificationEvent, strings: NotificationStrings): Pair<String, String> = when (event) {
    is NotificationEvent.JobFinished -> strings.jobFinishedTitle() to strings.jobFinishedBody(event.appid)
    is NotificationEvent.JobFailed -> strings.jobFailedTitle() to strings.jobFailedBody(event.appid)
    is NotificationEvent.UpdateReady ->
        strings.updateReadyTitle() to strings.updateReadyBody(event.name ?: event.appid.toString())
    is NotificationEvent.BypassSuspected -> strings.bypassSuspectedTitle() to strings.bypassSuspectedBody(event.clientId)
    is NotificationEvent.BypassResolved -> strings.bypassResolvedTitle() to strings.bypassResolvedBody(event.clientId)
}
