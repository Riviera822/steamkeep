package dev.steamvault.app.notifications

import android.content.Context
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.ProcessLifecycleOwner
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.profile.buildConnectivityProfile
import dev.steamvault.app.storage.EncryptedCredentialStore
import kotlinx.coroutines.CancellationException

/**
 * The WorkManager `CoroutineWorker` that actually runs a notification poll
 * (WP 4b.8 brief). Deliberately thin glue -- every real DECISION is made by
 * already-unit-tested pure code ([NotificationPollLogic.evaluate],
 * [NotificationRouting]); this class's own job is wiring Android/network/
 * storage objects together in the right order, which is why it is NOT unit
 * tested itself (device/WorkManager-runtime territory, same
 * `app/README.md` "No instrumented tests" boundary every other Android-
 * framework-bound class in this app already lives with -- see
 * `EncryptedCredentialStore`'s kdoc for the precedent). The honest device-
 * test list this class needs:
 *
 * - does WorkManager actually invoke [doWork] on the declared ~15-minute
 *   cadence, batched/deferred under Doze as expected (brief: "respecting
 *   Doze by design... WorkManager under Doze batches/defers... accepted v1
 *   behaviour");
 * - does a real [EncryptedCredentialStore]/Keystore round-trip actually
 *   supply working credentials to a background process (no Activity, no UI
 *   thread) the way it does to `MainActivity`;
 * - does [NotificationPoster]/[NotificationChannels] actually show a
 *   notification end-to-end (see `AndroidNotificationPoster`'s own list).
 *
 * ## Order of operations (idempotency -- see [NotificationPollLogic]'s kdoc)
 *
 * fetch -> [NotificationPollLogic.evaluate] -> POST events (unless
 * foreground) -> PERSIST the new snapshot, strictly in that order. Persist
 * happens unconditionally after the notify step -- including when
 * `foregroundActive` suppressed every actual system-notification call --
 * so the snapshot always advances to reflect what was just seen, and a
 * later background run never replays events that happened while the app
 * was in the foreground (see [AndroidNotificationPoster]'s kdoc for why
 * that matters).
 *
 * ## Fail-soft rules
 *
 * - No vault-api connection configured (`buildConnectivityProfile` returns
 *   `null`) or no API key stored -- succeed silently (brief: "no connection
 *   ⇒ succeed silently"). Nothing to poll; not a worker failure.
 * - The fetch call throws [VaultApiError] (network down, auth failure,
 *   server error, unparsable response) -- succeed WITHOUT persisting a new
 *   snapshot, leaving the old one in place so the next successful run diffs
 *   against a still-valid baseline instead of a partial/absent one. Not
 *   retried more aggressively than WorkManager's own periodic-request
 *   cadence already provides -- a `Result.retry()` would fight the
 *   already-15-minute-minimum period for no benefit.
 * - **Review fix S4: anything else this method throws is ALSO caught and
 *   turned into `Result.success()`.** The top device-test risk this class's
 *   own kdoc names above is [EncryptedCredentialStore]/Android Keystore
 *   behaving differently from a background process than it does from
 *   `MainActivity` -- this worker is the FIRST caller of it that isn't
 *   Activity-driven, and a Keystore failure mode that only manifests off
 *   the main thread / outside an Activity context is exactly the kind of
 *   thing that cannot be ruled out without a real device. Before this fix,
 *   only [VaultApiError] was caught -- any other exception (a Keystore
 *   throw, a `NotificationManager` failure, anything unexpected in the
 *   notify/persist steps) would propagate out of [doWork] uncaught. The
 *   specific [VaultApiError] catch stays as documentation of the EXPECTED
 *   failure path (and to return early without attempting notify/persist at
 *   all); the outer catch-all is the net underneath it for anything
 *   unexpected, per the reviewer's explicit preference for degrading this
 *   background poll to a silent no-op rather than letting it fail loudly.
 *   **[CancellationException] is deliberately rethrown, never swallowed**
 *   by that catch-all -- it is how structured concurrency tells a suspend
 *   function to stop (WorkManager cancelling this work because its
 *   constraints stopped holding, e.g. network dropped mid-fetch, throws it
 *   at the next suspension point); catching it as a plain failure and
 *   returning `Result.success()` would suppress the cancellation signal
 *   instead of cooperating with it, a well-known Kotlin-coroutines
 *   footgun distinct from every other exception this method might see.
 */
class NotificationPollWorker(
    context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        return try {
            val credentialStore = EncryptedCredentialStore(applicationContext)
            val profile = buildConnectivityProfile(credentialStore) ?: return Result.success()
            val apiKey = credentialStore.getApiKey()
            if (apiKey.isNullOrBlank()) return Result.success()

            val client = VaultApiClient(profile, apiKeyProvider = { credentialStore.getApiKey().orEmpty() })
            val snapshotStore = SharedPreferencesNotificationSnapshotStore(applicationContext)

            val (jobs, games, clients) = try {
                Triple(client.jobs(), client.games(), client.clients())
            } catch (_: VaultApiError) {
                return Result.success()
            }

            val prevSnapshot = snapshotStore.load()
            val result = NotificationPollLogic.evaluate(prevSnapshot, jobs, games, clients)

            // Process-lifecycle check (WP 4b.8 brief: "a process-lifecycle
            // check is enough") -- see AndroidNotificationPoster's kdoc for
            // the full foreground-suppression rule this implements.
            val foregroundActive =
                ProcessLifecycleOwner.get().lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)

            NotificationChannels.ensureChannels(applicationContext)
            val poster: NotificationPoster = AndroidNotificationPoster(
                applicationContext,
                AndroidNotificationStrings(applicationContext.resources),
            )
            for (event in result.events) poster.post(event, foregroundActive)

            // Persist AFTER the notify step, unconditionally -- see this
            // class's kdoc "Order of operations".
            snapshotStore.save(result.snapshotToPersist)

            Result.success()
        } catch (e: CancellationException) {
            // Never swallow coroutine cancellation -- see this class's kdoc
            // "Fail-soft rules" last bullet.
            throw e
        } catch (_: Exception) {
            // Review fix S4 -- see this class's kdoc "Fail-soft rules" last
            // bullet for the full reasoning.
            Result.success()
        }
    }
}
