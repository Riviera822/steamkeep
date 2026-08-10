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
 * **Gap this documents for the reviewer/orchestrator.** No onboarding/
 * settings UI writing `baseUrl`/`profileKind`/`apiKey` into [CredentialStore]
 * exists yet -- that is WP 4b.7 ("Onboarding + settings"), a
 * branch-parallel sibling of this WP, not a prerequisite of it. Until 4b.7
 * ships, [CredentialStore] is unconfigured on every real install and this
 * factory always returns `null`; the Library/Downloads screens' "not
 * connected" placeholder (see `MainActivity`) is therefore the ONLY
 * reachable state today, and manual testing of [dev.steamvault.app.ui.library.LibraryScreen]
 * against a real vault-api needs `adb shell` / a debug hook to seed
 * [CredentialStore] until 4b.7 lands.
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
