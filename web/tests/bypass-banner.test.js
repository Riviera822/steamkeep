/**
 * Headless tests for web/js/lib/bypass-banner.js (WP 4a.7 DoD).
 *
 * The pin the WP brief calls out by name: the banner logic must NOT
 * "not-fire" (stay hidden) on the very first `GET /v1/clients` snapshot
 * just because there is no prior snapshot to diff against — that
 * first-poll suppression is the notifications DIFFER's own invariant
 * (web/tests/notifications.test.js), and this module deliberately does not
 * inherit it for banner VISIBILITY (see bypass-banner.js's module header).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { bypassBannerVisible, nextBypassDismissState } from "../js/lib/bypass-banner.js";

function client(overrides) {
  return { client_id: "workshop-pc", bypass_suspected: false, ...overrides };
}

// ---------------------------------------------------------------------
// bypassBannerVisible
// ---------------------------------------------------------------------

test("MUTATION PIN: a client already bypass_suspected on the FIRST ever snapshot shows the banner", () => {
  // No prior state exists at all here — this call itself IS the first
  // sighting. If bypassBannerVisible were (wrongly) implemented against a
  // diff's isFirst flag instead of the raw snapshot, this would return
  // false, and an operator whose DNS was already broken before they ever
  // opened the app would see no warning until something ELSE changed.
  const clients = [client({ bypass_suspected: true })];
  assert.equal(bypassBannerVisible(clients), true);
});

test("bypassBannerVisible: false when nobody is suspected", () => {
  assert.equal(bypassBannerVisible([client(), client({ client_id: "rig-01" })]), false);
});

test("bypassBannerVisible: true when AT LEAST ONE client is suspected among many", () => {
  const clients = [client(), client({ client_id: "demon", bypass_suspected: true }), client({ client_id: "c3" })];
  assert.equal(bypassBannerVisible(clients), true);
});

test("bypassBannerVisible: false/null-safe for empty, null, undefined, non-array input", () => {
  assert.equal(bypassBannerVisible([]), false);
  assert.equal(bypassBannerVisible(null), false);
  assert.equal(bypassBannerVisible(undefined), false);
});

// ---------------------------------------------------------------------
// nextBypassDismissState
// ---------------------------------------------------------------------

test("MUTATION PIN: dismissed stays dismissed across an unrelated poll tick with no bypass transition", () => {
  // If this were (wrongly) reset on every tick regardless of content, the
  // Dismiss button would be pointless — it would re-show itself within one
  // poll interval even though nothing changed.
  assert.equal(nextBypassDismissState(true, []), true);
  assert.equal(nextBypassDismissState(true, [{ type: "job_finished", jobId: 1 }]), true);
  assert.equal(nextBypassDismissState(true, undefined), true);
});

test("MUTATION PIN: a real bypass_suspected transition un-dismisses", () => {
  // If this branch were dropped (dismiss made permanent), an operator who
  // dismissed a warning, fixed nothing, and later had a DIFFERENT client
  // start bypassing would never be told.
  assert.equal(
    nextBypassDismissState(true, [{ type: "bypass_suspected", clientId: "new-offender" }]),
    false,
  );
});

test("a bypass_resolved transition also un-dismisses (the dismiss is about staleness, not direction)", () => {
  assert.equal(
    nextBypassDismissState(true, [{ type: "bypass_resolved", clientId: "workshop-pc" }]),
    false,
  );
});

test("nextBypassDismissState: already-false stays false regardless of events", () => {
  assert.equal(nextBypassDismissState(false, []), false);
  assert.equal(nextBypassDismissState(false, [{ type: "bypass_suspected", clientId: "x" }]), false);
});

test("first poll (differ emits zero bypass_* events) never forces an un-dismiss by itself", () => {
  // The differ's first-poll-silent invariant means the very first tick
  // always hands this function an empty (or bypass-free) event batch —
  // proving no special-casing is needed here for that case.
  assert.equal(nextBypassDismissState(true, []), true);
});
