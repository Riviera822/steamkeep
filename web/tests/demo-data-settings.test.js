import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { demoRequest, resetDemoData } from "../js/demo-data.js";

beforeEach(() => {
  resetDemoData();
});

test("GET /v1/settings returns every overridable key plus env-only informational rows", async () => {
  const out = await demoRequest("GET", "/v1/settings");
  assert.equal(out.readonly, false);
  const keys = out.settings.map((s) => s.key);
  for (const k of [
    "vault_name",
    "schedule_window",
    "schedule_interval_minutes",
    "schedule_client_stale_days",
    "auto_gc",
    "webhook_url",
    "webhook_events",
  ]) {
    assert.ok(keys.includes(k), `missing overridable key ${k}`);
  }
  assert.ok(keys.includes("db_path"), "missing env-only informational row");
  const dbPath = out.settings.find((s) => s.key === "db_path");
  assert.equal(dbPath.env_only, true);
});

// WP 4e.6 rail foot / WP 4e.7 (api/) — server_version is a SIBLING of
// readonly on the response object, never a row inside `settings`, matching
// the real endpoint's shape (it has no source precedence and PATCH rejects
// it as an unrecognised key — nothing in demo mode's coercePatchValue
// accepts it either, by omission).
test("GET /v1/settings carries server_version as a top-level string sibling of readonly, not a settings row", async () => {
  const out = await demoRequest("GET", "/v1/settings");
  assert.equal(typeof out.server_version, "string");
  assert.ok(out.server_version.length > 0);
  assert.equal(out.settings.some((s) => s.key === "server_version"), false, "server_version must not appear as a settings row");
});

test("a freshly reset demo has no db-sourced overrides", async () => {
  const out = await demoRequest("GET", "/v1/settings");
  for (const entry of out.settings.filter((s) => !s.env_only)) {
    assert.notEqual(entry.source, "db");
  }
});

test("PATCH sets an override, and GET reflects it with source 'db'", async () => {
  const patched = await demoRequest("PATCH", "/v1/settings", { body: { vault_name: "my-vault" } });
  const entry = patched.settings.find((s) => s.key === "vault_name");
  assert.equal(entry.effective, "my-vault");
  assert.equal(entry.source, "db");
});

test("PATCH with null clears an override back to env/default", async () => {
  await demoRequest("PATCH", "/v1/settings", { body: { auto_gc: "dry-run" } });
  const cleared = await demoRequest("PATCH", "/v1/settings", { body: { auto_gc: null } });
  const entry = cleared.settings.find((s) => s.key === "auto_gc");
  assert.notEqual(entry.source, "db");
  assert.equal(entry.effective, "off"); // SETTINGS_BASE.auto_gc.env
});

test("PATCH validates the WHOLE body before writing anything (all-or-nothing)", async () => {
  await assert.rejects(
    () =>
      demoRequest("PATCH", "/v1/settings", {
        body: { vault_name: "should-not-stick", auto_gc: "not-a-real-mode" },
      }),
    (err) => err.status === 422,
  );
  const after = await demoRequest("GET", "/v1/settings");
  const vaultName = after.settings.find((s) => s.key === "vault_name");
  assert.notEqual(vaultName.effective, "should-not-stick");
});

test("PATCH rejects an env-only key by name, distinct from 'unknown key'", async () => {
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { db_path: "/somewhere/else.db" } }),
    (err) => err.status === 422 && /environment-only/.test(err.detail),
  );
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { not_a_real_setting: "x" } }),
    (err) => err.status === 422 && /not a recognised setting/.test(err.detail),
  );
});

test("PATCH rejects a boolean value explicitly (Pydantic lax-mode trap, LEARNINGS Parsers)", async () => {
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { auto_gc: true } }),
    (err) => err.status === 422,
  );
});

test("webhook_events accepts a JSON array, joined with commas before validation", async () => {
  const out = await demoRequest("PATCH", "/v1/settings", {
    body: { webhook_events: ["job.done", "job.error"] },
  });
  const entry = out.settings.find((s) => s.key === "webhook_events");
  assert.deepEqual([...entry.effective].sort(), ["job.done", "job.error"]);
});

test("webhook_events rejects an unknown event name", async () => {
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { webhook_events: "job.done,not.a.real.event" } }),
    (err) => err.status === 422,
  );
});

// ---------------------------------------------------------------------
// Steam Web API relay (/v1/steam/*)
// ---------------------------------------------------------------------

const VALID_KEY = "0123456789ABCDEF0123456789ABCDEF";
const VALID_STEAMID = "76561198042117903";

test("GET /v1/steam/key starts unconfigured", async () => {
  const out = await demoRequest("GET", "/v1/steam/key");
  assert.deepEqual(out, { configured: false, key_last4: null });
});

test("PUT /v1/steam/key configures it and returns only the last 4 characters", async () => {
  const out = await demoRequest("PUT", "/v1/steam/key", { body: { key: VALID_KEY } });
  assert.equal(out.configured, true);
  assert.equal(out.key_last4, "CDEF");
  assert.equal(JSON.stringify(out).includes(VALID_KEY), false);
});

test("PUT /v1/steam/key rejects a malformed key", async () => {
  await assert.rejects(
    () => demoRequest("PUT", "/v1/steam/key", { body: { key: "too-short" } }),
    (err) => err.status === 422,
  );
});

test("DELETE /v1/steam/key clears configuration", async () => {
  await demoRequest("PUT", "/v1/steam/key", { body: { key: VALID_KEY } });
  const out = await demoRequest("DELETE", "/v1/steam/key");
  assert.equal(out, null);
  const status = await demoRequest("GET", "/v1/steam/key");
  assert.deepEqual(status, { configured: false, key_last4: null });
});

test("GET /v1/steam/owned-games answers 409 while unconfigured", async () => {
  await assert.rejects(
    () => demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: VALID_STEAMID } }),
    (err) => err.status === 409,
  );
});

test("MUTATION PIN: 409-unconfigured is checked even with a syntactically valid steamid", async () => {
  // If the configured-check were ever dropped, this would 200 instead.
  await assert.rejects(
    () => demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: VALID_STEAMID } }),
    (err) => err.status === 409,
  );
});

test("GET /v1/steam/owned-games answers 200 with a game list once configured", async () => {
  // WP 4h.2 fix (baseline-test edit, named per the brief): this used to
  // assert `"playtime_forever" in g` for every game — the shape of a
  // NON-default ADR-0010 gate state (both relay_expose_playtime/
  // relay_expose_last_played on), while the real DEFAULT (both off, WP
  // 4h.0) omits the key entirely (`response_model_exclude_unset=True`).
  // The demo fixture was wrong, not this test in isolation, but the
  // assertion baked the wrong shape in — per docs/LEARNINGS.md ("demo
  // fixtures are a shipped surface, shapes 1:1 with the real API"), the
  // fixture is corrected (web/js/demo-data.js's `DEMO_OWNED_GAMES` now
  // ships keys-absent by default) and this baseline test now asserts the
  // DEFAULT shape instead of the accidentally-endorsed non-default one; the
  // enabled-gate shape is covered separately in
  // web/tests/demo-data-relay-privacy.test.js.
  await demoRequest("PUT", "/v1/steam/key", { body: { key: VALID_KEY } });
  const out = await demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: VALID_STEAMID } });
  assert.equal(out.configured, true);
  assert.ok(out.game_count > 0);
  assert.ok(Array.isArray(out.games));
  for (const g of out.games) {
    assert.ok("appid" in g && "name" in g);
    assert.equal("playtime_forever" in g, false, "the DEFAULT gate (both ADR-0010 keys off) must omit playtime_forever entirely, never a fabricated 0");
    assert.equal("rtime_last_played" in g, false, "the DEFAULT gate must omit rtime_last_played entirely");
  }
});

test("GET /v1/steam/owned-games answers 422 for a syntactically invalid steamid, even when configured", async () => {
  await demoRequest("PUT", "/v1/steam/key", { body: { key: VALID_KEY } });
  await assert.rejects(
    () => demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: "not-a-steamid" } }),
    (err) => err.status === 422,
  );
});

test("GET /v1/steam/player-summaries mirrors the same 409/422/200 shape", async () => {
  await assert.rejects(
    () => demoRequest("GET", "/v1/steam/player-summaries", { params: { steamid: VALID_STEAMID } }),
    (err) => err.status === 409,
  );
  await demoRequest("PUT", "/v1/steam/key", { body: { key: VALID_KEY } });
  const out = await demoRequest("GET", "/v1/steam/player-summaries", { params: { steamid: VALID_STEAMID } });
  assert.equal(out.configured, true);
  assert.equal(out.players.length, 1);
  assert.equal(out.players[0].steamid, VALID_STEAMID);
});

test("turning the relay off is immediate: DELETE then the very next call answers 409 again", async () => {
  await demoRequest("PUT", "/v1/steam/key", { body: { key: VALID_KEY } });
  await demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: VALID_STEAMID } });
  await demoRequest("DELETE", "/v1/steam/key");
  await assert.rejects(
    () => demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: VALID_STEAMID } }),
    (err) => err.status === 409,
  );
});

test("resetDemoData() clears settings overrides and the steam relay key between test cases", async () => {
  await demoRequest("PATCH", "/v1/settings", { body: { vault_name: "leftover" } });
  await demoRequest("PUT", "/v1/steam/key", { body: { key: VALID_KEY } });
  resetDemoData();
  const settings = await demoRequest("GET", "/v1/settings");
  assert.notEqual(settings.settings.find((s) => s.key === "vault_name").effective, "leftover");
  const steamKey = await demoRequest("GET", "/v1/steam/key");
  assert.equal(steamKey.configured, false);
});
