/**
 * Pure decision-support statement pins (WP 4h.2) — see
 * web/js/lib/decision-support.js's header for the plan-anchored statement
 * families and the binding privacy stance this file exists to enforce.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { statementForGame, buildSuggestions, SUGGESTION_KIND } from "../js/lib/decision-support.js";

const DAY = 86_400_000;
const NOW = Date.parse("2026-08-19T00:00:00.000Z");

function game(overrides = {}) {
  return {
    appid: 100,
    name: "Aurora Cascade",
    status: "done",
    size_bytes: 10_000_000_000,
    last_manifest_check: null,
    manifest_change_frequency: null,
    manifest_observation_days: null,
    manifest_days_since_last_change: null,
    ...overrides,
  };
}

// ---------------------------------------------------------------------
// PLAYABLE_NOW — the honesty pin: absence must never fabricate a zero.
// ---------------------------------------------------------------------

test("PLAYABLE_NOW fires only on a REAL, explicit zero playtime with real cached bytes", () => {
  const s = statementForGame(game(), { playtimeForever: 0, nowMs: NOW });
  assert.equal(s.kind, SUGGESTION_KIND.PLAYABLE_NOW);
  assert.match(s.text, /never started/i);
  assert.match(s.text, /playable/i);
});

test("MUTATION PIN: an ABSENT playtime value never fabricates PLAYABLE_NOW (honesty: absence is unknown, not zero)", () => {
  const s = statementForGame(game(), { playtimeForever: undefined, nowMs: NOW });
  assert.notEqual(s?.kind, SUGGESTION_KIND.PLAYABLE_NOW);
});

test("a negative or non-finite playtime value is treated as unknown, never zero", () => {
  for (const bad of [-1, NaN, Infinity, "0", null]) {
    const s = statementForGame(game(), { playtimeForever: bad, nowMs: NOW });
    assert.notEqual(s?.kind, SUGGESTION_KIND.PLAYABLE_NOW, `playtimeForever=${bad} must not read as a real zero`);
  }
});

test("PLAYABLE_NOW never fires with zero playtime but no real cached bytes (size_bytes null/0)", () => {
  for (const sizeBytes of [null, 0, undefined]) {
    const s = statementForGame(game({ size_bytes: sizeBytes }), { playtimeForever: 0, nowMs: NOW });
    assert.notEqual(s?.kind, SUGGESTION_KIND.PLAYABLE_NOW);
  }
});

// ---------------------------------------------------------------------
// STALE_CONFIRMATION — threshold boundary.
// ---------------------------------------------------------------------

test("STALE_CONFIRMATION fires at exactly the 30-day threshold, not before", () => {
  const at29 = statementForGame(
    game({ last_manifest_check: new Date(NOW - 29 * DAY).toISOString() }),
    { nowMs: NOW },
  );
  assert.equal(at29, null, "29 days must not yet qualify");

  const at30 = statementForGame(
    game({ last_manifest_check: new Date(NOW - 30 * DAY).toISOString() }),
    { nowMs: NOW },
  );
  assert.equal(at30.kind, SUGGESTION_KIND.STALE_CONFIRMATION);
  assert.equal(at30.text, "Not confirmed current for 30 days.");
});

test("a null last_manifest_check produces no STALE_CONFIRMATION statement", () => {
  const s = statementForGame(game({ last_manifest_check: null }), { nowMs: NOW });
  assert.equal(s, null);
});

test("a clock-skewed FUTURE last_manifest_check does not produce a negative-day claim", () => {
  const s = statementForGame(
    game({ last_manifest_check: new Date(NOW + DAY).toISOString() }),
    { nowMs: NOW },
  );
  assert.equal(s, null);
});

// ---------------------------------------------------------------------
// CHANGED_RECENTLY / STABLE — WP 4h.1's four-state field.
// ---------------------------------------------------------------------

test("CHANGED_RECENTLY: 'changed' + a real days-since-last-change renders a past-tense, non-rate claim", () => {
  const s = statementForGame(
    game({ manifest_change_frequency: "changed", manifest_days_since_last_change: 3 }),
    { nowMs: NOW },
  );
  assert.equal(s.kind, SUGGESTION_KIND.CHANGED_RECENTLY);
  assert.equal(s.text, "Last changed 3 days ago.");
  assert.equal(/every/i.test(s.text), false, "must never claim a cadence — depot_manifests cannot support one");
});

test("CHANGED_RECENTLY singular day wording", () => {
  const s = statementForGame(
    game({ manifest_change_frequency: "changed", manifest_days_since_last_change: 1 }),
    { nowMs: NOW },
  );
  assert.equal(s.text, "Last changed 1 day ago.");
});

test("STABLE: 'stable' + observation days renders an unchanged-for-N-days claim", () => {
  const s = statementForGame(
    game({ manifest_change_frequency: "stable", manifest_observation_days: 60 }),
    { nowMs: NOW },
  );
  assert.equal(s.kind, SUGGESTION_KIND.STABLE);
  assert.equal(s.text, "Unchanged for the 60 days we've been watching.");
});

test("'insufficient_data' and null manifest_change_frequency both produce NO statement (distinct from 'stable', per WP 4h.1)", () => {
  for (const category of ["insufficient_data", null]) {
    const s = statementForGame(
      game({ manifest_change_frequency: category, manifest_observation_days: 5 }),
      { nowMs: NOW },
    );
    assert.equal(s, null, `category=${category} must not produce a statement`);
  }
});

// ---------------------------------------------------------------------
// Should-fix S4 (Opus review, WP 4h.2 fix round): the two manifest day
// fields need the same numeric sanitation `playtimeForever` already has —
// probed and confirmed rendering before this fix: NaN, negative, fractional,
// Infinity and a pathologically large integer.
// ---------------------------------------------------------------------

test("MUTATION PIN (S4): manifest_days_since_last_change rejects NaN/negative/fractional/Infinity/oversized values — no statement, never a garbage number in the text", () => {
  for (const bad of [NaN, -5, 3.7, Infinity, 1e21, "2"]) {
    const s = statementForGame(
      game({ manifest_change_frequency: "changed", manifest_days_since_last_change: bad }),
      { nowMs: NOW },
    );
    assert.equal(s, null, `manifest_days_since_last_change=${bad} must not produce a statement`);
  }
});

test("MUTATION PIN (S4): manifest_observation_days rejects the same bad values for the STABLE family", () => {
  for (const bad of [NaN, -1, 0.5, Infinity, 1e21]) {
    const s = statementForGame(
      game({ manifest_change_frequency: "stable", manifest_observation_days: bad }),
      { nowMs: NOW },
    );
    assert.equal(s, null, `manifest_observation_days=${bad} must not produce a statement`);
  }
});

test("a real zero day count IS a valid, printable value for both manifest families (0 is real, not falsy-rejected)", () => {
  const changed = statementForGame(
    game({ manifest_change_frequency: "changed", manifest_days_since_last_change: 0 }),
    { nowMs: NOW },
  );
  assert.equal(changed.text, "Last changed 0 days ago.");
  const stable = statementForGame(
    game({ manifest_change_frequency: "stable", manifest_observation_days: 0 }),
    { nowMs: NOW },
  );
  assert.equal(stable.text, "Unchanged for the 0 days we've been watching.");
});

test("a game with nothing qualifying at all returns null, not a fabricated fallback", () => {
  const s = statementForGame(game(), { nowMs: NOW });
  assert.equal(s, null);
});

// ---------------------------------------------------------------------
// Priority within one game: PLAYABLE_NOW beats every other family.
// ---------------------------------------------------------------------

test("when a game qualifies for BOTH PLAYABLE_NOW and STALE_CONFIRMATION, only PLAYABLE_NOW is returned", () => {
  const s = statementForGame(
    game({ last_manifest_check: new Date(NOW - 90 * DAY).toISOString() }),
    { playtimeForever: 0, nowMs: NOW },
  );
  assert.equal(s.kind, SUGGESTION_KIND.PLAYABLE_NOW);
});

// ---------------------------------------------------------------------
// The negative privacy pin (build spec's binding requirement): no
// statement string, under ANY input including a non-zero playtime or a
// last-played timestamp, ever reads as judgemental or names a number this
// module is not allowed to name.
// ---------------------------------------------------------------------

const JUDGEMENTAL_PATTERNS = [
  /haven'?t played/i,
  /never played/i, // distinct from "never started" (the module's own wording), which is allowed
  /\d+\s*(hours?|minutes?)\s*played/i, // a played-time NUMBER is never named
  /since.*(played|last)/i,
  /last played/i,
];

test("MUTATION PIN (negative privacy pin): non-zero playtime never produces PLAYABLE_NOW and never surfaces the number", () => {
  for (const pt of [1, 42, 500, 100_000]) {
    const s = statementForGame(game(), { playtimeForever: pt, nowMs: NOW });
    assert.notEqual(s?.kind, SUGGESTION_KIND.PLAYABLE_NOW, `playtimeForever=${pt} must not trigger PLAYABLE_NOW`);
    if (s) assert.equal(s.text.includes(String(pt)), false, `statement text must never contain the raw playtime number ${pt}`);
  }
});

test("MUTATION PIN (negative privacy pin): rtime_last_played is never read into any statement, however it is spelled on the input object", () => {
  const withLastPlayed = { ...game(), rtime_last_played: Math.floor(NOW / 1000) - 3600, playtime_forever: 0 };
  // Passed the whole game object (which itself carries rtime_last_played,
  // mirroring OwnedGameOut's shape) — statementForGame must not reach for it
  // even structurally, since this module only ever destructures
  // `playtimeForever` from its OWN second argument, never the game object's
  // `rtime_last_played`/`playtime_forever` fields directly.
  const s = statementForGame(withLastPlayed, { playtimeForever: 0, nowMs: NOW });
  assert.equal(s.kind, SUGGESTION_KIND.PLAYABLE_NOW);
  assert.ok(!/\b\d{5,}\b/.test(s.text), "no large (epoch-shaped) number leaked into the statement text");
});

test("no statement produced by ANY family, across a broad input sweep, ever matches a judgemental pattern", () => {
  const sweeps = [
    game({ manifest_change_frequency: "changed", manifest_days_since_last_change: 400 }),
    game({ manifest_change_frequency: "stable", manifest_observation_days: 900 }),
    game({ last_manifest_check: new Date(NOW - 800 * DAY).toISOString() }),
  ];
  for (const g of sweeps) {
    for (const pt of [undefined, 0, 999]) {
      const s = statementForGame(g, { playtimeForever: pt, nowMs: NOW });
      if (!s) continue;
      for (const re of JUDGEMENTAL_PATTERNS) {
        assert.equal(re.test(s.text), false, `statement "${s.text}" matched banned pattern ${re}`);
      }
    }
  }
});

// ---------------------------------------------------------------------
// buildSuggestions: ranking, limit, and the tier ladder.
// ---------------------------------------------------------------------

test("buildSuggestions ranks PLAYABLE_NOW before STALE_CONFIRMATION before CHANGED_RECENTLY before STABLE", () => {
  const games = [
    game({ appid: 4, manifest_change_frequency: "stable", manifest_observation_days: 40 }),
    game({ appid: 3, manifest_change_frequency: "changed", manifest_days_since_last_change: 2 }),
    game({ appid: 2, last_manifest_check: new Date(NOW - 45 * DAY).toISOString() }),
    game({ appid: 1, size_bytes: 5_000_000_000 }),
  ];
  const playtimeByAppid = new Map([[1, 0]]);
  const { items } = buildSuggestions(games, { playtimeByAppid, nowMs: NOW, limit: 10 });
  assert.deepEqual(items.map((i) => i.kind), [
    SUGGESTION_KIND.PLAYABLE_NOW,
    SUGGESTION_KIND.STALE_CONFIRMATION,
    SUGGESTION_KIND.CHANGED_RECENTLY,
    SUGGESTION_KIND.STABLE,
  ]);
});

test("buildSuggestions ties within the same kind break by ascending appid", () => {
  const games = [
    game({ appid: 20, manifest_change_frequency: "stable", manifest_observation_days: 10 }),
    game({ appid: 10, manifest_change_frequency: "stable", manifest_observation_days: 10 }),
  ];
  const { items } = buildSuggestions(games, { nowMs: NOW });
  assert.deepEqual(items.map((i) => i.appid), [10, 20]);
});

test("buildSuggestions respects `limit`", () => {
  const games = [1, 2, 3, 4, 5, 6].map((n) =>
    game({ appid: n, manifest_change_frequency: "stable", manifest_observation_days: 10 }),
  );
  const { items } = buildSuggestions(games, { nowMs: NOW, limit: 2 });
  assert.equal(items.length, 2);
});

test('tier is "full" whenever at least one PLAYABLE_NOW item is present', () => {
  const games = [game({ appid: 1 })];
  const { tier } = buildSuggestions(games, { playtimeByAppid: new Map([[1, 0]]), nowMs: NOW });
  assert.equal(tier, "full");
});

test('tier is "frequency" when items exist but none used playtime', () => {
  const games = [game({ appid: 1, manifest_change_frequency: "stable", manifest_observation_days: 5 })];
  const { tier, items } = buildSuggestions(games, { nowMs: NOW });
  assert.equal(items.length, 1);
  assert.equal(tier, "frequency");
});

test('MUTATION PIN: tier is "insufficient_data" when nothing qualifies at all — the honest empty-panel state, never silently "frequency"', () => {
  const games = [game({ appid: 1 }), game({ appid: 2, manifest_change_frequency: "insufficient_data" })];
  const { tier, items } = buildSuggestions(games, { nowMs: NOW });
  assert.equal(items.length, 0);
  assert.equal(tier, "insufficient_data");
});

test("buildSuggestions tolerates a non-array/undefined games snapshot (before the first successful poll)", () => {
  for (const bad of [undefined, null, "not an array"]) {
    const { tier, items } = buildSuggestions(bad, { nowMs: NOW });
    assert.deepEqual(items, []);
    assert.equal(tier, "insufficient_data");
  }
});

test("a malformed game row (no numeric appid) is skipped, not thrown on", () => {
  const games = [{ name: "broken" }, game({ appid: 1 })];
  const playtimeByAppid = new Map([[1, 0]]);
  const { items } = buildSuggestions(games, { playtimeByAppid, nowMs: NOW });
  assert.equal(items.length, 1);
  assert.equal(items[0].appid, 1);
});

test("a game with no `name` falls back to 'App <appid>', never a blank label", () => {
  const games = [game({ appid: 77, name: null })];
  const { items } = buildSuggestions(games, { playtimeByAppid: new Map([[77, 0]]), nowMs: NOW });
  assert.equal(items[0].name, "App 77");
});
