/**
 * Headless tests for web/js/errors.js (WP 4a.2 review should-fix #3):
 * classifyHttpStatus covering all six ERROR_KINDS (network is not a status
 * code, so it is exercised as a taxonomy member only; api.js's own
 * fetch-level catch is what actually produces it, out of scope here).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { classifyHttpStatus, ERROR_KINDS } from "../js/errors.js";

test("ERROR_KINDS has exactly six members, including 'network' and 'unknown'", () => {
  assert.deepEqual(Object.values(ERROR_KINDS).sort(), [
    "auth",
    "network",
    "not_found",
    "server",
    "unknown",
    "validation",
  ]);
});

test("401 classifies as auth", () => {
  assert.equal(classifyHttpStatus(401), ERROR_KINDS.AUTH);
});

test("404 classifies as not_found", () => {
  assert.equal(classifyHttpStatus(404), ERROR_KINDS.NOT_FOUND);
});

test("422 classifies as validation", () => {
  assert.equal(classifyHttpStatus(422), ERROR_KINDS.VALIDATION);
});

test("409 (job-control conflict) folds into validation, not a dedicated kind", () => {
  assert.equal(classifyHttpStatus(409), ERROR_KINDS.VALIDATION);
});

test("an undocumented 4xx (e.g. 403, 405) folds into validation, not unknown", () => {
  assert.equal(classifyHttpStatus(403), ERROR_KINDS.VALIDATION);
  assert.equal(classifyHttpStatus(405), ERROR_KINDS.VALIDATION);
});

test("5xx classifies as server", () => {
  assert.equal(classifyHttpStatus(500), ERROR_KINDS.SERVER);
  assert.equal(classifyHttpStatus(503), ERROR_KINDS.SERVER);
});

test("a status below 400 classifies as unknown (should not reach a real caller)", () => {
  assert.equal(classifyHttpStatus(200), ERROR_KINDS.UNKNOWN);
  assert.equal(classifyHttpStatus(301), ERROR_KINDS.UNKNOWN);
});

test("'network' is not produced by classifyHttpStatus (it has no status code)", () => {
  // network is part of the taxonomy but is raised directly by api.js's
  // fetch-level catch, before any status code exists to classify.
  const allPossibleOutputs = [200, 301, 401, 403, 404, 405, 409, 422, 500, 503].map(
    classifyHttpStatus,
  );
  assert.ok(!allPossibleOutputs.includes(ERROR_KINDS.NETWORK));
});
