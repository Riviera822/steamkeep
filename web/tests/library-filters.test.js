/**
 * Headless tests for web/js/lib/library-filters.js (WP 4a.3).
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  matchesQuery,
  normalizeQuery,
  visibleGames,
  chipCounts,
  FILTER_DEFS,
} from "../js/lib/library-filters.js";
import { indexLiveJobsByAppid } from "../js/lib/game-status.js";

const game = (over) => ({
  appid: 1,
  name: "Aurora Cascade",
  status: "idle",
  last_prefill_at: null,
  size_bytes: null,
  ...over,
});

const GAMES = [
  game({ appid: 1, name: "Aurora Cascade", status: "done", size_bytes: 5_000_000_000 }), // cached
  game({ appid: 2, name: "Copper Horizon", status: "idle", size_bytes: null }), // none
  game({ appid: 3, name: "Driftwood Signal", status: "error", size_bytes: null }), // failed
  game({ appid: 4, name: "Ashfall Requiem", status: "idle", size_bytes: null }), // none
];
const JOBS = [{ id: 900, appid: 4, type: "prefill", status: "running" }];
const LIVE = indexLiveJobsByAppid(JOBS);

test("matchesQuery: case-insensitive substring on the title", () => {
  // matchesQuery's contract (see its docstring) is that `query` arrives
  // ALREADY normalized (normalizeQuery, tested below) — the case
  // insensitivity this test pins is the GAME NAME's casing, not the
  // query's; query casing is the caller's job.
  assert.equal(matchesQuery(game({ name: "Aurora Cascade" }), "cascade"), true);
  assert.equal(matchesQuery(game({ name: "AURORA CASCADE" }), "cascade"), true);
  assert.equal(matchesQuery(game({ name: "Aurora Cascade" }), "zzz"), false);
});
test("matchesQuery: an empty query matches everything", () => {
  assert.equal(matchesQuery(game({ name: "anything" }), ""), true);
});

test("normalizeQuery trims and lowercases", () => {
  assert.equal(normalizeQuery("  Cascade  "), "cascade");
  assert.equal(normalizeQuery(null), "");
  assert.equal(normalizeQuery(undefined), "");
});

test("FILTER_DEFS has no 'stale'/'update ready' chip (no oracle data on GET /v1/games yet)", () => {
  assert.equal(
    FILTER_DEFS.some((f) => f.key === "stale" || f.key === "upd"),
    false,
  );
});

test("visibleGames: 'all' with no query returns everything", () => {
  const list = visibleGames(GAMES, { query: "", filterKey: "all", liveJobsByAppid: LIVE });
  assert.equal(list.length, 4);
});

test("visibleGames: search and chip are ANDed", () => {
  // "a" matches Aurora Cascade, Ashfall Requiem, Driftwood Signal(no) —
  // 'a' appears in "Ashfall" and "Aurora" and "Driftwood Signal" (has an
  // 'a' too)... pick an unambiguous substring instead.
  const list = visibleGames(GAMES, { query: "ash", filterKey: "none", liveJobsByAppid: LIVE });
  // Only Ashfall Requiem matches BOTH "contains 'ash'" AND "is Not cached"
  // (appid 4 is actually mid-download -> its filter kind is "downloading",
  // not "none" -- see the next test for that nuance).
  assert.deepEqual(list.map((g) => g.appid), []);
});

test("visibleGames: a live job moves a game OUT of the 'none' filter and INTO 'downloading'", () => {
  const asNone = visibleGames(GAMES, { query: "", filterKey: "none", liveJobsByAppid: LIVE });
  assert.ok(!asNone.some((g) => g.appid === 4), "appid 4 has a running job, so it is not 'none'");

  const asDownloading = visibleGames(GAMES, {
    query: "",
    filterKey: "downloading",
    liveJobsByAppid: LIVE,
  });
  assert.deepEqual(asDownloading.map((g) => g.appid), [4]);
});

test("visibleGames: 'cached' and 'failed' chips", () => {
  const cached = visibleGames(GAMES, { query: "", filterKey: "cached", liveJobsByAppid: LIVE });
  assert.deepEqual(cached.map((g) => g.appid), [1]);
  const failed = visibleGames(GAMES, { query: "", filterKey: "failed", liveJobsByAppid: LIVE });
  assert.deepEqual(failed.map((g) => g.appid), [3]);
});

test("chipCounts recompute against the CURRENT query — always what the grid can show", () => {
  const counts = chipCounts(GAMES, { query: "driftwood", liveJobsByAppid: LIVE });
  const byKey = Object.fromEntries(counts.map((c) => [c.key, c.count]));
  assert.equal(byKey.all, 1);
  assert.equal(byKey.failed, 1);
  assert.equal(byKey.cached, 0);
  assert.equal(byKey.none, 0);
});

test("chipCounts with no query sums to the full library for 'all'", () => {
  const counts = chipCounts(GAMES, { query: "", liveJobsByAppid: LIVE });
  const all = counts.find((c) => c.key === "all");
  assert.equal(all.count, GAMES.length);
});
