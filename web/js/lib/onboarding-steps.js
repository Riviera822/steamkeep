/**
 * Onboarding step machine (WP 4a.6) — pure decision logic for the 3-step
 * first-run flow ported from docs/design/vault-app-mockup.html's onboarding
 * (frozen round 7), adapted to the DECIDED web-UI shape
 * (docs/WORKPACKAGES.md Phase 4a header; ADR-0004 addendum):
 *
 *   Step 1 Connect  — vault name (optional) + vault API key + a real
 *                      server/key check (no server-URL/connection-profile
 *                      fields: the web app is served BY vault-api and the
 *                      WP 4a.1 CSP's `connect-src 'self'` makes any other
 *                      origin unusable, so there is nothing to pick — see
 *                      api.js's module header for where `getServerUrl`/
 *                      `setServerUrl` used to live).
 *   Step 2 Steam    — optional: Steam Web API key + SteamID64 (the relay,
 *                      WP 4a.6r), never Valve OpenID (that is the native
 *                      Android app's device-local path, ADR-0004 decision 2,
 *                      untouched here).
 *   Step 3 Done     — summary, then close.
 *
 * Kept pure (no DOM, no fetch) so the gating rules are testable head-on:
 * step 1 cannot be left until the key has actually been verified against a
 * live server (`tested: true`) — unlike the mockup, which only gated on
 * form completeness, this fork's step 1 result is used for real (the
 * verified key is what onboarding.js stores), so advancing past it on an
 * unverified guess would silently ship a broken API key into localStorage.
 * Step 2 is optional by design (mockup: "Continue without one — you can
 * sign in later under Settings") and step 3 is terminal.
 */

export const STEP = Object.freeze({ CONNECT: 1, STEAM: 2, DONE: 3 });
export const FIRST_STEP = STEP.CONNECT;
export const LAST_STEP = STEP.DONE;

const STEP_TITLES = Object.freeze({
  [STEP.CONNECT]: "Step 1 of 3 · Connect",
  [STEP.STEAM]: "Step 2 of 3 · Steam identity",
  [STEP.DONE]: "Step 3 of 3 · Ready",
});

/** @param {number} step */
export function stepTitle(step) {
  return STEP_TITLES[step] || "";
}

/** @param {number} step @returns {number} 33 | 66 | 100 */
export function progressPercent(step) {
  const clamped = clampStep(step);
  return Math.round((clamped / LAST_STEP) * 100);
}

/** @param {number} step */
export function clampStep(step) {
  if (!Number.isFinite(step)) return FIRST_STEP;
  return Math.min(LAST_STEP, Math.max(FIRST_STEP, Math.trunc(step)));
}

/**
 * Can the flow leave `step` right now?
 * @param {number} step
 * @param {{tested?: boolean}} state
 */
export function canAdvance(step, state) {
  if (step === STEP.CONNECT) return state?.tested === true;
  return true; // Steam identity (step 2) is optional; step 3 has no "next"
}

/**
 * @param {number} step current step
 * @param {{tested?: boolean}} state
 * @returns {number} the step to move to (unchanged if blocked)
 */
export function nextStep(step, state) {
  if (!canAdvance(step, state)) return step;
  return clampStep(step + 1);
}

/** Mockup rule: step 1 has no Back — `prevStep(1, ...)` is a no-op. */
export function prevStep(step) {
  return clampStep(step - 1);
}

/**
 * Should the onboarding overlay be shown at all? First run only: no vault
 * API key stored yet, and the user has not already chosen demo mode
 * (mockup: "Skip for now — browse in demo mode" is itself an onboarding
 * exit, not a state that reopens it).
 * @param {{hasApiKey: boolean, demoMode: boolean}} ctx
 */
export function shouldShowOnboarding({ hasApiKey, demoMode }) {
  return !hasApiKey && !demoMode;
}
