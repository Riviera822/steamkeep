import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSettingsPatch } from "../js/lib/settings-diff.js";

const ENTRIES = [
  { key: "vault_name", effective: "vault-01", source: "default", applies: "restart-required", env_only: false },
  { key: "schedule_window", effective: null, source: "default", applies: "next_sweep", env_only: false },
  { key: "schedule_interval_minutes", effective: 180, source: "default", applies: "next_sweep", env_only: false },
  { key: "auto_gc", effective: "off", source: "env", applies: "immediately", env_only: false },
  { key: "webhook_url", effective: "", source: "default", applies: "restart-required", env_only: false },
  {
    key: "webhook_events",
    effective: ["client.bypass_resolved", "client.bypass_suspected", "job.cancelled", "job.done", "job.error"],
    source: "default",
    applies: "restart-required",
    env_only: false,
  },
  { key: "db_path", effective: "/data/vault.db", source: "env", applies: "restart-required", env_only: true },
];

test("a field never touched by the user is never in the body", () => {
  const body = buildSettingsPatch(ENTRIES, { vault_name: { value: "vault-02" } });
  assert.deepEqual(Object.keys(body), ["vault_name"]);
});

test("MUTATION PIN: a touched field whose value equals the current effective value is dropped, not sent", () => {
  const body = buildSettingsPatch(ENTRIES, {
    vault_name: { value: "vault-01" }, // identical to entries' effective
    schedule_interval_minutes: { value: "180" }, // number vs string, same value
  });
  assert.deepEqual(body, {});
});

test("a genuinely changed value is included, sent as the raw draft value", () => {
  const body = buildSettingsPatch(ENTRIES, { schedule_interval_minutes: { value: "90" } });
  assert.deepEqual(body, { schedule_interval_minutes: "90" });
});

test("reset only clears a key that currently has a db override", () => {
  const body = buildSettingsPatch(ENTRIES, { auto_gc: { reset: true } }); // source: env, no override
  assert.deepEqual(body, {});
});

test("reset on a db-sourced key sends null", () => {
  const overridden = ENTRIES.map((e) => (e.key === "auto_gc" ? { ...e, source: "db" } : e));
  const body = buildSettingsPatch(overridden, { auto_gc: { reset: true } });
  assert.deepEqual(body, { auto_gc: null });
});

test("blank is a real override value for schedule_window/webhook_url, never coerced to reset", () => {
  const body = buildSettingsPatch(ENTRIES, {
    webhook_url: { value: "" }, // already blank -> no-op, dropped
    schedule_window: { value: "22:00-06:00" }, // was null -> real change
  });
  assert.deepEqual(body, { schedule_window: "22:00-06:00" });
});

test("webhook_events: a comma string equal (order-independent, whitespace-tolerant) to the current list is a no-op", () => {
  const body = buildSettingsPatch(ENTRIES, {
    webhook_events: { value: " job.error, job.done ,job.cancelled,client.bypass_suspected,client.bypass_resolved" },
  });
  assert.deepEqual(body, {});
});

test("webhook_events: a real change (one event dropped) is included, sent verbatim as given", () => {
  const body = buildSettingsPatch(ENTRIES, { webhook_events: { value: ["job.done", "job.error"] } });
  assert.deepEqual(body, { webhook_events: ["job.done", "job.error"] });
});

test("an env-only key is dropped defensively even if somehow present in drafts", () => {
  const body = buildSettingsPatch(ENTRIES, { db_path: { value: "/somewhere/else.db" } });
  assert.deepEqual(body, {});
});

test("an unrecognised key is dropped defensively", () => {
  const body = buildSettingsPatch(ENTRIES, { made_up_key: { value: "x" } });
  assert.deepEqual(body, {});
});

test("multiple touched fields: only the ones that actually changed are sent", () => {
  const body = buildSettingsPatch(ENTRIES, {
    vault_name: { value: "vault-01" }, // unchanged
    schedule_interval_minutes: { value: "240" }, // changed
    auto_gc: { value: "dry-run" }, // changed
  });
  assert.deepEqual(body, { schedule_interval_minutes: "240", auto_gc: "dry-run" });
});

test("empty drafts produce an empty body", () => {
  assert.deepEqual(buildSettingsPatch(ENTRIES, {}), {});
});
