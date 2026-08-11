package dev.steamvault.app.ui.onboarding

import android.content.res.Resources
import dev.steamvault.app.R
import dev.steamvault.app.net.connection.ConnectionFailureReason

/**
 * [OnboardingController]'s dynamic strings -- same pattern/rationale as
 * `ui/library/LibraryStrings.kt` (app/README.md's "String resources"
 * convention): these are only known AFTER a suspend network call returns
 * inside a coroutine the controller launches (the connection-test result,
 * a Steam Web API key rejection), which is not composition time, so
 * `stringResource` cannot be called there directly.
 */
interface OnboardingStrings {
    fun invalidUrl(): String
    fun enterApiKeyFirst(): String
    fun connectionOk(): String
    fun connectionFailure(reason: ConnectionFailureReason): String
    fun invalidWebApiKeyFormat(): String
    fun webApiKeySaveFailed(cause: Throwable): String
}

class AndroidOnboardingStrings(private val resources: Resources) : OnboardingStrings {
    override fun invalidUrl(): String = resources.getString(R.string.onboarding_error_invalid_url)
    override fun enterApiKeyFirst(): String = resources.getString(R.string.onboarding_error_enter_key_first)
    override fun connectionOk(): String = resources.getString(R.string.onboarding_connection_ok)

    override fun connectionFailure(reason: ConnectionFailureReason): String = when (reason) {
        is ConnectionFailureReason.Unreachable -> resources.getString(R.string.onboarding_error_unreachable)
        is ConnectionFailureReason.KeyRejected -> resources.getString(R.string.onboarding_error_key_rejected)
        is ConnectionFailureReason.UnexpectedStatus -> resources.getString(
            R.string.onboarding_error_unexpected_status,
            reason.status ?: 0,
        )
    }

    override fun invalidWebApiKeyFormat(): String = resources.getString(R.string.onboarding_steam_key_invalid_format)
    override fun webApiKeySaveFailed(cause: Throwable): String =
        resources.getString(R.string.onboarding_steam_key_save_failed, cause.message ?: "")
}
