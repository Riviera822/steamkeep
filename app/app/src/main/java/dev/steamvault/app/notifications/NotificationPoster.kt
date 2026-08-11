package dev.steamvault.app.notifications

import android.Manifest
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import dev.steamvault.app.MainActivity
import dev.steamvault.app.R

/**
 * Posts one Android system notification for a [NotificationEvent] (WP
 * 4b.8). Android-framework-bound, device-territory (not unit tested --
 * `NotificationManagerCompat`/permission checks need a real system service;
 * see app/README.md's "No instrumented tests" note). Deliberately thin: the
 * decisions this class defers to already-tested pure code are
 * [NotificationRouting] (channel/destination/id) and [textsFor]
 * (title/body) -- this class's own job is just "call the three Android
 * APIs those decisions require", listed honestly for the device test pass:
 *
 * - does `NotificationManagerCompat.notify` actually show a heads-up/tray
 *   notification with the right channel, title, body and tap target on a
 *   real device;
 * - does the POST_NOTIFICATIONS permission prompt (API 33+) actually appear
 *   and does denying it leave the rest of the app (worker included) working;
 * - does tapping the notification actually land on the right
 *   [dev.steamvault.app.ui.nav.Destination] via `MainActivity.onNewIntent`.
 *
 * ## Foreground-suppression rule (WP 4b.8 brief)
 *
 * `foregroundActive` is supplied by the caller ([NotificationPollWorker]),
 * not read here, so this class stays a plain function of its inputs. The
 * rule itself: **while `foregroundActive` is true, [post] is a no-op.**
 * Simplest honest interpretation of the brief ("cancel/skip notifications
 * while an activity is resumed... a process-lifecycle check is enough") --
 * this app's screens are foreground-only polling (Library/Downloads, see
 * `MainActivity`'s kdoc), so "an activity is resumed" and "the process
 * lifecycle is at least STARTED" are the same condition here; there is no
 * separate "is Library/Downloads specifically the visible screen" signal to
 * refine this with, and the brief explicitly calls a process-lifecycle
 * check sufficient. `NotificationPollWorker` still runs its full fetch +
 * diff + persist cycle while foreground -- only the POSTING step is
 * skipped, so returning to the background does not replay events the user
 * already saw live on screen (see that class's kdoc).
 *
 * ## Permission handling (API 33+)
 *
 * `POST_NOTIFICATIONS` (declared in `AndroidManifest.xml`) is a runtime
 * permission on API 33+. [post] checks it and silently no-ops if denied --
 * the brief's "gracefully degrade if denied: the worker still runs, just no
 * visible notifications". Below API 33 the permission does not exist as a
 * runtime grant (notifications were always allowed by default pre-33,
 * subject only to the user's Settings toggle, which `notify()` already
 * honours on its own without an explicit check here).
 */
class AndroidNotificationPoster(
    private val context: Context,
    private val strings: NotificationStrings,
) : NotificationPoster {

    override fun post(event: NotificationEvent, foregroundActive: Boolean) {
        if (foregroundActive) return // foreground-suppression rule, see class kdoc

        if (Build.VERSION.SDK_INT >= 33 &&
            ActivityCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            return // denied -- degrade gracefully, see class kdoc
        }

        val channel = NotificationRouting.channelFor(event)
        val (title, body) = textsFor(event, strings)

        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            putExtra(NotificationRouting.EXTRA_DESTINATION, NotificationRouting.destinationFor(event).name)
        }
        val notificationId = NotificationRouting.notificationId(event)
        val pendingIntent = PendingIntent.getActivity(
            context,
            notificationId,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val notification = NotificationCompat.Builder(context, channel.id)
            // Vault-shield silhouette, already shipped for the adaptive
            // launcher icon's monochrome layer (WP 4b.1) -- pure white on
            // transparent, exactly the shape an Android status-bar icon
            // needs. Not a purpose-built notification asset; revisit in the
            // WP 4b.9 release-art pass if a dedicated glyph is wanted.
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(title)
            .setContentText(body)
            .setAutoCancel(true)
            .setContentIntent(pendingIntent)
            // Review fix S2: makes the documented crash-repost trade-off
            // (NotificationPollLogic's kdoc -- a crash between notify and
            // persist can re-derive and re-post the SAME event on the next
            // run) actually silent for the user. NotificationManagerCompat
            // .notify() with the SAME id (NotificationRouting.notificationId
            // is a stable hash of the event's own key) already updates the
            // existing tray entry rather than stacking a duplicate, but
            // WITHOUT this flag it still re-alerts (sound/vibration/heads-up)
            // on every such update -- onlyAlertOnce makes a repost of
            // already-seen news update the tray silently, exactly like the
            // idempotency design intends.
            .setOnlyAlertOnce(true)
            .build()

        NotificationManagerCompat.from(context).notify(notificationId, notification)
    }
}

/** Seam for [NotificationPollWorker] -- see [AndroidNotificationPoster]'s kdoc
 * for the one production implementation and why it is not unit tested. */
interface NotificationPoster {
    fun post(event: NotificationEvent, foregroundActive: Boolean)
}
