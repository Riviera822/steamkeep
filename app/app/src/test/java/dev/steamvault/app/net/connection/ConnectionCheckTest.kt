package dev.steamvault.app.net.connection

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.HealthOut
import dev.steamvault.app.net.model.SettingsOut
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ConnectionCheckTest {

    private val health = HealthOut(status = "ok")
    private val settings = SettingsOut(readonly = false, settings = emptyList())

    // ---- classifyConnectionFailure -----------------------------------------

    @Test
    fun `a network error on either step classifies as Unreachable`() {
        assertEquals(
            ConnectionFailureReason.Unreachable,
            classifyConnectionFailure(ConnectionCheckStep.HEALTH, VaultApiError.Network("boom")),
        )
        assertEquals(
            ConnectionFailureReason.Unreachable,
            classifyConnectionFailure(ConnectionCheckStep.SETTINGS, VaultApiError.Network("boom")),
        )
    }

    @Test
    fun `a 401 on the settings step classifies as KeyRejected`() {
        val reason = classifyConnectionFailure(ConnectionCheckStep.SETTINGS, VaultApiError.Auth("nope", status = 401))
        assertEquals(ConnectionFailureReason.KeyRejected(401), reason)
    }

    @Test
    fun `MUTATION PIN -- a 401 on the HEALTH step is NOT reported as KeyRejected`() {
        // /v1/health has no auth check at all -- a 401 there (which cannot
        // happen against a real vault-api) must not be mislabeled with the
        // "your key was rejected" meaning that label carries.
        val reason = classifyConnectionFailure(ConnectionCheckStep.HEALTH, VaultApiError.Auth("nope", status = 401))
        assertTrue(reason is ConnectionFailureReason.UnexpectedStatus)
        assertEquals(ConnectionCheckStep.HEALTH, (reason as ConnectionFailureReason.UnexpectedStatus).step)
    }

    @Test
    fun `a 500 on either step classifies as UnexpectedStatus carrying the step and status`() {
        val reason = classifyConnectionFailure(ConnectionCheckStep.SETTINGS, VaultApiError.Server("boom", status = 503))
        assertEquals(ConnectionFailureReason.UnexpectedStatus(ConnectionCheckStep.SETTINGS, 503), reason)
    }

    // ---- checkVaultConnection: ordering + short-circuit --------------------

    @Test
    fun `success calls both steps in order and returns both bodies`() = runTest {
        var healthCalls = 0
        var settingsCalls = 0
        val result = checkVaultConnection(
            health = { healthCalls++; health },
            settings = { settingsCalls++; settings },
        )
        assertTrue(result is ConnectionCheckResult.Success)
        assertEquals(health, (result as ConnectionCheckResult.Success).health)
        assertEquals(settings, result.settings)
        assertEquals(1, healthCalls)
        assertEquals(1, settingsCalls)
    }

    @Test
    fun `MUTATION PIN -- a health failure short-circuits -- settings is never called`() = runTest {
        var settingsCalls = 0
        val result = checkVaultConnection(
            health = { throw VaultApiError.Network("unreachable") },
            settings = { settingsCalls++; settings },
        )
        assertTrue(result is ConnectionCheckResult.Failure)
        assertEquals(ConnectionFailureReason.Unreachable, (result as ConnectionCheckResult.Failure).reason)
        assertEquals(0, settingsCalls)
    }

    @Test
    fun `a settings failure after a successful health reports the SETTINGS-step reason`() = runTest {
        val result = checkVaultConnection(
            health = { health },
            settings = { throw VaultApiError.Auth("nope", status = 401) },
        )
        assertTrue(result is ConnectionCheckResult.Failure)
        assertEquals(ConnectionFailureReason.KeyRejected(401), (result as ConnectionCheckResult.Failure).reason)
    }

    @Test
    fun `a non-VaultApiError exception is not swallowed -- it propagates`() = runTest {
        var threw = false
        try {
            checkVaultConnection(
                health = { throw IllegalStateException("unexpected") },
                settings = { settings },
            )
        } catch (_: IllegalStateException) {
            threw = true
        }
        assertTrue(threw)
    }
}
