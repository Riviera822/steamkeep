/**
 * Headless tests for web/js/lib/clients-render-plan.js (WP 4a.7 DoD).
 *
 * Same shape as render-plan.test.js / downloads-render-plan.test.js.
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { planClientsUpdate } from "../js/lib/clients-render-plan.js";

function client(overrides) {
  return { client_id: "workshop-pc", bypass_suspected: false, cache_hits: 0, cache_misses: 0, ...overrides };
}

test("first poll (isFirst / missing diff) always means a full render", () => {
  assert.deepEqual(planClientsUpdate(undefined), { full: true, patch: [], rebuild: [] });
  assert.deepEqual(planClientsUpdate(null), { full: true, patch: [], rebuild: [] });
  assert.deepEqual(planClientsUpdate({ isFirst: true, added: [], updated: [], removed: [] }), {
    full: true,
    patch: [],
    rebuild: [],
  });
});

test("any added or removed client forces a full render", () => {
  assert.deepEqual(
    planClientsUpdate({ isFirst: false, added: [client()], updated: [], removed: [] }),
    { full: true, patch: [], rebuild: [] },
  );
  assert.deepEqual(
    planClientsUpdate({ isFirst: false, added: [], updated: [], removed: [client()] }),
    { full: true, patch: [], rebuild: [] },
  );
});

test("MUTATION TARGET: a bypass_suspected flip lands in rebuild, not patch", () => {
  const prev = client({ bypass_suspected: false });
  const curr = client({ bypass_suspected: true });
  const plan = planClientsUpdate({ isFirst: false, added: [], removed: [], updated: [{ prev, curr }] });
  assert.deepEqual(plan, { full: false, patch: [], rebuild: ["workshop-pc"] });
});

test("MUTATION TARGET: a bypass_resolved flip (true -> false) also lands in rebuild", () => {
  const prev = client({ bypass_suspected: true });
  const curr = client({ bypass_suspected: false });
  const plan = planClientsUpdate({ isFirst: false, added: [], removed: [], updated: [{ prev, curr }] });
  assert.deepEqual(plan, { full: false, patch: [], rebuild: ["workshop-pc"] });
});

test("a stats-only change with bypass_suspected unchanged lands in patch, never rebuild", () => {
  const prev = client({ bypass_suspected: false, cache_hits: 5 });
  const curr = client({ bypass_suspected: false, cache_hits: 6 });
  const plan = planClientsUpdate({ isFirst: false, added: [], removed: [], updated: [{ prev, curr }] });
  assert.deepEqual(plan, { full: false, patch: ["workshop-pc"], rebuild: [] });
});

test("a mixed batch: one flip + one stats-only change resolve independently", () => {
  const flipPrev = client({ client_id: "a", bypass_suspected: false });
  const flipCurr = client({ client_id: "a", bypass_suspected: true });
  const statsPrev = client({ client_id: "b", bypass_suspected: false, bytes_served: 1 });
  const statsCurr = client({ client_id: "b", bypass_suspected: false, bytes_served: 2 });
  const plan = planClientsUpdate({
    isFirst: false,
    added: [],
    removed: [],
    updated: [
      { prev: flipPrev, curr: flipCurr },
      { prev: statsPrev, curr: statsCurr },
    ],
  });
  assert.deepEqual(plan, { full: false, patch: ["b"], rebuild: ["a"] });
});

test("no updates at all -> full: false with both lists empty", () => {
  assert.deepEqual(planClientsUpdate({ isFirst: false, added: [], removed: [], updated: [] }), {
    full: false,
    patch: [],
    rebuild: [],
  });
});
