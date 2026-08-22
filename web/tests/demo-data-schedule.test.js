import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { demoRequest, resetDemoData } from "../js/demo-data.js";

beforeEach(() => {
  resetDemoData();
});

test("GET /v1/schedule returns the ScheduleOut shape, including the WP 4d fields", async () => {
  const out = await demoRequest("GET", "/v1/schedule");
  for (const key of [
    "enabled",
    "window",
    "overnight",
    "interval_minutes",
    "client_stale_days",
    "server_timezone",
    "last_sweep_at",
    "last_sweep_targets",
    "last_sweep_enqueued",
    "next_eligible_at",
    "sweep_include_cached",
    "sweep_cached_gc_risk",
  ]) {
    assert.ok(key in out, `missing field ${key}`);
  }
});

test("sweep_include_cached on GET /v1/schedule mirrors the live settings override, not a fixed snapshot", async () => {
  const before = await demoRequest("GET", "/v1/schedule");
  assert.equal(before.sweep_include_cached, true); // ADR-0014 default

  await demoRequest("PATCH", "/v1/settings", { body: { sweep_include_cached: "false" } });
  const after = await demoRequest("GET", "/v1/schedule");
  assert.equal(after.sweep_include_cached, false);
});

// MUTATION TARGET: sweep_cached_gc_risk = sweep_include_cached AND auto_gc
// != "execute" (vault_api/scheduler.py::cached_sweep_gc_risk). Every
// combination below is asserted explicitly, including the two that make
// "dry-run" a risky mode too (it reports without reclaiming).
test("sweep_cached_gc_risk follows the exact same formula as scheduler.cached_sweep_gc_risk", async () => {
  const cases = [
    { sweepIncludeCached: "false", autoGc: "off", expected: false },
    { sweepIncludeCached: "false", autoGc: "dry-run", expected: false },
    { sweepIncludeCached: "false", autoGc: "execute", expected: false },
    { sweepIncludeCached: "true", autoGc: "off", expected: true },
    { sweepIncludeCached: "true", autoGc: "dry-run", expected: true },
    { sweepIncludeCached: "true", autoGc: "execute", expected: false },
  ];
  for (const { sweepIncludeCached, autoGc, expected } of cases) {
    await demoRequest("PATCH", "/v1/settings", {
      body: { sweep_include_cached: sweepIncludeCached, auto_gc: autoGc },
    });
    const out = await demoRequest("GET", "/v1/schedule");
    assert.equal(
      out.sweep_cached_gc_risk,
      expected,
      `sweep_include_cached=${sweepIncludeCached} auto_gc=${autoGc} should give risk=${expected}`,
    );
  }
});

// Named mutation, verified (review round 1 nitpick — the previous wording
// named the WRONG mutation: `autoGc !== "off"` leaves this test green too,
// since "dry-run" !== "off" is also true, giving the same risk=true. The
// mutation that actually kills this test, confirmed by reverting the fix
// and watching it fail, is checking EQUALITY to "off" instead of
// INEQUALITY to "execute" (`autoGc === "off"` in place of
// `autoGc !== "execute"`) — that formula gives risk=false for dry-run
// ("dry-run" !== "off" is true, so === "off" is false), which is exactly
// the bug this test exists to catch: a version of the formula that only
// treats the OFF mode as risky and silently lets dry-run through clean.
test("MUTATION PIN: dry-run counts as risky too, not only 'off' — kills a formula that checks 'autoGc === off' instead of 'autoGc !== execute'", async () => {
  await demoRequest("PATCH", "/v1/settings", {
    body: { sweep_include_cached: "true", auto_gc: "dry-run" },
  });
  const out = await demoRequest("GET", "/v1/schedule");
  assert.equal(out.sweep_cached_gc_risk, true);
});

test("last_sweep_targets/last_sweep_enqueued/last_sweep_at are present and internally consistent (targets > 0 with a real timestamp)", async () => {
  const out = await demoRequest("GET", "/v1/schedule");
  assert.equal(typeof out.last_sweep_targets, "number");
  assert.ok(out.last_sweep_targets > 0);
  assert.equal(typeof out.last_sweep_enqueued, "number");
  assert.equal(typeof out.last_sweep_at, "string");
  assert.ok(!Number.isNaN(new Date(out.last_sweep_at).getTime()));
});

test("resetDemoData() does not change the reported sweep_include_cached/sweep_cached_gc_risk from their ADR-0014 defaults", async () => {
  await demoRequest("PATCH", "/v1/settings", { body: { sweep_include_cached: "false" } });
  resetDemoData();
  const out = await demoRequest("GET", "/v1/schedule");
  assert.equal(out.sweep_include_cached, true);
  assert.equal(out.sweep_cached_gc_risk, false); // true AND auto_gc==="execute" (ADR-0014 default) -> false
});
