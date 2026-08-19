/**
 * WP 4h.2 fixes to two of the three carried-over defects from the WP 4h.0
 * review (both api/-only WP 4h.0/4h.1 landings left web/js/demo-data.js
 * diverged from the real API — see that module's own comments at
 * `ENV_ONLY_DEMO`/`DEMO_OWNED_GAMES`/`demoOwnedGameForCurrentGate`):
 *
 *   1. `DEMO_OWNED_GAMES`'s DEFAULT shape now omits `playtime_forever`/
 *      `rtime_last_played` entirely (both ADR-0010 keys ship off by
 *      default) — the enabled-gate shape is a SEPARATE, explicit fixture
 *      reached only via `resetDemoData({ relayExposePlaytime,
 *      relayExposeLastPlayed })`, mirroring the real server's env-var+
 *      restart-only knob.
 *   3. `ENV_ONLY_DEMO` now carries `relay_expose_playtime`/
 *      `relay_expose_last_played` as two more informational rows, and
 *      `PATCH` on either answers the SAME "environment-only" 422 detail
 *      string `api/vault_api/routers/settings.py`'s `_ENV_ONLY_DETAIL_
 *      TEMPLATE` uses (cross-checked against that file, not guessed),
 *      instead of the "unrecognised setting key" 422 they used to fall
 *      through to.
 *
 * (Defect 2 — the `demo-data-settings.test.js:165` baseline-test edit — is
 * covered in that file directly, named in its own comment, per the "one
 * allowed edit" rule.)
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { demoRequest, resetDemoData } from "../js/demo-data.js";

const VALID_KEY = "a".repeat(32);
const VALID_STEAMID = "76561198042117903";

beforeEach(() => {
  resetDemoData();
});

async function connectSteam() {
  await demoRequest("PUT", "/v1/steam/key", { body: { key: VALID_KEY } });
}

// ---------------------------------------------------------------------
// Defect 1: the owned-games fixture's DEFAULT shape.
// ---------------------------------------------------------------------

test("MUTATION PIN: DEFAULT gate (no resetDemoData options) omits playtime_forever/rtime_last_played from EVERY owned game", async () => {
  await connectSteam();
  const out = await demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: VALID_STEAMID } });
  assert.ok(out.games.length > 0, "sanity: the fixture must not be empty");
  for (const g of out.games) {
    assert.equal("playtime_forever" in g, false, `appid ${g.appid} carries playtime_forever under the DEFAULT gate`);
    assert.equal("rtime_last_played" in g, false, `appid ${g.appid} carries rtime_last_played under the DEFAULT gate`);
  }
});

test("relayExposePlaytime:true adds playtime_forever ONLY where the fixture has a value, never a fabricated one, and never rtime_last_played", async () => {
  resetDemoData({ relayExposePlaytime: true });
  await connectSteam();
  const out = await demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: VALID_STEAMID } });
  const byAppid = new Map(out.games.map((g) => [g.appid, g]));

  const withPlaytime = byAppid.get(2010010);
  assert.equal(withPlaytime.playtime_forever, 4312);
  assert.equal("rtime_last_played" in withPlaytime, false, "relayExposeLastPlayed is independently off — must not leak");

  // Explicit real zero (3300300, "Quietbrook") must render as a real 0, not be
  // dropped as if it were absence.
  const neverPlayed = byAppid.get(3300300);
  assert.equal(neverPlayed.playtime_forever, 0);
});

test("relayExposeLastPlayed:true adds rtime_last_played ONLY for the one appid this fixture has no value for at all (3300100) omitting it, independent of playtime", async () => {
  resetDemoData({ relayExposePlaytime: true, relayExposeLastPlayed: true });
  await connectSteam();
  const out = await demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: VALID_STEAMID } });
  const byAppid = new Map(out.games.map((g) => [g.appid, g]));

  const both = byAppid.get(2010010);
  assert.equal(both.playtime_forever, 4312);
  assert.equal(typeof both.rtime_last_played, "number");

  const playtimeOnlyFixture = byAppid.get(3300100); // DEMO_OWNED_GAMES_PLAYTIME has no rtime_last_played for this appid
  assert.equal(playtimeOnlyFixture.playtime_forever, 972);
  assert.equal(
    "rtime_last_played" in playtimeOnlyFixture,
    false,
    "the gate being ON must not fabricate a last-played value the fixture never recorded for this appid",
  );
});

test("MUTATION PIN: relayExposeLastPlayed:true with relayExposePlaytime left false exposes ONLY the last-played field, never playtime — the two keys are independent", async () => {
  resetDemoData({ relayExposeLastPlayed: true });
  await connectSteam();
  const out = await demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: VALID_STEAMID } });
  const aurora = out.games.find((g) => g.appid === 2010010);
  assert.equal("playtime_forever" in aurora, false);
  assert.equal(typeof aurora.rtime_last_played, "number");
});

test("resetDemoData() with no options resets the gate back to the default (off) even after a prior test turned it on", async () => {
  resetDemoData({ relayExposePlaytime: true, relayExposeLastPlayed: true });
  resetDemoData(); // no options — must go back to the default, not stay latched on
  await connectSteam();
  const out = await demoRequest("GET", "/v1/steam/owned-games", { params: { steamid: VALID_STEAMID } });
  for (const g of out.games) {
    assert.equal("playtime_forever" in g, false);
    assert.equal("rtime_last_played" in g, false);
  }
});

// ---------------------------------------------------------------------
// Defect 3: ENV_ONLY_DEMO parity.
// ---------------------------------------------------------------------

test("GET /v1/settings reports relay_expose_playtime/relay_expose_last_played as env-only informational rows, defaulting to false/'default'", async () => {
  const out = await demoRequest("GET", "/v1/settings");
  for (const key of ["relay_expose_playtime", "relay_expose_last_played"]) {
    const entry = out.settings.find((s) => s.key === key);
    assert.ok(entry, `missing settings row for ${key}`);
    assert.equal(entry.env_only, true);
    assert.equal(entry.applies, "restart-required");
    assert.equal(entry.effective, false);
    assert.equal(entry.source, "default");
  }
});

test("resetDemoData({ relayExposePlaytime: true }) reports relay_expose_playtime as effective:true, source:'env'", async () => {
  resetDemoData({ relayExposePlaytime: true });
  const out = await demoRequest("GET", "/v1/settings");
  const entry = out.settings.find((s) => s.key === "relay_expose_playtime");
  assert.equal(entry.effective, true);
  assert.equal(entry.source, "env");
  // The OTHER key stays independently off.
  const other = out.settings.find((s) => s.key === "relay_expose_last_played");
  assert.equal(other.effective, false);
  assert.equal(other.source, "default");
});

test('MUTATION PIN: PATCH on relay_expose_playtime answers the SAME "environment-only" 422 detail as the real API (api/vault_api/routers/settings.py\'s _ENV_ONLY_DETAIL_TEMPLATE), not "unrecognised setting key"', async () => {
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { relay_expose_playtime: "true" } }),
    (err) => {
      assert.equal(err.status, 422);
      assert.equal(
        err.detail,
        "'relay_expose_playtime' is environment-only and cannot be changed via the API; set its environment variable and restart instead.",
      );
      assert.equal(/not a recognised setting/i.test(err.detail), false, "must not fall through to the generic unknown-key message");
      return true;
    },
  );
});

test("PATCH on relay_expose_last_played answers the same environment-only 422 shape", async () => {
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { relay_expose_last_played: "true" } }),
    (err) => {
      assert.equal(err.status, 422);
      assert.equal(
        err.detail,
        "'relay_expose_last_played' is environment-only and cannot be changed via the API; set its environment variable and restart instead.",
      );
      return true;
    },
  );
});

test("the fix to the env-only 422 template also corrects the message for the seven pre-existing env-only keys (parity was templated, not per-key)", async () => {
  await assert.rejects(
    () => demoRequest("PATCH", "/v1/settings", { body: { db_path: "/elsewhere" } }),
    (err) => {
      assert.equal(err.status, 422);
      assert.equal(
        err.detail,
        "'db_path' is environment-only and cannot be changed via the API; set its environment variable and restart instead.",
      );
      return true;
    },
  );
});
