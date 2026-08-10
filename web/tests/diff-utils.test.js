/**
 * Headless tests for web/js/diff-utils.js (WP 4a.2).
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { diffByKey } from "../js/diff-utils.js";

const byId = (x) => x.id;

test("first poll (prevList undefined): everything is 'added', isFirst true", () => {
  const curr = [{ id: 1, v: "a" }, { id: 2, v: "b" }];
  const diff = diffByKey(undefined, curr, byId);
  assert.equal(diff.isFirst, true);
  assert.deepEqual(diff.added, curr);
  assert.deepEqual(diff.updated, []);
  assert.deepEqual(diff.removed, []);
  assert.deepEqual(diff.unchanged, []);
});

test("first poll via null behaves the same as undefined", () => {
  const curr = [{ id: 1, v: "a" }];
  const diff = diffByKey(null, curr, byId);
  assert.equal(diff.isFirst, true);
  assert.deepEqual(diff.added, curr);
});

test("empty-to-empty is NOT a first poll (prevList = [] is a real, empty snapshot)", () => {
  const diff = diffByKey([], [], byId);
  assert.equal(diff.isFirst, false);
  assert.deepEqual(diff.added, []);
  assert.deepEqual(diff.updated, []);
  assert.deepEqual(diff.removed, []);
  assert.deepEqual(diff.unchanged, []);
});

test("no-op poll: identical content across two separate array instances is 'unchanged'", () => {
  const prev = [{ id: 1, v: "a" }, { id: 2, v: "b" }];
  const curr = [{ id: 1, v: "a" }, { id: 2, v: "b" }]; // structurally equal, different objects
  const diff = diffByKey(prev, curr, byId);
  assert.equal(diff.isFirst, false);
  assert.deepEqual(diff.added, []);
  assert.deepEqual(diff.updated, []);
  assert.deepEqual(diff.removed, []);
  assert.equal(diff.unchanged.length, 2);
});

test("added / updated / removed all detected in one diff", () => {
  const prev = [{ id: 1, v: "a" }, { id: 2, v: "b" }];
  const curr = [{ id: 2, v: "c" }, { id: 3, v: "d" }];
  const diff = diffByKey(prev, curr, byId);
  assert.deepEqual(diff.added, [{ id: 3, v: "d" }]);
  assert.deepEqual(diff.removed, [{ id: 1, v: "a" }]);
  assert.deepEqual(diff.updated, [{ prev: { id: 2, v: "b" }, curr: { id: 2, v: "c" } }]);
  assert.deepEqual(diff.unchanged, []);
});

test("a null/undefined currList is treated as an empty snapshot, not a crash", () => {
  const prev = [{ id: 1, v: "a" }];
  const diff = diffByKey(prev, undefined, byId);
  assert.equal(diff.isFirst, false);
  assert.deepEqual(diff.removed, [{ id: 1, v: "a" }]);
  assert.deepEqual(diff.added, []);
});

test("keyFn works for non-numeric keys (e.g. client_id strings)", () => {
  const byClientId = (c) => c.client_id;
  const prev = [{ client_id: "a", bypass_suspected: false }];
  const curr = [{ client_id: "a", bypass_suspected: true }];
  const diff = diffByKey(prev, curr, byClientId);
  assert.equal(diff.updated.length, 1);
  assert.equal(diff.updated[0].curr.bypass_suspected, true);
});
