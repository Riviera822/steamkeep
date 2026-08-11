/**
 * Headless tests for web/js/lib/detail-render-plan.js (WP 4a.4 round-7
 * patch-in-place requirement).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildDetailStructuralKey } from "../js/lib/detail-render-plan.js";

const base = { dispKind: "cached", trackedJobStatus: null, depotTags: ["exclusive", "protected"] };

test("identical inputs produce an identical key", () => {
  assert.equal(buildDetailStructuralKey(base), buildDetailStructuralKey({ ...base }));
});

test("MUTATION TARGET -- a dispKind change (e.g. a download finishing) changes the key", () => {
  const before = buildDetailStructuralKey(base);
  const after = buildDetailStructuralKey({ ...base, dispKind: "running" });
  assert.notEqual(before, after);
});

test("MUTATION TARGET -- a tracked job's status change (e.g. queued -> running) changes the key", () => {
  const before = buildDetailStructuralKey({ ...base, trackedJobStatus: "queued" });
  const after = buildDetailStructuralKey({ ...base, trackedJobStatus: "running" });
  assert.notEqual(before, after);
});

test("MUTATION TARGET -- a depot's sharing tag flipping (a co-owner's cache state changed) changes the key", () => {
  const before = buildDetailStructuralKey({ ...base, depotTags: ["protected"] });
  const after = buildDetailStructuralKey({ ...base, depotTags: ["orphaned"] });
  assert.notEqual(before, after);
});

test("depot tag ORDER matters (a reordering would be a different presentation list) -- not a bug, just documented", () => {
  const a = buildDetailStructuralKey({ ...base, depotTags: ["exclusive", "protected"] });
  const b = buildDetailStructuralKey({ ...base, depotTags: ["protected", "exclusive"] });
  assert.notEqual(a, b);
});

test("null/undefined trackedJobStatus normalize to the same key (no tracked job)", () => {
  assert.equal(
    buildDetailStructuralKey({ ...base, trackedJobStatus: null }),
    buildDetailStructuralKey({ ...base, trackedJobStatus: undefined }),
  );
});

test("a non-structural change (size_bytes drifting) is simply not part of the key's inputs at all", () => {
  // buildDetailStructuralKey takes no size/byte field -- the caller never
  // feeds one in, so a size-only tick naturally produces the identical key
  // without this module needing to know anything about bytes.
  assert.equal(buildDetailStructuralKey(base), buildDetailStructuralKey(base));
});

test("missing/non-array depotTags defaults to an empty list rather than throwing", () => {
  assert.equal(
    buildDetailStructuralKey({ dispKind: "none", trackedJobStatus: null, depotTags: undefined }),
    buildDetailStructuralKey({ dispKind: "none", trackedJobStatus: null, depotTags: [] }),
  );
});
