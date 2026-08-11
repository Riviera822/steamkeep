package dev.steamvault.app.ui.settings

import android.content.res.Resources
import dev.steamvault.app.R

/**
 * [SettingsController]'s dynamic strings -- same pattern as
 * `ui/library/LibraryStrings.kt`/`ui/onboarding/OnboardingStrings.kt`
 * (app/README.md's "String resources" convention): these are only known
 * after a suspend network call returns inside a coroutine the controller
 * launches, not at composition time.
 */
interface SettingsStrings {
    fun loadFailedFallback(cause: Throwable): String
    fun savedToast(): String
    fun saveFailedFallback(): String
    fun invalidWebApiKeyFormat(): String
    fun webApiKeySaveFailed(cause: Throwable): String
    fun webApiKeySavedToast(): String
    fun webApiKeyRemovedToast(): String
}

class AndroidSettingsStrings(private val resources: Resources) : SettingsStrings {
    override fun loadFailedFallback(cause: Throwable): String =
        resources.getString(R.string.settings_load_error, cause.message ?: "")
    override fun savedToast(): String = resources.getString(R.string.settings_toast_saved)
    override fun saveFailedFallback(): String = resources.getString(R.string.settings_toast_save_failed_fallback)
    override fun invalidWebApiKeyFormat(): String = resources.getString(R.string.onboarding_steam_key_invalid_format)
    override fun webApiKeySaveFailed(cause: Throwable): String =
        resources.getString(R.string.onboarding_steam_key_save_failed, cause.message ?: "")
    override fun webApiKeySavedToast(): String = resources.getString(R.string.settings_toast_webapikey_saved)
    override fun webApiKeyRemovedToast(): String = resources.getString(R.string.settings_toast_webapikey_removed)
}
