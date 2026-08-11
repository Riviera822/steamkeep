package dev.steamvault.app.notifications

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import androidx.core.content.getSystemService
import dev.steamvault.app.R

/**
 * Creates the three notification channels (WP 4b.8 brief: "one channel per
 * event class... user can silence classes"). Android-framework-bound,
 * device-territory (not unit tested -- see app/README.md's "No instrumented
 * tests" note); kept thin on purpose, same shape as
 * `EncryptedCredentialStore`'s "one narrow guarantee, pinned structurally
 * elsewhere" split -- [NotificationChannelDef]'s id/[NotificationRouting]'s
 * mapping are the parts that ARE unit-tested.
 *
 * `createNotificationChannel` is safe to call every time the app process
 * starts (idempotent -- re-registering an existing channel id updates its
 * name/description without resetting the user's own importance/sound
 * choice for it), so [ensureChannels] is called unconditionally from
 * [dev.steamvault.app.VaultApplication.onCreate].
 *
 * Importance: `BYPASS` is `IMPORTANCE_HIGH` -- a suspected cache bypass is a
 * heads-up-worthy warning (mockup-notes.md bell panel: "Warnings"),
 * `DOWNLOADS`/`UPDATES` are `IMPORTANCE_DEFAULT` (a finished/failed download
 * or an available update is useful but not urgent). Below API 26 channels
 * don't exist at all -- but this app's `minSdk` is 26, so that branch is
 * unreachable in practice; `NotificationChannel` itself is API 26+ so no
 * SDK guard is needed for the class reference.
 */
object NotificationChannels {
    fun ensureChannels(context: Context) {
        val manager = context.getSystemService<NotificationManager>() ?: return
        manager.createNotificationChannel(
            NotificationChannel(
                NotificationChannelDef.DOWNLOADS.id,
                context.getString(R.string.notif_channel_downloads_name),
                NotificationManager.IMPORTANCE_DEFAULT,
            ).apply { description = context.getString(R.string.notif_channel_downloads_desc) },
        )
        manager.createNotificationChannel(
            NotificationChannel(
                NotificationChannelDef.UPDATES.id,
                context.getString(R.string.notif_channel_updates_name),
                NotificationManager.IMPORTANCE_DEFAULT,
            ).apply { description = context.getString(R.string.notif_channel_updates_desc) },
        )
        manager.createNotificationChannel(
            NotificationChannel(
                NotificationChannelDef.BYPASS.id,
                context.getString(R.string.notif_channel_bypass_name),
                NotificationManager.IMPORTANCE_HIGH,
            ).apply { description = context.getString(R.string.notif_channel_bypass_desc) },
        )
    }
}
