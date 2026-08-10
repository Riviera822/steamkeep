/**
 * Headless tests for web/js/lib/render-plan.js (WP 4a.3 review fix,
 * blocker B1 — the round-7 patch-in-place decision for the GAMES poll).
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { planGamesUpdate } from "../js/lib/render-plan.js";

const g = (appid, extra) => ({ appid, name: `Game ${appid}`, ...extra });

// A trivial computeKey stand-in: the caller normally wires this to
// game-card.js's cardStructuralKey (dispKind); tests only need SOMETHING
// that can differ or stay the same between two fixture objects.
const keyOf = (game) => game.kind;

test("first poll (isFirst): always a full render, nothing painted yet to patch/rebuild", () => {
  const plan = planGamesUpdate({ isFirst: true, added: [], updated: [], removed: [] }, new Map(), keyOf);
  assert.deepEqual(plan, { full: true, patch: [], rebuild: [] });
});

test("a null/undefined diff is treated as a full render (defensive)", () => {
  assert.deepEqual(planGamesUpdate(null, new Map(), keyOf), { full: true, patch: [], rebuild: [] });
  assert.deepEqual(planGamesUpdate(undefined, new Map(), keyOf), { full: true, patch: [], rebuild: [] });
});

test("added rows -> full render (grid membership may change)", () => {
  const diff = { isFirst: false, added: [g(1, { kind: "none" })], updated: [], removed: [] };
  const plan = planGamesUpdate(diff, new Map([[1, "none"]]), keyOf);
  assert.equal(plan.full, true);
});

test("removed rows -> full render", () => {
  const diff = { isFirst: false, added: [], updated: [], removed: [g(1, { kind: "none" })] };
  const plan = planGamesUpdate(diff, new Map(), keyOf);
  assert.equal(plan.full, true);
});

test("no added/removed, no updated -> full:false, empty patch and rebuild", () => {
  const diff = { isFirst: false, added: [], updated: [], removed: [] };
  const plan = planGamesUpdate(diff, new Map([[1, "cached"]]), keyOf);
  assert.deepEqual(plan, { full: false, patch: [], rebuild: [] });
});

test("an updated row not currently on screen (filtered out) is skipped entirely", () => {
  const diff = {
    isFirst: false,
    added: [],
    removed: [],
    updated: [{ prev: g(1, { kind: "cached" }), curr: g(1, { kind: "running" }) }],
  };
  // currentCardKeys has no entry for appid 1 -> it isn't rendered right now
  const plan = planGamesUpdate(diff, new Map(), keyOf);
  assert.deepEqual(plan, { full: false, patch: [], rebuild: [] });
});

// ---------------------------------------------------------------------
// MUTATION TARGET 1: a structural change (painted key != new key) must be
// classified as REBUILD, never patch. If this branch were flipped
// (structural changes patched instead of rebuilt), this test dies —
// the animated node would silently keep the WRONG glyph shape forever.
// ---------------------------------------------------------------------
test("MUTATION TARGET: a game whose structural key CHANGED lands in rebuild, not patch", () => {
  const diff = {
    isFirst: false,
    added: [],
    removed: [],
    updated: [{ prev: g(1, { kind: "none" }), curr: g(1, { kind: "running" }) }],
  };
  const plan = planGamesUpdate(diff, new Map([[1, "none"]]), keyOf);
  assert.deepEqual(plan.rebuild, [1]);
  assert.deepEqual(plan.patch, []);
});

// ---------------------------------------------------------------------
// MUTATION TARGET 2: an update whose structural key is UNCHANGED (e.g.
// only `size_bytes` drifted while a download runs) must be classified as
// PATCH, never rebuild. If this were flipped (any update -> rebuild,
// ignoring the structural-key comparison), this test dies — that is
// EXACTLY the bug this module exists to prevent: rebuilding, and thereby
// restarting the CSS animation of, a card whose displayed state never
// changed.
// ---------------------------------------------------------------------
test("MUTATION TARGET: a game whose structural key is UNCHANGED (pure size drift) lands in patch, not rebuild", () => {
  const diff = {
    isFirst: false,
    added: [],
    removed: [],
    updated: [
      {
        prev: g(1, { kind: "running", size_bytes: 100 }),
        curr: g(1, { kind: "running", size_bytes: 150 }), // size drifted; kind did not
      },
    ],
  };
  const plan = planGamesUpdate(diff, new Map([[1, "running"]]), keyOf);
  assert.deepEqual(plan.patch, [1]);
  assert.deepEqual(plan.rebuild, []);
});

test("a mixed batch: independently classifies each updated appid", () => {
  const diff = {
    isFirst: false,
    added: [],
    removed: [],
    updated: [
      { prev: g(1, { kind: "cached" }), curr: g(1, { kind: "cached" }) }, // unchanged -> patch
      { prev: g(2, { kind: "none" }), curr: g(2, { kind: "running" }) }, // changed -> rebuild
      { prev: g(3, { kind: "cached" }), curr: g(3, { kind: "cached" }) }, // not on screen -> skipped
    ],
  };
  const currentCardKeys = new Map([
    [1, "cached"],
    [2, "none"],
    // appid 3 intentionally absent (filtered out of the current view)
  ]);
  const plan = planGamesUpdate(diff, currentCardKeys, keyOf);
  assert.deepEqual(plan.full, false);
  assert.deepEqual(plan.patch, [1]);
  assert.deepEqual(plan.rebuild, [2]);
});
