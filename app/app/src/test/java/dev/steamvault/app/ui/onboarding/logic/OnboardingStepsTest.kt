package dev.steamvault.app.ui.onboarding.logic

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OnboardingStepsTest {

    // ---- onboardingProgressPercent -----------------------------------------

    @Test
    fun `progress percent is 33 66 100 for the three steps`() {
        assertEquals(33, onboardingProgressPercent(OnboardingStep.CONNECT))
        assertEquals(67, onboardingProgressPercent(OnboardingStep.STEAM))
        assertEquals(100, onboardingProgressPercent(OnboardingStep.DONE))
    }

    // ---- canAdvanceOnboardingStep -------------------------------------------

    @Test
    fun `step 1 (Connect) cannot be left until tested is true`() {
        assertFalse(canAdvanceOnboardingStep(OnboardingStep.CONNECT, tested = false))
        assertTrue(canAdvanceOnboardingStep(OnboardingStep.CONNECT, tested = true))
    }

    @Test
    fun `step 2 (Steam) is always advanceable -- optional by design`() {
        assertTrue(canAdvanceOnboardingStep(OnboardingStep.STEAM, tested = false))
        assertTrue(canAdvanceOnboardingStep(OnboardingStep.STEAM, tested = true))
    }

    @Test
    fun `step 3 (Done) is always advanceable -- terminal, nextOnboardingStep no-ops there anyway`() {
        assertTrue(canAdvanceOnboardingStep(OnboardingStep.DONE, tested = false))
    }

    // ---- nextOnboardingStep --------------------------------------------------

    @Test
    fun `MUTATION PIN -- next refuses to leave step 1 when not tested`() {
        assertEquals(OnboardingStep.CONNECT, nextOnboardingStep(OnboardingStep.CONNECT, tested = false))
    }

    @Test
    fun `next advances from Connect to Steam once tested`() {
        assertEquals(OnboardingStep.STEAM, nextOnboardingStep(OnboardingStep.CONNECT, tested = true))
    }

    @Test
    fun `next advances from Steam to Done unconditionally`() {
        assertEquals(OnboardingStep.DONE, nextOnboardingStep(OnboardingStep.STEAM, tested = false))
    }

    @Test
    fun `next is a no-op past the last step`() {
        assertEquals(OnboardingStep.DONE, nextOnboardingStep(OnboardingStep.DONE, tested = true))
    }

    // ---- previousOnboardingStep -----------------------------------------------

    @Test
    fun `MUTATION PIN -- previous is a no-op on step 1 -- no Back from Connect`() {
        assertEquals(OnboardingStep.CONNECT, previousOnboardingStep(OnboardingStep.CONNECT))
    }

    @Test
    fun `previous steps back from Done to Steam and from Steam to Connect`() {
        assertEquals(OnboardingStep.STEAM, previousOnboardingStep(OnboardingStep.DONE))
        assertEquals(OnboardingStep.CONNECT, previousOnboardingStep(OnboardingStep.STEAM))
    }

    // ---- shouldShowOnboarding ------------------------------------------------

    @Test
    fun `MUTATION PIN -- onboarding shows exactly when there is no vault connection and demo mode is off`() {
        assertTrue(shouldShowOnboarding(hasVaultConnection = false, demoMode = false))
        assertFalse(shouldShowOnboarding(hasVaultConnection = true, demoMode = false))
    }

    /** WP APP-DEMO: demo mode is itself an onboarding exit -- a fresh
     * process that entered demo mode last time (were this ever persisted)
     * must not reopen onboarding just because there is still no real vault
     * connection. Mirrors the web port's own `demoMode` half of
     * `shouldShowOnboarding`. */
    @Test
    fun `MUTATION PIN -- demo mode alone suppresses onboarding even with no vault connection`() {
        assertFalse(shouldShowOnboarding(hasVaultConnection = false, demoMode = true))
    }

    /** A real connection always wins even if a stale demo flag were still
     * set -- onboarding correctly stays hidden either way, but the more
     * interesting direction (WP brief constraint 4, "leaving demo mode
     * must be clean") is pinned at the `MainActivity` wiring level: finishing
     * onboarding with a real connection always clears demo mode as part of
     * the same rebuild, so the two flags are never BOTH true in practice. */
    @Test
    fun `MUTATION PIN -- a real vault connection suppresses onboarding regardless of the demo flag`() {
        assertFalse(shouldShowOnboarding(hasVaultConnection = true, demoMode = true))
        assertFalse(shouldShowOnboarding(hasVaultConnection = true, demoMode = false))
    }
}
