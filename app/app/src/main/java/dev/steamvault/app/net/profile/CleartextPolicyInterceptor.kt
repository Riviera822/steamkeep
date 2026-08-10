package dev.steamvault.app.net.profile

import okhttp3.Interceptor
import okhttp3.Response

/**
 * One more enforcement of the same cleartext policy
 * [ConnectivityProfile.allowsCleartext] already applies at profile
 * construction ([PublicDomainProfile]'s init block) — registered TWICE by
 * [dev.steamvault.app.net.VaultApiClient], once as an application
 * interceptor and once as a network interceptor. The two registrations
 * cover DIFFERENT things (see below); read this kdoc before changing
 * either one.
 *
 * **CORRECTION (WP 4b.2 Opus review round 1, BLOCKER B1).** An earlier
 * version of this kdoc claimed a single `addInterceptor` registration
 * alone would catch "a redirect response pointing at an `http://`
 * Location". That claim was FALSE, empirically demonstrated against the
 * pinned OkHttp 4.12.0:
 *
 * - An **application interceptor** (`OkHttpClient.Builder.addInterceptor`)
 *   wraps the ENTIRE call. It runs exactly ONCE per [dev.steamvault.app.net.VaultApiClient]
 *   call, sees only the ORIGINAL request, and `chain.proceed()` does not
 *   return until OkHttp's internal `RetryAndFollowUpInterceptor` has
 *   already issued and followed as many redirect hops as it decided to.
 *   It never sees an individual redirect TARGET — by the time it can
 *   inspect anything, following (or refusing) has already happened deeper
 *   in the chain. Its actual job is narrower: the pre-socket gate for the
 *   ORIGINAL request only (e.g. if some future call site ever built a
 *   request against a `PublicDomainProfile` directly, bypassing its own
 *   constructor guard).
 * - Only a **network interceptor** (`addNetworkInterceptor`) runs once per
 *   actual request OkHttp puts on the wire — including each redirect hop
 *   and any other internally-generated follow-up (e.g. an auth-challenge
 *   retry, which likewise never reaches application interceptors again).
 *   That is the layer that can actually see and reject an `http://`
 *   redirect target before ITS socket opens.
 *
 * On top of that, OkHttp forwards `X-Api-Key` (not an `Authorization`-class
 * header, so it is NOT one OkHttp strips on a host/scheme change) across a
 * redirect by default; `followSslRedirects` (whether an https<->http scheme
 * change is followed at all) defaults to `true`; and plain `followRedirects`
 * (whether ANY redirect — including an https-to-https one to a DIFFERENT
 * HOST, which `followSslRedirects` does NOT cover, WP 4b.2 delta review
 * should-fix S2) also defaults to `true`. An unpinned client therefore
 * silently follows a redirect — same-scheme cross-host or scheme-downgrading
 * — and leaks the key to whatever host it names. `VaultApiClient` closes
 * this with independent, redundant layers, all documented at its `client`
 * field: `followSslRedirects(false)` + `followRedirects(false)` (refuse to
 * auto-follow ANY redirect at all — no redirect is ever a legitimate
 * outcome for this client's fixed `/v1/...` paths) alongside this
 * interceptor registered both ways above.
 *
 * **On "independent": measured, not assumed (WP 4b.2 delta review
 * should-fix S1).** Against the CURRENT end-to-end redirect scenario, the
 * flag layer and the network-interceptor layer are each independently
 * sufficient to block it — removing either one alone still leaves the
 * other standing, so a single end-to-end test cannot tell "both layers are
 * correctly wired" apart from "only one happens to be, and the other is a
 * silent no-op". That is why `VaultApiClientTest` also pins each layer in
 * ISOLATION: one test builds a client with ONLY this interceptor (as a
 * network interceptor) and BOTH redirect flags left at OkHttp's insecure
 * default (`true`), proving the interceptor alone blocks the downgrade;
 * another asserts directly (via `debugHttpClientForTesting`) that the
 * built client's `followRedirects`/`followSslRedirects` are actually
 * `false`, proving the flag layer landed regardless of what the
 * interceptor would have done anyway.
 *
 * See `ConnectivityProfileTest` for the interceptor's OWN pass/block logic
 * pinned via a fake `Interceptor.Chain` (no sockets at all) and
 * `VaultApiClientTest` for the full set: the end-to-end https-to-http and
 * https-to-https(cross-host) redirect pins, the interceptor-alone pin, and
 * the flag-configuration pin.
 */
class CleartextPolicyInterceptor(private val profile: ConnectivityProfile) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val url = chain.request().url
        if (url.scheme == "http" && !profile.allowsCleartext) {
            throw CleartextNotAllowedException(
                "${profile::class.simpleName} refuses cleartext HTTP for $url"
            )
        }
        return chain.proceed(chain.request())
    }
}
