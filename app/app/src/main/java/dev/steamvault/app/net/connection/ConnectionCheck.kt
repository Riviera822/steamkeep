package dev.steamvault.app.net.connection

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.HealthOut
import dev.steamvault.app.net.model.SettingsOut

/**
 * The onboarding "Test connection" state machine (WP 4b.7 brief), a direct
 * port of `web/js/api.js::checkVaultApiKey`'s TWO-STEP reasoning: `GET
 * /v1/health` answers with no auth at all (api/README.md "Auth" --
 * [dev.steamvault.app.net.VaultApiClient]'s kdoc notes this app attaches
 * `X-Api-Key` to it anyway, same as the web client, but the SERVER never
 * checks it there), so a successful health call proves only that a server
 * is reachable -- NOT that the API key is correct. Only the second,
 * authenticated call ([ConnectionCheckStep.SETTINGS], chosen for the exact
 * reason the web client picks `/v1/settings`: onboarding needs its response
 * anyway, no wasted request) actually proves the key.
 *
 * Deliberately takes two suspend LAMBDAS rather than a
 * [dev.steamvault.app.net.VaultApiClient] directly: `VaultApiClient` is a
 * concrete OkHttp-backed class, and threading a real client through this
 * function would force every test onto MockWebServer. A caller (production:
 * [dev.steamvault.app.ui.onboarding.OnboardingController]) supplies
 * `{ client.health() }` / `{ client.settings() }`; a test supplies a plain
 * fake that throws a chosen [VaultApiError] subtype -- proving the STEP
 * ORDERING and short-circuit behaviour (the settings lambda must never run
 * if the health lambda already failed) without any network stack at all.
 */
enum class ConnectionCheckStep { HEALTH, SETTINGS }

/**
 * Why a step failed, independent of user-facing WORDING (which lives in
 * `ui/onboarding/OnboardingStrings.kt`'s string-resource mapping, per
 * app/README.md's "String resources" convention -- this enum is the pure,
 * testable half; the resource lookup is the Android-framework half).
 */
sealed class ConnectionFailureReason {
    /** No response reached this app at all -- [VaultApiError.Network], on
     * EITHER step. Indistinguishable from the user's point of view whether
     * it failed on health or on settings: either way, nothing was reached. */
    object Unreachable : ConnectionFailureReason()

    /**
     * The authenticated call ([ConnectionCheckStep.SETTINGS] only) came
     * back `401` -- the one outcome [ConnectionCheckStep.HEALTH] can NEVER
     * produce (it is not auth-checked at all), which is exactly why this
     * reason is scoped to the settings step in [classifyConnectionFailure]
     * below rather than being a generic "auth error" case.
     */
    data class KeyRejected(val status: Int) : ConnectionFailureReason()

    /** Anything else non-2xx (validation/server/unknown) on either step. */
    data class UnexpectedStatus(val step: ConnectionCheckStep, val status: Int?) : ConnectionFailureReason()
}

/**
 * Pure classification: mirrors web's `checkVaultApiKey` distinguishing a
 * `401` on the AUTHENTICATED call ("That API key was rejected.") from every
 * other non-2xx response ("The server answered N."). A `401` on the HEALTH
 * step cannot happen against a real vault-api (that endpoint has no auth
 * check to fail), but if it somehow did, it is treated as an unexpected
 * status rather than silently reused as [ConnectionFailureReason.KeyRejected]
 * -- that label's entire meaning is "the SETTINGS call rejected this key".
 */
fun classifyConnectionFailure(step: ConnectionCheckStep, error: VaultApiError): ConnectionFailureReason = when {
    error is VaultApiError.Network -> ConnectionFailureReason.Unreachable
    step == ConnectionCheckStep.SETTINGS && error is VaultApiError.Auth ->
        ConnectionFailureReason.KeyRejected(error.status ?: 401)
    else -> ConnectionFailureReason.UnexpectedStatus(step, error.status)
}

/** Outcome of [checkVaultConnection]. */
sealed class ConnectionCheckResult {
    data class Success(val health: HealthOut, val settings: SettingsOut) : ConnectionCheckResult()
    data class Failure(val reason: ConnectionFailureReason) : ConnectionCheckResult()
}

/**
 * Runs the two-step check in order, stopping at the first failure --
 * [settings] is NEVER invoked if [health] already threw. See this file's
 * header for why both are suspend lambdas rather than a concrete client.
 */
suspend fun checkVaultConnection(
    health: suspend () -> HealthOut,
    settings: suspend () -> SettingsOut,
): ConnectionCheckResult {
    val healthOut = try {
        health()
    } catch (e: VaultApiError) {
        return ConnectionCheckResult.Failure(classifyConnectionFailure(ConnectionCheckStep.HEALTH, e))
    }
    val settingsOut = try {
        settings()
    } catch (e: VaultApiError) {
        return ConnectionCheckResult.Failure(classifyConnectionFailure(ConnectionCheckStep.SETTINGS, e))
    }
    return ConnectionCheckResult.Success(healthOut, settingsOut)
}
