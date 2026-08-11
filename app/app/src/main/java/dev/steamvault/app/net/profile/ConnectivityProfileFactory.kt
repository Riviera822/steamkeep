package dev.steamvault.app.net.profile

import dev.steamvault.app.storage.CredentialStore
import dev.steamvault.app.storage.ProfileKind

/**
 * Build a [ConnectivityProfile] from whatever is currently in
 * [CredentialStore] (WP 4b.4 reconciliation note -- see `MainActivity`'s
 * kdoc). Returns `null`, never throws, when the vault-api connection has
 * not been configured yet (fresh install, no base URL/profile kind stored)
 * OR when the stored value is malformed ([SystemVpnProfile]/
 * [PublicDomainProfile]'s constructors validate the URL and throw on a bad
 * one) -- either case means "there is nothing to connect to right now",
 * which the Library/Downloads screens render as an explicit
 * not-connected state rather than crash or silently retry a broken client.
 *
 * **Gap closed by WP 4b.7.** Before that WP, no onboarding/settings UI wrote
 * `baseUrl`/`profileKind`/`apiKey` into [CredentialStore] at all -- this
 * factory always returned `null`, and the Library/Downloads screens' "not
 * connected" placeholder (see `MainActivity`) was the ONLY reachable state.
 * `dev.steamvault.app.ui.onboarding.OnboardingController.finish` is the
 * write path that now populates all three fields, and `MainActivity`
 * rebuilds its `VaultApiClient` (via this function) every time the
 * connection changes -- onboarding finishing, or Settings' Disconnect
 * clearing it again.
 */
fun buildConnectivityProfile(store: CredentialStore): ConnectivityProfile? {
    val baseUrl = store.getBaseUrl()?.takeIf { it.isNotBlank() } ?: return null
    val kind = store.getProfileKind() ?: return null
    return try {
        when (kind) {
            ProfileKind.SYSTEM_VPN -> SystemVpnProfile(baseUrl)
            ProfileKind.PUBLIC_DOMAIN -> PublicDomainProfile(baseUrl)
            else -> null
        }
    } catch (_: IllegalArgumentException) {
        null
    } catch (_: CleartextNotAllowedException) {
        null
    }
}
