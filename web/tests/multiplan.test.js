/**
 * Headless tests for web/js/lib/multiplan.js (WP 4a.3).
 *
 * Scenario mirrors docs/design/vault-app-mockup-NOTES.md round 6's own
 * worked example almost exactly (three games, one depot shared by all
 * three): "Deleting Nebula Drift + Ironwood Hollow keeps shared depot
 * 228990 because Tundra Protocol still holds it; adding Tundra to the same
 * selection frees it." Renamed for this WP's fixtures but the arithmetic
 * (and the reason it matters) is the same.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildMultiPlan } from "../js/lib/multiplan.js";

const GAME_A = 100; // exclusive depot 1001 + shared depot 9000
const GAME_B = 200; // exclusive depot 2001 + shared depot 9000
const GAME_C = 300; // only maps the shared depot 9000
const SHARED_DEPOT = 9000;
const EXCLUSIVE_A = 1001;
const EXCLUSIVE_B = 2001;

function detail(appid, depots) {
  return { appid, depots };
}

function makeGamesByAppid(overrides = {}) {
  const base = {
    [GAME_A]: { appid: GAME_A, status: "done", last_prefill_at: "2026-08-01T00:00:00Z" },
    [GAME_B]: { appid: GAME_B, status: "done", last_prefill_at: "2026-08-01T00:00:00Z" },
    [GAME_C]: { appid: GAME_C, status: "done", last_prefill_at: "2026-08-01T00:00:00Z" },
  };
  const merged = { ...base, ...overrides };
  return new Map(Object.entries(merged).map(([k, v]) => [Number(k), v]));
}

const MAPPING = [
  { depotid: EXCLUSIVE_A, appid: GAME_A },
  { depotid: EXCLUSIVE_B, appid: GAME_B },
  { depotid: SHARED_DEPOT, appid: GAME_A },
  { depotid: SHARED_DEPOT, appid: GAME_B },
  { depotid: SHARED_DEPOT, appid: GAME_C },
];

const DETAILS = [
  detail(GAME_A, [
    { depotid: EXCLUSIVE_A, shared: false, size_bytes: 1_000_000_000 },
    { depotid: SHARED_DEPOT, shared: true, size_bytes: 500_000_000 },
  ]),
  detail(GAME_B, [
    { depotid: EXCLUSIVE_B, shared: false, size_bytes: 2_000_000_000 },
    { depotid: SHARED_DEPOT, shared: true, size_bytes: 500_000_000 },
  ]),
  detail(GAME_C, [{ depotid: SHARED_DEPOT, shared: true, size_bytes: 500_000_000 }]),
];

test("round-6 scenario: deleting A+B keeps the shared depot because C still holds it", () => {
  const plan = buildMultiPlan([GAME_A, GAME_B], {
    details: DETAILS.filter((d) => [GAME_A, GAME_B].includes(d.appid)),
    mapping: MAPPING,
    gamesByAppid: makeGamesByAppid(),
    activeJobAppids: new Set(),
  });
  const sharedRow = plan.rows.find((r) => r.depotid === SHARED_DEPOT);
  assert.equal(sharedRow.free, false, "C is still cached and outside the batch -> kept");
  assert.deepEqual(sharedRow.holderAppids, [GAME_C]);
  assert.equal(plan.freedBytes, 3_000_000_000, "only the two exclusive depots are freed");
  assert.equal(plan.keptBytes, 500_000_000);
});

test("MUTATION TARGET (the real set-dedupe): adding C to the SAME batch frees the shared depot", () => {
  // This is the mutation-kill the WP brief means by "dropping the
  // set-dedupe must kill a named test": `others.filter(appid =>
  // !idSet.has(appid))` in multiplan.js excludes co-owners INSIDE the
  // current batch from counting as protectors. Delete that `!idSet.has`
  // filter and this test fails — C would still be counted as an "other"
  // owner of the shared depot even while C is itself being deleted in the
  // same call, so `sharedRow.free` would wrongly stay `false`.
  const plan = buildMultiPlan([GAME_A, GAME_B, GAME_C], {
    details: DETAILS,
    mapping: MAPPING,
    gamesByAppid: makeGamesByAppid(),
    activeJobAppids: new Set(),
  });
  const sharedRow = plan.rows.find((r) => r.depotid === SHARED_DEPOT);
  assert.equal(sharedRow.free, true, "every co-owner is now inside the batch");
  assert.deepEqual(sharedRow.holderAppids, []);
  assert.equal(plan.freedBytes, 3_500_000_000, "all three depots are freed");
  assert.equal(plan.keptBytes, 0);
});

test("regression pin: the shared depot is counted exactly ONCE across the batch, not per game", () => {
  // A and B BOTH list depot 9000 in their own `details.depots`. Pins the
  // real arithmetic outcome (one row, bytes counted once) — NOT a
  // mutation-kill for the `if (!depotsSeen.has(...))` line itself: that
  // line only breaks a first-vs-last-wins TIE for which game's depot
  // object is kept (see the comment on `depotsSeen` in multiplan.js).
  // `depotsSeen` is a Map keyed by depotid, so it already guarantees at
  // most one entry per depot even without that `if` — this test would
  // stay green if the guard were removed. The genuine "dropping the
  // set-dedupe kills a named test" mutation target is the OTHER
  // exclusion below (`others.filter(appid => !idSet.has(appid))`), pinned
  // by "round-6 scenario: adding C to the SAME batch frees the shared
  // depot" above: remove that filter and co-owners INSIDE the batch would
  // still count as protectors, and that test fails.
  const plan = buildMultiPlan([GAME_A, GAME_B], {
    details: DETAILS.filter((d) => [GAME_A, GAME_B].includes(d.appid)),
    mapping: MAPPING,
    gamesByAppid: makeGamesByAppid(),
    activeJobAppids: new Set(),
  });
  assert.equal(
    plan.rows.filter((r) => r.depotid === SHARED_DEPOT).length,
    1,
    "the shared depot must appear as exactly one row",
  );
  assert.equal(
    plan.occupiedBytes,
    1_000_000_000 /* exclusive A */ + 2_000_000_000 /* exclusive B */ + 500_000_000 /* shared ONCE */,
  );
});

test("a co-owner outside the batch that is currently idle/never-prefilled does NOT protect the depot (last cached remnant)", () => {
  const plan = buildMultiPlan([GAME_A, GAME_B], {
    details: DETAILS.filter((d) => [GAME_A, GAME_B].includes(d.appid)),
    mapping: MAPPING,
    gamesByAppid: makeGamesByAppid({
      [GAME_C]: { appid: GAME_C, status: "idle", last_prefill_at: null },
    }),
    activeJobAppids: new Set(),
  });
  const sharedRow = plan.rows.find((r) => r.depotid === SHARED_DEPOT);
  assert.equal(sharedRow.free, true, "C has never been prefilled and has no job -> not protecting");
  assert.equal(plan.freedBytes, 3_500_000_000);
});

test("a co-owner outside the batch with an ACTIVE job protects the depot even if status is idle", () => {
  const plan = buildMultiPlan([GAME_A, GAME_B], {
    details: DETAILS.filter((d) => [GAME_A, GAME_B].includes(d.appid)),
    mapping: MAPPING,
    gamesByAppid: makeGamesByAppid({
      [GAME_C]: { appid: GAME_C, status: "idle", last_prefill_at: null },
    }),
    activeJobAppids: new Set([GAME_C]),
  });
  const sharedRow = plan.rows.find((r) => r.depotid === SHARED_DEPOT);
  assert.equal(sharedRow.free, false);
  assert.deepEqual(sharedRow.holderAppids, [GAME_C]);
});

test("an unresolvable owner appid (missing from gamesByAppid) fails CLOSED: the depot is protected", () => {
  const gamesByAppid = makeGamesByAppid();
  gamesByAppid.delete(GAME_C); // simulate an unreadable/unknown mapping row owner
  const plan = buildMultiPlan([GAME_A, GAME_B], {
    details: DETAILS.filter((d) => [GAME_A, GAME_B].includes(d.appid)),
    mapping: MAPPING,
    gamesByAppid,
    activeJobAppids: new Set(),
  });
  const sharedRow = plan.rows.find((r) => r.depotid === SHARED_DEPOT);
  assert.equal(sharedRow.free, false, "unknown owner must protect, never resolve to delete");
});

test("no shared depots at all: everything is freed, sharedRows is empty", () => {
  const plan = buildMultiPlan([GAME_A], {
    details: [detail(GAME_A, [{ depotid: EXCLUSIVE_A, shared: false, size_bytes: 1_000_000_000 }])],
    mapping: [{ depotid: EXCLUSIVE_A, appid: GAME_A }],
    gamesByAppid: makeGamesByAppid(),
    activeJobAppids: new Set(),
  });
  assert.deepEqual(plan.sharedRows, []);
  assert.equal(plan.freedBytes, 1_000_000_000);
  assert.equal(plan.keptBytes, 0);
});
