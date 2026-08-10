import { test } from "node:test";
import assert from "node:assert/strict";
import {
  STEP,
  canAdvance,
  nextStep,
  prevStep,
  clampStep,
  progressPercent,
  stepTitle,
  shouldShowOnboarding,
} from "../js/lib/onboarding-steps.js";

test("step 1 blocks advancing until the key has been tested (MUTATION PIN)", () => {
  assert.equal(canAdvance(STEP.CONNECT, { tested: false }), false);
  assert.equal(canAdvance(STEP.CONNECT, {}), false);
  assert.equal(canAdvance(STEP.CONNECT, { tested: true }), true);
  assert.equal(nextStep(STEP.CONNECT, { tested: false }), STEP.CONNECT); // unchanged
  assert.equal(nextStep(STEP.CONNECT, { tested: true }), STEP.STEAM);
});

test("step 2 (Steam identity) is optional — always advanceable", () => {
  assert.equal(canAdvance(STEP.STEAM, {}), true);
  assert.equal(nextStep(STEP.STEAM, {}), STEP.DONE);
});

test("nextStep never exceeds the last step", () => {
  assert.equal(nextStep(STEP.DONE, {}), STEP.DONE);
});

test("prevStep: step 1 has no Back (mockup rule) — clamps, does not go negative", () => {
  assert.equal(prevStep(STEP.CONNECT), STEP.CONNECT);
});

test("prevStep moves back exactly one step and preserves values (caller's job; this only moves the pointer)", () => {
  assert.equal(prevStep(STEP.STEAM), STEP.CONNECT);
  assert.equal(prevStep(STEP.DONE), STEP.STEAM);
});

test("clampStep bounds any input into [1,3]", () => {
  assert.equal(clampStep(0), STEP.CONNECT);
  assert.equal(clampStep(-5), STEP.CONNECT);
  assert.equal(clampStep(99), STEP.DONE);
  assert.equal(clampStep(2), STEP.STEAM);
  assert.equal(clampStep(NaN), STEP.CONNECT);
});

test("progressPercent is monotonically increasing across the three steps", () => {
  const p1 = progressPercent(STEP.CONNECT);
  const p2 = progressPercent(STEP.STEAM);
  const p3 = progressPercent(STEP.DONE);
  assert.ok(p1 < p2 && p2 < p3, `expected ${p1} < ${p2} < ${p3}`);
  assert.equal(p3, 100);
});

test("stepTitle names every step distinctly", () => {
  const titles = new Set([stepTitle(STEP.CONNECT), stepTitle(STEP.STEAM), stepTitle(STEP.DONE)]);
  assert.equal(titles.size, 3);
  for (const t of titles) assert.ok(t.length > 0);
});

test("shouldShowOnboarding: only true with no stored key AND no demo mode (MUTATION PIN)", () => {
  assert.equal(shouldShowOnboarding({ hasApiKey: false, demoMode: false }), true);
  assert.equal(shouldShowOnboarding({ hasApiKey: true, demoMode: false }), false);
  assert.equal(shouldShowOnboarding({ hasApiKey: false, demoMode: true }), false);
  assert.equal(shouldShowOnboarding({ hasApiKey: true, demoMode: true }), false);
});
