package dev.steamvault.app.net.steam

import okhttp3.HttpUrl.Companion.toHttpUrl

/**
 * The custom-scheme app link this app registers as its OpenID `return_to`
 * (WP 4b.3 brief: "pick an app link scheme, document the choice + manifest
 * intent-filter").
 *
 * **Choice: a custom `steamvault://` scheme, not an `https://` return_to.**
 * SteamVault has no hosted web presence of its own (it is a self-hosted
 * homelab tool -- `vault-api` is reachable only over the user's LAN/VPN,
 * never a public domain by default), so there is no HTTPS endpoint this app
 * could register as a redirect target even if it wanted one. A custom
 * scheme deep link is the standard native-app OpenID/OAuth pattern for
 * exactly this situation (no public HTTPS callback surface available) --
 * `AndroidManifest.xml`'s `MainActivity` carries a matching
 * `BROWSABLE`+`DEFAULT` intent-filter for `steamvault://auth/openid-return`,
 * and `MainActivity.onNewIntent` is where the redirected Custom Tab hands
 * the callback back to this app.
 *
 * **`realm` equals `return_to` exactly.** OpenID 2.0's realm-matching rules
 * exist to let a `return_to` be a NARROWER URL than a wildcarded realm (a
 * realm with a leading `*.` subdomain wildcard matching a `return_to` on
 * any one of its subdomains) -- this
 * app has no such wildcard need (one fixed callback URL), and using the
 * identical URL for both is an explicitly PERMITTED degenerate case of the
 * spec's realm/return_to relationship, not a workaround.
 *
 * **Honest caveat (WP brief: maximize the pure-logic testable surface,
 * since the browser round trip itself cannot be exercised here).** Steam's
 * concrete OpenID implementation is not independently documented to
 * validate non-`http(s)` realms/return_to URLs the same way a fully
 * spec-compliant generic OpenID provider would -- this repo has no way to
 * confirm empirically, from this environment, that Valve's login page
 * accepts and correctly redirects to a `steamvault://` URL rather than
 * silently rejecting it or mangling it in transit. That confirmation is
 * exactly the "on-device only" verification step called out in this WP's
 * final report; if it turns out Valve rejects the custom scheme, the fix is
 * narrow (swap [RETURN_TO]/[REALM] for a scheme Valve accepts) and does not
 * touch anything downstream of [SteamOpenIdCallback.parse].
 */
object SteamOpenIdConfig {
    const val SCHEME = "steamvault"
    const val HOST = "auth"
    const val PATH = "/openid-return"
    const val RETURN_TO = "$SCHEME://$HOST$PATH"
    const val REALM = RETURN_TO

    /** The one host this app will ever send an OpenID request/verification to. */
    const val STEAM_HOST = "steamcommunity.com"
    const val LOGIN_ENDPOINT = "https://$STEAM_HOST/openid/login"
}

/**
 * Pure `checkid_setup` URL construction -- no network, no Android
 * framework dependency, fully JVM-testable (the WP brief's explicit ask:
 * "OpenID URL construction (literal expected URL pin)").
 *
 * Built via `okhttp3.HttpUrl.Builder` (the same URL-building primitive
 * [dev.steamvault.app.net.VaultApiClient] already uses) rather than hand
 * assembled string concatenation, so query-parameter percent-encoding is
 * OkHttp's own well-tested implementation, not a hand-rolled one.
 */
object SteamOpenIdLoginUrl {
    fun build(
        returnTo: String = SteamOpenIdConfig.RETURN_TO,
        realm: String = SteamOpenIdConfig.REALM,
    ): String {
        val builder = SteamOpenIdConfig.LOGIN_ENDPOINT.toHttpUrl().newBuilder()
        builder.addQueryParameter("openid.ns", "http://specs.openid.net/auth/2.0")
        builder.addQueryParameter("openid.mode", "checkid_setup")
        builder.addQueryParameter("openid.return_to", returnTo)
        builder.addQueryParameter("openid.realm", realm)
        builder.addQueryParameter(
            "openid.identity",
            "http://specs.openid.net/auth/2.0/identifier_select",
        )
        builder.addQueryParameter(
            "openid.claimed_id",
            "http://specs.openid.net/auth/2.0/identifier_select",
        )
        return builder.build().toString()
    }
}
