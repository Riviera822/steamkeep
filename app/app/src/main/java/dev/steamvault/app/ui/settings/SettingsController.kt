package dev.steamvault.app.ui.settings

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.SettingsOut
import dev.steamvault.app.net.steam.submitWebApiKey
import dev.steamvault.app.repo.SteamIdentityRepository
import dev.steamvault.app.repo.SteamLoginResult
import dev.steamvault.app.storage.CredentialStore
import dev.steamvault.app.ui.settings.logic.SettingDraft
import dev.steamvault.app.ui.settings.logic.buildSettingsPatchDraft

/** What the Connection section shows -- never the API key itself (WP brief: "never the API key"). */
data class ConnectionSummary(val profileKind: String?, val baseUrl: String?) {
    val isConfigured: Boolean get() = profileKind != null && !baseUrl.isNullOrBlank()
}

/**
 * Everything the Settings screen needs (WP 4b.7 brief) -- same thin-glue
 * shape every other screen controller in this app documents. Three
 * independent surfaces, each backed by its own real source, same split
 * `web/js/views/settings.js` documents:
 *
 *  - Vault / Schedule / Webhook -- one form over `GET`/`PATCH /v1/settings`
 *    (ADR-0009). The PATCH body is built by
 *    `ui/settings/logic/SettingsDiff.kt` from [drafts], populated ONLY by
 *    fields the user actually edits (via [setDraft]/[resetDraft]) -- never
 *    pre-seeded with every field's current value on [load] -- which is what
 *    makes "the body contains only changed keys" true by construction.
 *  - Steam identity -- the existing sign-in/out state
 *    ([dev.steamvault.app.repo.SteamIdentityRepository], unchanged from WP
 *    4b.3) plus Web API key management this WP adds a UI path for
 *    (masked display, remove -- [removeWebApiKey]).
 *  - Connection -- [connectionSummary] reads [CredentialStore] directly
 *    (never the API key); [disconnect] clears the WHOLE store
 *    ("forget this vault" -- [CredentialStore.clear]'s own documented
 *    contract, deliberately broader than [CredentialStore.clearSteamIdentity]),
 *    and relies on the caller ([dev.steamvault.app.MainActivity]) to notice
 *    the connection is gone and swap back to the onboarding gate -- this
 *    controller has no reference to (and does not need one) whatever
 *    polling loop was running on another screen: this app's screens are
 *    plain state-switched (`ui/nav/Destination.kt`), so a screen not
 *    currently composed has no `LaunchedEffect` alive to stop in the first
 *    place, and a stale [dev.steamvault.app.net.VaultApiClient] can no
 *    longer be reached (nothing keeps `MainActivity`'s [VaultApiClient]
 *    reference around once it rebuilds from the now-cleared store).
 */
class SettingsController(
    private val client: VaultApiClient,
    private val credentialStore: CredentialStore,
    private val identityRepository: SteamIdentityRepository,
    private val strings: SettingsStrings,
) {
    var loading by mutableStateOf(true)
        private set
    var loadError by mutableStateOf<String?>(null)
        private set
    var settingsResponse by mutableStateOf<SettingsOut?>(null)
        private set
    var drafts by mutableStateOf<Map<String, SettingDraft>>(emptyMap())
        private set
    var saving by mutableStateOf(false)
        private set
    var saveError by mutableStateOf<String?>(null)
        private set
    var toast by mutableStateOf<String?>(null)
        private set

    var identityState by mutableStateOf(identityRepository.state())
        private set
    var loginError by mutableStateOf<String?>(null)
        private set
    var webApiKeyInput by mutableStateOf("")
    var webApiKeyError by mutableStateOf<String?>(null)
        private set

    val isDirty: Boolean get() = drafts.isNotEmpty()
    val isReadonly: Boolean get() = settingsResponse?.readonly ?: false

    suspend fun load() {
        loading = true
        loadError = null
        try {
            settingsResponse = client.settings()
        } catch (e: VaultApiError) {
            loadError = e.detail ?: strings.loadFailedFallback(e)
        } finally {
            loading = false
        }
    }

    fun setDraft(key: String, draft: SettingDraft) {
        drafts = drafts + (key to draft)
    }

    fun resetDraft(key: String) {
        drafts = drafts + (key to SettingDraft.Reset)
    }

    fun discard() {
        drafts = emptyMap()
    }

    suspend fun save() {
        val entries = settingsResponse?.settings ?: return
        val patch = buildSettingsPatchDraft(entries, drafts)
        if (patch.isEmpty()) {
            drafts = emptyMap()
            return
        }
        saving = true
        saveError = null
        try {
            settingsResponse = client.patchSettings(patch)
            drafts = emptyMap()
            toast = strings.savedToast()
        } catch (e: VaultApiError) {
            // 422 field errors (api/README.md: "one bad value... fails the
            // request... with a DISTINCT detail") are surfaced verbatim --
            // vault_api's detail string already names the offending key
            // (e.g. "'schedule_window': ...").
            saveError = e.detail ?: strings.saveFailedFallback()
        } finally {
            saving = false
        }
    }

    fun dismissToast() {
        toast = null
    }

    // ---- Steam identity -----------------------------------------------------

    fun refreshIdentity() {
        identityState = identityRepository.state()
    }

    /** @return the `checkid_setup` URL to open in a Custom Tab -- caller
     * ([dev.steamvault.app.MainActivity]) launches it; the redirect back
     * arrives at `onNewIntent`, which must call [completeSteamLogin] on
     * THIS controller instance while it is the active one. */
    fun buildSteamLoginUrl(): String {
        loginError = null
        return identityRepository.buildLoginUrl()
    }

    suspend fun completeSteamLogin(rawCallbackUrl: String) {
        when (val result = identityRepository.completeLogin(rawCallbackUrl)) {
            is SteamLoginResult.Success -> {
                refreshIdentity()
                loginError = null
            }
            is SteamLoginResult.Failure -> {
                refreshIdentity()
                loginError = result.reason
            }
        }
    }

    fun signOutSteam() {
        identityRepository.signOut()
        refreshIdentity()
    }

    /** WP brief: "closing the recorded setWebApiKey UI gap" -- see
     * `submitWebApiKey`'s kdoc for the unconditional field-clearing guarantee. */
    fun submitWebApiKeyEntry() {
        val result = submitWebApiKey(
            rawInput = webApiKeyInput,
            invalidFormatError = strings.invalidWebApiKeyFormat(),
            genericError = { strings.webApiKeySaveFailed(it) },
            persist = { identityRepository.setWebApiKey(it) },
        )
        webApiKeyInput = result.nextFieldValue
        webApiKeyError = result.error
        if (result.ok) {
            refreshIdentity()
            toast = strings.webApiKeySavedToast()
        }
    }

    /** Masked management (WP brief: "never the value") -- only clears the
     * stored key, never displays it. */
    fun removeWebApiKey() {
        credentialStore.setSteamWebApiKey(null)
        refreshIdentity()
        toast = strings.webApiKeyRemovedToast()
    }

    // ---- Connection -----------------------------------------------------------

    fun connectionSummary(): ConnectionSummary =
        ConnectionSummary(profileKind = credentialStore.getProfileKind(), baseUrl = credentialStore.getBaseUrl())

    /** See this class's kdoc for exactly what "forget this vault" clears and why. */
    fun disconnect() {
        credentialStore.clear()
    }
}
