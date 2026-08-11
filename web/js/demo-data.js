/**
 * Demo-mode fixtures (WP 4a.2).
 *
 * Serves the app entirely from in-memory, synthetic data so it works with
 * no vault present (NOTES open question 5: "real value for first-run and
 * screenshots"). Shapes are modeled 1:1 on the real vault-api responses
 * documented in api/README.md's "Endpoints" table and the Pydantic models
 * in api/vault_api/routers/{games,jobs,clients}.py — field names match
 * exactly, so api.js's callers and the polling store cannot tell demo mode
 * and a real server apart.
 *
 * Per docs/LEARNINGS.md ("Testing discipline": fixtures are synthetic,
 * modeled on real structure, never personal data): every title, id and
 * client name below is fictional; none of it is real Steam data.
 *
 * No network access of any kind (CSP compatibility, WP 4a.2 scope) — this
 * is local state, reset on every page load, not a mock HTTP server.
 *
 * Testability: this module imports only errors.js and two other DOM-free
 * `lib/` modules (no `window`, no `document`, no `fetch`) — it is plain
 * data plus plain functions and runs in bare Node. `resetDemoData()`
 * restores the module's mutable state to a fresh copy of the seed data;
 * tests call it between cases so scenarios (e.g. "delete this game") don't
 * leak into the next test (see web/tests/demo-data.test.js).
 *
 * WP 4a.6 extends this with `/v1/settings` (ADR-0009) and the Steam Web API
 * relay (`/v1/steam/*`, ADR-0004 addendum) so the Settings view has
 * something to render in demo mode instead of a bare 404 toast — see
 * web/tests/demo-data-settings.test.js. Both reuse the same pure validators
 * the real router/relay use client-side (`lib/steamid.js`,
 * `lib/steam-key-form.js`) so a demo rejection and a real `422` agree on
 * shape, without duplicating the grammar a third time.
 */

import { ApiError, ERROR_KINDS } from "./errors.js";
import { validSteamId64 } from "./lib/steamid.js";
import { validSteamWebApiKey } from "./lib/steam-key-form.js";

function isoAgo(msAgo) {
  return new Date(Date.now() - msAgo).toISOString();
}

// ---------------------------------------------------------------------
// Seed data
// ---------------------------------------------------------------------

function makeGame({
  appid,
  name,
  status,
  needsForce = false,
  depots = [],
  // WP 4a.4: `apps.last_manifest_check` (api/README.md "Job outcome
  // honesty") — set on only two seed games below to exercise all three
  // `lib/detail-wording.js` branches without inventing a fourth game just
  // for this field. `null` (the default) is the common case: an ordinary
  // run that actually changed depots leaves it untouched, so most demo
  // games are honestly "never confirmed" even while `status: "done"`.
  lastManifestCheck = null,
  // WP 4a.4 demo-only: bytes a GC dry run "discovers" as reclaimable for
  // this app, purely so the GC flow has something to show in demo mode —
  // never serialized into any response (see gcHandler below). Real
  // vault-api computes this from a manifest diff (`gc.py`); the demo model
  // tracks no manifests at all, so this is a deliberately simple stand-in,
  // not an approximation of the real algorithm.
  gcReclaimableBytes = 0,
  // WP 4a.8 demo-only addition: bytes the dry run finds but reports HELD
  // BACK (api/vault_api/gc_execute.py's `RecentlyStoredGrace` — a chunk
  // stored within the grace window). Kept separate from
  // `gcReclaimableBytes` (which is what an EXECUTE run actually frees) so
  // demo mode can exercise the real `held_back=N (M bytes)` field
  // `lib/gc-log-summary.js` parses end to end — before this WP the demo
  // log's `held_back` was hardcoded to `0 (0 bytes)` in every scenario, so
  // that branch of the parser (and the detail sheet's "N chunks held
  // back — inside the grace window" note) was never exercised by demo mode
  // at all. An execute run does NOT clear this counter: the grace window is
  // a TIME rule, not something an execute run changes (gc_execute.py: "the
  // separation is deliberate... time is a policy on top of [the plan],
  // not part of it").
  gcHeldBackBytes = 0,
}) {
  return {
    appid,
    name,
    status,
    last_prefill_at: status === "idle" ? null : isoAgo(3 * 3_600_000),
    last_manifest_check: lastManifestCheck,
    needs_force: needsForce,
    // Doubles as both "mapping rows" and "cache state" for this depot in the
    // demo model (the real vault-api keeps those two facts separately —
    // api/README.md "Per-game deletion": deletion clears cache content, not
    // mapping rows). Simplification accepted for this WP: see the DELETE
    // /v1/cache/{appid} handler below for where that would matter and how
    // it is approximated.
    depots,
    _demoGcReclaimableBytes: gcReclaimableBytes,
    _demoGcHeldBackBytes: gcHeldBackBytes,
  };
}

function buildGames() {
  return [
    makeGame({
      appid: 2010010,
      name: "Aurora Cascade",
      status: "done",
      depots: [{ depotid: 2010011, shared: false, size_bytes: 4_200_000_000 }],
      // Gives the GC dry-run flow something honest to find on the first
      // check (WP 4a.4 live-verification fixture) — see makeGame()'s header.
      gcReclaimableBytes: 120_000_000,
    }),
    makeGame({
      appid: 2010020,
      name: "Copper Horizon",
      status: "idle",
      needsForce: true,
      depots: [],
      // WP 4a.4: last_manifest_check SURVIVING a null last_prefill_at is
      // exactly the post-deletion shape api/README.md documents — this game
      // was once prefilled and confirmed current, then had its cache
      // deleted (needsForce: true is the other half of that same story).
      // Exercises lib/detail-wording.js's CONFIRMED_BEFORE_CACHE_CLEARED
      // branch ("Confirmed current at X (before the cache was cleared)").
      lastManifestCheck: isoAgo(2 * 86_400_000),
    }),
    makeGame({
      appid: 2010030,
      name: "Driftwood Signal",
      status: "running",
      depots: [{ depotid: 2010031, shared: false, size_bytes: 1_100_000_000 }],
    }),
    makeGame({
      appid: 2010040,
      name: "Emberreach",
      status: "done",
      depots: [
        { depotid: 2010041, shared: false, size_bytes: 800_000_000 },
        // Shared with Frostline Convoy (2010050) below — deleting Emberreach
        // alone must SKIP this depot (Frostline is still cached), and
        // deleting Frostline afterward must skip it right back (Emberreach
        // is still cached) — the "sole holder" case only fires once both
        // are gone. See web/tests/demo-data.test.js.
        { depotid: 2010060, shared: true, size_bytes: 300_000_000 },
      ],
    }),
    makeGame({
      appid: 2010050,
      name: "Frostline Convoy",
      status: "done",
      depots: [{ depotid: 2010060, shared: true, size_bytes: 300_000_000 }],
      // WP 4a.4: the ordinary CONFIRMED case — both timestamps present.
      // Exercises lib/detail-wording.js's CONFIRMED branch ("Confirmed
      // current at X").
      lastManifestCheck: isoAgo(3_600_000),
    }),
    makeGame({
      appid: 2010070,
      name: "Glass Meridian",
      status: "error",
      needsForce: true,
      depots: [{ depotid: 2010071, shared: false, size_bytes: 2_400_000_000 }],
      // WP 4a.8: gives the GC dry-run flow a non-zero `held_back` alongside
      // its `would_delete` — see makeGame()'s header. Aurora Cascade (above)
      // stays held_back=0 on purpose so the pre-existing
      // web/tests/demo-data-gc.test.js assertions keep pinning the plain
      // case unchanged.
      gcReclaimableBytes: 40_000_000,
      gcHeldBackBytes: 15_000_000,
    }),
  ];
}

function buildJobs() {
  return [
    {
      id: 900001,
      appid: 2010030,
      type: "prefill",
      status: "running",
      created_at: isoAgo(60_000),
      started_at: isoAgo(45_000),
      finished_at: null,
      updated: null,
      up_to_date: null,
      summary_parse_ok: null,
      gc_execute: null,
      paused_at: null,
      stop_request: null,
      log_excerpt:
        "[vault-api] worker claimed job 900001\nDownloading depot 2010031 ...",
      // Demo-only bookkeeping (never serialized into a response): how many
      // more GET /v1/jobs polls before this job "finishes". Call-count
      // driven rather than wall-clock so demo mode needs no timers of its
      // own.
      _demoTicksLeft: 3,
    },
    {
      id: 900000,
      appid: 2010070,
      type: "prefill",
      status: "error",
      created_at: isoAgo(3 * 3_600_000),
      started_at: isoAgo(3 * 3_600_000 - 5_000),
      finished_at: isoAgo(3 * 3_600_000 - 1_000),
      updated: 0,
      up_to_date: 0,
      summary_parse_ok: true,
      gc_execute: null,
      paused_at: null,
      stop_request: null,
      log_excerpt: "[vault-api] SteamPrefill exited 1 — see attached log.",
    },
    {
      id: 899999,
      appid: 2010010,
      type: "prefill",
      status: "done",
      created_at: isoAgo(6 * 3_600_000),
      started_at: isoAgo(6 * 3_600_000 - 5_000),
      finished_at: isoAgo(6 * 3_600_000 - 1_000),
      updated: 12,
      up_to_date: 0,
      summary_parse_ok: true,
      gc_execute: null,
      paused_at: null,
      stop_request: null,
      log_excerpt: "[vault-api] worker claimed job 899999\nPrefilled 12 apps.",
    },
  ];
}

function buildClients() {
  return [
    {
      client_id: "workshop-pc",
      first_seen: isoAgo(30 * 86_400_000),
      last_reported_at: isoAgo(3_600_000),
      app_count: 148,
      source_addrs: ["10.10.0.21"],
      cache_hits: 4213,
      cache_misses: 96,
      bytes_served: 812_345_678_912,
      last_seen_in_cache_log: isoAgo(120_000),
      bypass_suspected: false,
    },
    {
      client_id: "loft-laptop",
      first_seen: isoAgo(9 * 86_400_000),
      last_reported_at: isoAgo(7_200_000),
      app_count: 42,
      source_addrs: ["10.10.0.44"],
      cache_hits: 3,
      cache_misses: 1,
      bytes_served: 41_943_040,
      last_seen_in_cache_log: null,
      bypass_suspected: true,
    },
  ];
}

let games = buildGames();
let jobs = buildJobs();
let clients = buildClients();
let nextJobId = 900002;

/** Restore all demo state to a fresh copy of the seed data. Exported for
 * tests (web/tests/demo-data.test.js) so scenarios don't leak between
 * cases; the real app never calls this — a page reload does the same
 * thing by re-importing the module. */
export function resetDemoData() {
  games = buildGames();
  jobs = buildJobs();
  clients = buildClients();
  nextJobId = 900002;
  resetDemoSettings();
  resetDemoSteamRelay();
}

// ---------------------------------------------------------------------
// /v1/settings (WP 4a.6; ADR-0009) — a small in-memory mirror of
// vault_api/settings_store.py's precedence rule (db override > env value >
// built-in default), enough to exercise the Settings view's real code path
// in demo mode. `env` below stands in for "what Settings.from_env() would
// have produced" — fixed per key rather than reading real env vars (demo
// mode has no process env of its own to read).
// ---------------------------------------------------------------------

const WEBHOOK_EVENTS_ALL = Object.freeze([
  "job.done",
  "job.error",
  "job.cancelled",
  "client.bypass_suspected",
  "client.bypass_resolved",
]);
const AUTO_GC_MODES = Object.freeze(["off", "dry-run", "execute"]);

// key -> {default, env}. `env` != `default` for vault_name/schedule_window
// on purpose, so a fresh demo session shows a realistic mix of "default"
// and "env" sourced rows, not everything defaulted.
const SETTINGS_BASE = {
  vault_name: { default: "", env: "steamvault-demo" },
  schedule_window: { default: null, env: "22:00-06:00" },
  schedule_interval_minutes: { default: 180, env: 180 },
  schedule_client_stale_days: { default: 7, env: 7 },
  auto_gc: { default: "off", env: "off" },
  webhook_url: { default: "", env: "" },
  webhook_events: { default: [...WEBHOOK_EVENTS_ALL], env: [...WEBHOOK_EVENTS_ALL] },
};

const SETTINGS_APPLIES = {
  vault_name: "restart-required",
  schedule_window: "next_sweep",
  schedule_interval_minutes: "next_sweep",
  schedule_client_stale_days: "next_sweep",
  auto_gc: "immediately",
  webhook_url: "restart-required",
  webhook_events: "restart-required",
};

// Mirrors settings_store.ENV_ONLY_INFO_KEYS — informational-only rows a
// settings screen shows as "this exists but only the environment controls
// it" (api/README.md "Env-only keys"). `vault_api_key` is excluded here
// too, same reasoning as the real endpoint: it never appears in ANY GET
// response, not even redacted.
const ENV_ONLY_DEMO = [
  { key: "db_path", value: "/data/vault.db" },
  { key: "cache_root", value: "/vault/cache" },
  { key: "steamprefill_path", value: "/usr/local/bin/steamprefill" },
  { key: "steamprefill_cache_dir", value: "/root/.local/share/SteamPrefill" },
  { key: "manifest_archive_dir", value: "/vault/manifest-archive" },
  { key: "web_dir", value: "/app/web" },
  { key: "settings_readonly", value: false },
];
const ENV_ONLY_KEYS = new Set(["vault_api_key", ...ENV_ONLY_DEMO.map((e) => e.key)]);

let settingsOverrides = {}; // key -> raw string, mirrors the `settings` table's TEXT column
const settingsReadonly = false; // demo mode never ships VAULT_SETTINGS_READONLY

function resetDemoSettings() {
  settingsOverrides = {};
}

/** Raw PATCH-body value -> the typed value, or throw a 422 ApiError — the
 * demo-mode mirror of each `SettingSpec.parse` in settings_store.py. Typed
 * values are only used internally (to decide "did this change") and for
 * the GET projection; PATCH itself stores the raw string, same as the real
 * `settings` table. */
function parseSettingValue(key, raw) {
  if (key === "vault_name") return raw.trim();
  if (key === "schedule_window") {
    const text = raw.trim();
    if (!text) return null;
    if (!/^\d{2}:\d{2}-\d{2}:\d{2}$/.test(text)) {
      throw validationError(`'${key}': expected 'HH:MM-HH:MM' (e.g. '22:00-06:00'), got ${JSON.stringify(raw)}.`);
    }
    return text;
  }
  if (key === "schedule_interval_minutes" || key === "schedule_client_stale_days") {
    const text = raw.trim();
    if (!/^[0-9]+$/.test(text) || Number(text) < 1) {
      throw validationError(`'${key}' must be a positive whole number, got ${JSON.stringify(raw)}.`);
    }
    return Number(text);
  }
  if (key === "auto_gc") {
    const text = raw.trim();
    if (!AUTO_GC_MODES.includes(text)) {
      throw validationError(`'${key}' must be one of ${AUTO_GC_MODES.join(", ")}, got ${JSON.stringify(raw)}.`);
    }
    return text;
  }
  if (key === "webhook_url") {
    const text = raw.trim();
    if (text && !/^https?:\/\//i.test(text)) {
      throw validationError(`'${key}' must be a http(s) URL or blank to disable, got ${JSON.stringify(raw)}.`);
    }
    return text;
  }
  if (key === "webhook_events") {
    const text = raw.trim();
    if (!text) return [...WEBHOOK_EVENTS_ALL];
    const tokens = text.split(",").map((t) => t.trim());
    if (tokens.some((t) => !t)) {
      throw validationError(`'${key}' must be a comma-separated list with no empty entries.`);
    }
    const unknown = tokens.filter((t) => !WEBHOOK_EVENTS_ALL.includes(t));
    if (unknown.length) {
      throw validationError(`'${key}' contains unknown event name(s): ${unknown.join(", ")}.`);
    }
    return [...new Set(tokens)];
  }
  throw validationError(`unrecognised setting key ${JSON.stringify(key)}`); // unreachable: callers only pass known keys
}

/** One entry's `effective`/`source`/`fallback`, mirroring
 * settings_store.describe_settings's precedence exactly (db > env > default). */
function describeDemoSettings() {
  const infos = [];
  for (const key of Object.keys(SETTINGS_BASE)) {
    const base = SETTINGS_BASE[key];
    const raw = settingsOverrides[key];
    let effective;
    let source;
    if (raw !== undefined) {
      effective = parseSettingValue(key, raw);
      source = "db";
    } else {
      effective = base.env;
      source = JSON.stringify(base.env) === JSON.stringify(base.default) ? "default" : "env";
    }
    infos.push({
      key,
      effective,
      source,
      fallback: base.env,
      applies: SETTINGS_APPLIES[key],
      env_only: false,
    });
  }
  for (const entry of ENV_ONLY_DEMO) {
    infos.push({
      key: entry.key,
      effective: entry.value,
      source: "default",
      fallback: entry.value,
      applies: "restart-required",
      env_only: true,
    });
  }
  return infos;
}

/** One `PATCH` body value -> the raw string `parseSettingValue` expects, or
 * throw a 422 — mirrors routers/settings.py's `_coerce_patch_value`
 * (booleans rejected explicitly, `webhook_events` accepts a list). */
function coercePatchValue(key, value) {
  if (typeof value === "boolean") {
    throw validationError(`'${key}' must be a string, not a boolean.`);
  }
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  if (Array.isArray(value) && key === "webhook_events") {
    if (!value.every((v) => typeof v === "string")) {
      throw validationError(`'${key}': every list item must be a string event name.`);
    }
    return value.join(",");
  }
  throw validationError(`'${key}' must be a string, or null to clear the override.`);
}

function handleGetSettings() {
  return { readonly: settingsReadonly, settings: describeDemoSettings() };
}

function handlePatchSettings(body) {
  if (settingsReadonly) {
    throw new ApiError(ERROR_KINDS.VALIDATION, "The settings API is read-only.", { status: 403 });
  }
  const toSet = [];
  const toClear = [];
  for (const [key, value] of Object.entries(body || {})) {
    if (ENV_ONLY_KEYS.has(key)) {
      throw validationError(`'${key}' is environment-only and cannot be changed via the API.`);
    }
    if (!(key in SETTINGS_BASE)) {
      throw validationError(`'${key}' is not a recognised setting.`);
    }
    if (value === null) {
      toClear.push(key);
      continue;
    }
    const raw = coercePatchValue(key, value);
    parseSettingValue(key, raw); // validate only, same "validate everything first" order
    toSet.push([key, raw]);
  }
  // Everything validated above before anything is written (ADR-0009: a bad
  // value in a multi-key PATCH must persist nothing).
  for (const [key, raw] of toSet) settingsOverrides[key] = raw;
  for (const key of toClear) delete settingsOverrides[key];
  return handleGetSettings();
}

// ---------------------------------------------------------------------
// /v1/steam/* — the opt-in Steam Web API relay (WP 4a.6r; ADR-0004
// addendum). Demo mode never actually calls Valve; it validates the same
// shapes the real relay does (via the shared `lib/` validators) and answers
// from a small fixture library once a syntactically valid key is "set".
// ---------------------------------------------------------------------

let steamKeyConfigured = false;
let steamKeyLast4 = null;

function resetDemoSteamRelay() {
  steamKeyConfigured = false;
  steamKeyLast4 = null;
}

// Fictional owned-games fixture (LEARNINGS "Testing discipline": synthetic,
// never real Steam data) — deliberately a DIFFERENT list from the cache
// library's demo games above: this is what "the Steam Web API says this
// account owns", which in real life is almost always a much bigger,
// unrelated set from what happens to be cached.
const DEMO_OWNED_GAMES = [
  { appid: 2010010, name: "Aurora Cascade", playtime_forever: 4312, img_icon_url: "" },
  { appid: 2010040, name: "Emberreach", playtime_forever: 118, img_icon_url: "" },
  { appid: 3300100, name: "Sable Undertow", playtime_forever: 972, img_icon_url: "" },
  { appid: 3300200, name: "Halcyon Foundry", playtime_forever: 26, img_icon_url: "" },
  { appid: 3300300, name: "Quietbrook", playtime_forever: 0, img_icon_url: "" },
];

function demoPlayerSummary(steamid) {
  return {
    steamid,
    personaname: "vaultkeeper_demo",
    avatar: "https://avatars.steamstatic.com/demo_small.jpg",
    avatarmedium: "https://avatars.steamstatic.com/demo_medium.jpg",
    avatarfull: "https://avatars.steamstatic.com/demo_full.jpg",
    personastate: 1,
  };
}

function handleGetSteamKey() {
  return { configured: steamKeyConfigured, key_last4: steamKeyLast4 };
}

function handlePutSteamKey(body) {
  const key = body && body.key;
  if (!validSteamWebApiKey(key)) {
    throw validationError("'key' must be exactly 32 hexadecimal characters.");
  }
  steamKeyConfigured = true;
  steamKeyLast4 = key.slice(-4).toUpperCase();
  return handleGetSteamKey();
}

function handleDeleteSteamKey() {
  resetDemoSteamRelay();
  return null; // real endpoint answers 204 No Content
}

function requireSteamConfigured() {
  if (!steamKeyConfigured) {
    throw conflict("The Steam Web API relay is not configured. Set a key first (PUT /v1/steam/key).");
  }
}

function requireValidSteamId(raw) {
  const steamid = validSteamId64(typeof raw === "string" ? raw : String(raw ?? ""));
  if (!steamid) {
    throw validationError(`'${raw}' is not a valid SteamID64.`);
  }
  return steamid;
}

// ---------------------------------------------------------------------
// Projections (internal seed shape -> exact wire shape)
// ---------------------------------------------------------------------

function appSizeBytes(depots) {
  if (depots.length === 0) return null; // unmapped
  return depots.reduce((sum, d) => sum + (d.size_bytes ?? 0), 0);
}

function gameSummary(g) {
  return {
    appid: g.appid,
    name: g.name,
    status: g.status,
    last_prefill_at: g.last_prefill_at,
    // WP 4a.4: was missing from this projection entirely — the real
    // GameSummary model (api/vault_api/routers/games.py) has carried this
    // field since the WP 4c mini-WP, and the detail sheet's "confirmed
    // current" wording (lib/detail-wording.js) needs it to exercise all
    // three branches in demo mode.
    last_manifest_check: g.last_manifest_check,
    depot_count: g.depots.length,
    size_bytes: appSizeBytes(g.depots),
    needs_force: g.needs_force,
  };
}

function gameDetail(g) {
  return {
    appid: g.appid,
    name: g.name,
    status: g.status,
    last_prefill_at: g.last_prefill_at,
    last_manifest_check: g.last_manifest_check,
    depots: g.depots.map(({ depotid, shared, size_bytes }) => ({
      depotid,
      shared,
      size_bytes,
    })),
    size_bytes: appSizeBytes(g.depots),
    needs_force: g.needs_force,
  };
}

function jobSummary(j) {
  const {
    id,
    appid,
    type,
    status,
    created_at,
    started_at,
    finished_at,
    updated,
    up_to_date,
    summary_parse_ok,
    gc_execute,
    paused_at,
    stop_request,
  } = j;
  return {
    id,
    appid,
    type,
    status,
    created_at,
    started_at,
    finished_at,
    updated,
    up_to_date,
    summary_parse_ok,
    gc_execute,
    paused_at,
    stop_request,
  };
}

function jobDetail(j) {
  return { ...jobSummary(j), log_excerpt: j.log_excerpt };
}

// ---------------------------------------------------------------------
// Demo-only simulation
// ---------------------------------------------------------------------

function findGame(appid) {
  return games.find((g) => g.appid === appid);
}
function findJob(id) {
  return jobs.find((j) => j.id === id);
}

/** Advance one running job a step closer to "done" (see _demoTicksLeft above).
 * Branches on `job.type` (WP 4a.4 addition) — a "gc" job never touches
 * `apps.status`/`last_prefill_at` (api/README.md "Garbage collection": "What
 * a GC job does to app state: nothing to apps.status or last_prefill_at"),
 * so it needs its own completion path rather than falling through the
 * prefill one below. */
function advanceJob(job) {
  if (job.status !== "running" || typeof job._demoTicksLeft !== "number") return;
  job._demoTicksLeft -= 1;
  if (job._demoTicksLeft > 0) return;

  delete job._demoTicksLeft;
  job.finished_at = new Date().toISOString();

  if (job.type === "gc") {
    finishGcJob(job);
    return;
  }

  job.status = "done";
  job.updated = 1;
  job.up_to_date = 0;
  job.summary_parse_ok = true;
  job.log_excerpt += "\n[vault-api] worker: Prefilled 1 app.";

  const game = findGame(job.appid);
  if (game) {
    game.status = "done";
    game.last_prefill_at = job.finished_at;
    game.needs_force = false;
  }
}

/**
 * Complete a GC job (WP 4a.4; extended WP 4a.8) with a log_excerpt shaped
 * exactly like the real `GC totals (DRY RUN)`/`GC totals (EXECUTED)` lines
 * — every field name `api/vault_api/gc_execute.py`'s `GcRunReport.log_text`
 * writes, in the same order, not just the two or three
 * `lib/gc-log-summary.js` happens to parse — so demo mode exercises the
 * REAL production parse path end to end, including `held_back` (WP 4a.8:
 * before this change the demo log hardcoded `held_back=0 (0 bytes)` in
 * every scenario, so that branch of the parser — and the detail sheet's "N
 * chunks held back" note — was never exercised by demo mode at all).
 *
 * The demo model tracks no manifests at all, so "what is reclaimable" and
 * "what is held back" are two per-game counters
 * (`_demoGcReclaimableBytes`/`_demoGcHeldBackBytes`, see makeGame()'s
 * header) rather than a real orphan-chunk scan: a dry run reports both, an
 * execute run "collects" only the reclaimable half (decrementing it to 0 —
 * held_back chunks are a TIME rule an execute run does not touch, see
 * makeGame()'s header) — so a second dry run against the same game
 * honestly reports nothing left to find *except* whatever is still held
 * back, matching the real endpoint's "the plan is built when the job runs"
 * and "an executing run... invalidates the size cache" behaviour without
 * pretending to model chunk-level accounting. Every count field this
 * function cannot derive from those two counters (`already_gone`,
 * `dedupe_removed`, `problems`, `declined`) is honestly `0` — the demo
 * model has no failure/dedupe scenarios to report — and `depots_touched`/
 * `planned_depots`/`needs_force_set_for` use the game's own depot/appid
 * only when something was actually planned, mirroring the real report's
 * "empty unless something happened" properties.
 */
function finishGcJob(job) {
  job.status = "done";
  const game = findGame(job.appid);
  const reclaimable = game ? game._demoGcReclaimableBytes || 0 : 0;
  const heldBack = game ? game._demoGcHeldBackBytes || 0 : 0;
  const depotid = game && game.depots.length ? game.depots[0].depotid : null;
  const wouldDeleteCount = reclaimable > 0 ? 1 : 0;
  const heldBackCount = heldBack > 0 ? 1 : 0;

  if (job.gc_execute) {
    const chunksRemoved = wouldDeleteCount;
    const touchedDepots = chunksRemoved > 0 && depotid != null ? [depotid] : [];
    const flaggedAppids = chunksRemoved > 0 ? [job.appid] : [];
    job.log_excerpt +=
      `\n[vault-api] GC totals (EXECUTED): chunks_removed=${chunksRemoved} ` +
      `bytes_freed=${reclaimable} already_gone=0 dedupe_removed=0 ` +
      "dedupe_bytes_freed=0 " +
      `total_bytes_freed=${reclaimable} problems=0 declined=0 ` +
      `held_back=${heldBackCount} (${heldBack} bytes) ` +
      `depots_touched=[${touchedDepots.join(", ")}] ` +
      `needs_force_set_for=[${flaggedAppids.join(", ")}]`;
    if (game) game._demoGcReclaimableBytes = 0;
  } else {
    const orphans = wouldDeleteCount + heldBackCount;
    const orphanBytes = reclaimable + heldBack;
    const plannedDepots = depotid != null && orphans > 0 ? [depotid] : [];
    job.log_excerpt +=
      `\n[vault-api] GC totals (DRY RUN): orphans=${orphans} (${orphanBytes} bytes) ` +
      `held_back=${heldBackCount} (${heldBack} bytes) ` +
      `would_delete=${wouldDeleteCount} (${reclaimable} bytes) ` +
      "reclaimable_dedupe_bytes=0 " +
      `planned_depots=[${plannedDepots.join(", ")}]. ` +
      'NOTHING was deleted — re-run with {"execute": true} to reclaim it.';
  }
}

function tickAllJobs() {
  for (const job of jobs) advanceJob(job);
}

// ---------------------------------------------------------------------
// Request routing
// ---------------------------------------------------------------------

function notFound(detail) {
  return new ApiError(ERROR_KINDS.NOT_FOUND, detail, { status: 404, detail });
}
function validationError(detail) {
  return new ApiError(ERROR_KINDS.VALIDATION, detail, { status: 422, detail });
}
function conflict(detail) {
  return new ApiError(ERROR_KINDS.VALIDATION, detail, { status: 409, detail });
}

const JOB_ID_RE = /^\/v1\/jobs\/(\d+)$/;
const JOB_PAUSE_RE = /^\/v1\/jobs\/(\d+)\/pause$/;
const JOB_RESUME_RE = /^\/v1\/jobs\/(\d+)\/resume$/;
const GAME_ID_RE = /^\/v1\/games\/(\d+)$/;
const CACHE_APPID_RE = /^\/v1\/cache\/(\d+)$/;
const GC_APPID_RE = /^\/v1\/cache\/(\d+)\/gc$/;

function jobControlResponse(job, { status, outcome, detail }) {
  job.status = status;
  return { job_id: job.id, status, outcome, detail };
}

/**
 * Handle one request against the demo dataset, mirroring the same
 * (data | throw ApiError) contract api.js's real `request()` offers, so
 * every caller above it is indifferent to which mode is active.
 *
 * @param {string} method
 * @param {string} path
 * @param {{body?: unknown, params?: Record<string, unknown>}} [opts]
 */
export async function demoRequest(method, path, { body, params } = {}) {
  if (method === "GET" && path === "/v1/health") {
    return { status: "ok" };
  }

  if (method === "GET" && path === "/v1/games") {
    return games.map(gameSummary);
  }

  let m;
  if (method === "GET" && (m = path.match(GAME_ID_RE))) {
    const appid = Number(m[1]);
    const game = findGame(appid);
    if (!game) throw notFound(`Unknown appid ${appid}`);
    return gameDetail(game);
  }

  // WP 4a.3: the full depot->app mapping table, mirroring
  // `GET /v1/mapping` (api/README.md) exactly — one row per (depotid,
  // appid) pair. Derived from the same `depots` arrays every other demo
  // route already reads (see the makeGame() note above: this demo model
  // keeps "mapping" and "on-disk size" in one list), so a demo bulk-delete
  // confirm sees the same shared-depot ownership the rest of demo mode
  // already assumes.
  if (method === "GET" && path === "/v1/mapping") {
    const rows = [];
    for (const g of games) for (const d of g.depots) rows.push({ depotid: d.depotid, appid: g.appid });
    return rows;
  }

  if (method === "GET" && path === "/v1/jobs") {
    tickAllJobs();
    const limit = Number(params?.limit ?? 20);
    return jobs
      .slice()
      .sort((a, b) => b.id - a.id)
      .slice(0, limit)
      .map(jobSummary);
  }

  if (method === "GET" && (m = path.match(JOB_ID_RE))) {
    tickAllJobs();
    const id = Number(m[1]);
    const job = findJob(id);
    if (!job) throw notFound(`Unknown job id ${id}`);
    return jobDetail(job);
  }

  if (method === "POST" && path === "/v1/prefill") {
    const appids = Array.isArray(body?.appids) ? body.appids : [];
    if (appids.length === 0) throw validationError("appids must be a non-empty list");
    // All-or-nothing, BEFORE any job is created: real body validation is a
    // Pydantic model (PrefillRequest, api/vault_api/routers/jobs.py) that
    // rejects the whole request if any appid fails `AppId`'s `>= 1`
    // constraint — a 422 must never leave a partial side effect behind
    // (WP 4a.2 review fix: one bad id among several good ones used to still
    // queue a job for the good ones before the throw).
    for (const appid of appids) {
      if (!Number.isInteger(appid) || appid < 1) {
        throw validationError(`invalid appid ${appid}`);
      }
    }
    return appids.map((appid) => {
      const existing = jobs.find(
        (j) => j.appid === appid && ["queued", "running", "paused"].includes(j.status),
      );
      if (existing) {
        return { appid, job_id: existing.id, status: existing.status, deduplicated: true };
      }
      const game = findGame(appid);
      const job = {
        id: nextJobId++,
        appid,
        type: "prefill",
        status: "queued",
        created_at: new Date().toISOString(),
        started_at: null,
        finished_at: null,
        updated: null,
        up_to_date: null,
        summary_parse_ok: null,
        gc_execute: null,
        paused_at: null,
        stop_request: null,
        log_excerpt: "[vault-api] queued.",
        _demoTicksLeft: 3,
      };
      jobs.unshift(job);
      if (!game) {
        games.push(makeGame({ appid, name: `App ${appid}`, status: "idle", depots: [] }));
      }
      // First tick already flips it to "running" so a demo poll shortly
      // after enqueueing sees visible progress, matching the mockup's
      // "job start" transition rather than sitting at "queued" forever
      // (this module has no real worker draining a queue).
      job.status = "running";
      job.started_at = job.created_at;
      return { appid, job_id: job.id, status: job.status, deduplicated: false };
    });
  }

  if (method === "DELETE" && (m = path.match(JOB_ID_RE))) {
    const id = Number(m[1]);
    const job = findJob(id);
    if (!job) throw notFound(`Unknown job id ${id}`);
    if (["done", "error", "cancelled"].includes(job.status)) {
      throw conflict(`Job ${id} already finished`);
    }
    const outcome = job.status === "running" ? "requested" : "immediate";
    // Demo simplification: there is no separate background worker to defer
    // to, so a "requested" cancel of a running job settles immediately
    // rather than on some later tick. It MUST settle to 'cancelled', not
    // keep counting down toward 'done' (WP 4a.2 review fix: a cancelled
    // job used to keep ticking to completion in tickAllJobs() and fire the
    // very job_finished notification cancellation is supposed to suppress
    // — see notifications.js's "cancelled is deliberately silent").
    delete job._demoTicksLeft;
    job.finished_at = new Date().toISOString();
    return jobControlResponse(job, {
      status: "cancelled",
      outcome,
      detail:
        outcome === "requested"
          ? "Cancellation requested; poll GET /v1/jobs/{id}."
          : "Job cancelled.",
    });
  }

  if (method === "POST" && (m = path.match(JOB_PAUSE_RE))) {
    const id = Number(m[1]);
    const job = findJob(id);
    if (!job) throw notFound(`Unknown job id ${id}`);
    if (job.type !== "prefill" || job.status !== "running") {
      throw conflict(`Job ${id} cannot be paused from status ${job.status}`);
    }
    delete job._demoTicksLeft;
    job.paused_at = new Date().toISOString();
    return jobControlResponse(job, {
      status: "paused",
      outcome: "requested",
      detail: "Pause requested; poll GET /v1/jobs/{id}.",
    });
  }

  if (method === "POST" && (m = path.match(JOB_RESUME_RE))) {
    const id = Number(m[1]);
    const job = findJob(id);
    if (!job) throw notFound(`Unknown job id ${id}`);
    if (job.status !== "paused") throw conflict(`Job ${id} is not paused`);
    job.status = "running";
    job._demoTicksLeft = 2;
    return jobControlResponse(job, {
      status: "queued",
      outcome: "resumed",
      detail: "Job resumed at the front of the queue.",
    });
  }

  if (method === "DELETE" && (m = path.match(CACHE_APPID_RE))) {
    const appid = Number(m[1]);
    const game = findGame(appid);
    if (!game || game.depots.length === 0) {
      throw notFound(`App ${appid} has no depot mappings, so there is nothing to delete.`);
    }
    const activeJob = jobs.find(
      (j) => j.appid === appid && ["queued", "running", "paused"].includes(j.status),
    );
    if (activeJob) {
      const label = activeJob.type === "gc" ? "GC" : "Prefill";
      throw conflict(`${label} job ${activeJob.id} for app ${appid} is ${activeJob.status}.`);
    }

    // Mirrors api/vault_api/routers/cache.py's CacheDeletionOut exactly:
    //   deleted_depots:  {depotid, size_bytes_freed, shared_with_uncached}
    //   skipped_shared:  {depotid, shared_with}
    //   failed:          {depotid, error}   (never populated here — this
    //                     demo model has no filesystem to fail against)
    //
    // ADR-0003 shared-depot protection (the part the previous version of
    // this handler skipped entirely): a depot mapped by another game that
    // currently HAS cache content is never deleted — it is reported in
    // skipped_shared instead. A depot whose every other mapping is
    // currently uncached (a "last cached remnant") IS deleted, flagged via
    // shared_with_uncached rather than merged with an ordinary exclusive
    // deletion. Re-derived per depot against the CURRENT (not
    // request-start) state, same as the real endpoint's execute-time
    // recheck (`current_co_owners` there).
    //
    // "Has cache content" mirrors the real predicate exactly
    // (`deletion._has_cache_content`): it is a STATUS check
    // (status/last_prefill_at/active-job), not a live disk scan — a
    // depot's bytes being physically still present on a co-owner's shared
    // mapping does NOT by itself count, because `DELETE /v1/cache/{appid}`
    // unconditionally resets that app's own status to 'idle' and
    // last_prefill_at to null even when everything it mapped was
    // protected shared content. This is what actually lets a shared depot
    // become a "last cached remnant" on a LATER call — a depots-array-only
    // proxy (a co-owner's own retained mapping to the very depot being
    // evaluated) can never reach that state, since it is trivially always
    // "true" for the depot in question.
    function hasCacheContent(g) {
      const hasActiveJob = jobs.some(
        (j) => j.appid === g.appid && ["queued", "running", "paused"].includes(j.status),
      );
      const idle = g.status === "idle";
      const neverPrefilled = g.last_prefill_at === null;
      return !(idle && neverPrefilled && !hasActiveJob);
    }
    function otherOwners(depotid) {
      return games.filter(
        (g) => g.appid !== appid && g.depots.some((d) => d.depotid === depotid),
      );
    }

    const deletedDepots = [];
    const skippedShared = [];
    const remnantCoOwnerAppids = new Set();

    for (const depot of game.depots) {
      const others = otherOwners(depot.depotid);
      const cachedOthers = others.filter(hasCacheContent);
      if (cachedOthers.length > 0) {
        skippedShared.push({ depotid: depot.depotid, shared_with: others.map((g) => g.appid) });
        continue;
      }
      deletedDepots.push({
        depotid: depot.depotid,
        size_bytes_freed: depot.size_bytes ?? 0,
        shared_with_uncached: others.map((g) => g.appid),
      });
      for (const owner of others) remnantCoOwnerAppids.add(owner.appid);
    }

    // The bytes are actually gone from disk for EVERY game that mapped a
    // deleted depot, not just the one this request targeted — this demo
    // model keeps "mapping" and "on-disk size" in one list (see the
    // makeGame() note above) rather than the real schema's two separate
    // facts, so a deleted depot is dropped from every game's depot list,
    // not only the requested app's.
    const deletedIds = new Set(deletedDepots.map((d) => d.depotid));
    for (const g of games) {
      g.depots = g.depots.filter((d) => !deletedIds.has(d.depotid));
    }
    for (const coOwnerAppid of remnantCoOwnerAppids) {
      const coOwner = findGame(coOwnerAppid);
      if (coOwner) coOwner.needs_force = true;
    }

    // Real endpoint: `new_status = 'error' if failed else 'idle'`,
    // unconditionally — even a request that only hit skipped_shared (every
    // mapped depot protected, nothing actually removed) still resets to
    // 'idle', and last_prefill_at is cleared either way. This demo never
    // produces a filesystem failure, so it is always 'idle' here.
    game.status = "idle";
    game.last_prefill_at = null;
    // Real rule (ADR-0006 decision 2): set only when something in
    // deleted_depots or failed actually changed/left uncertain what's on
    // disk for THIS app; left untouched when everything mapped was
    // protected shared content (nothing exclusive to touch).
    if (deletedDepots.length > 0) game.needs_force = true;

    const totalBytesFreed = deletedDepots.reduce((sum, d) => sum + d.size_bytes_freed, 0);

    return {
      appid,
      deleted_depots: deletedDepots,
      skipped_shared: skippedShared,
      failed: [],
      total_bytes_freed: totalBytesFreed,
    };
  }

  // WP 4a.4: POST /v1/cache/{appid}/gc — mirrors api/README.md's
  // "Garbage collection" section: 404 for an unknown app or one with no
  // depot mappings (same reasoning/wording as DELETE), a 422-shaped
  // rejection for an unrecognised body field or a non-boolean `execute`
  // (StrictBool posture — see vault_api/routers/cache.py's GcRequest kdoc),
  // and — the one deliberate difference from DELETE — NO 409 for an active
  // job (the real endpoint queues onto the single worker, which serializes
  // GC against prefills by construction; see that router's "No 409 for an
  // active job" note). Dedupe is scoped to the SAME mode (a dry run and an
  // execute run never dedupe into each other, `jobs.enqueue_gc`'s rule).
  if (method === "POST" && (m = path.match(GC_APPID_RE))) {
    const appid = Number(m[1]);
    const game = findGame(appid);
    if (!game) {
      throw notFound(`Unknown appid ${appid} — vault-api tracks no such app, so there is nothing to garbage-collect.`);
    }
    if (game.depots.length === 0) {
      throw notFound(`App ${appid} has no depot mappings, so there is nothing to garbage-collect.`);
    }
    if (body != null) {
      const unknown = Object.keys(body).filter((k) => k !== "execute");
      if (unknown.length) throw validationError(`unrecognised field(s) in GC request body: ${unknown.join(", ")}`);
      if ("execute" in body && typeof body.execute !== "boolean") {
        throw validationError("'execute' must be a literal JSON boolean.");
      }
    }
    const execute = !!(body && body.execute);

    const existing = jobs.find(
      (j) => j.appid === appid && j.type === "gc" && j.gc_execute === execute && ["queued", "running", "paused"].includes(j.status),
    );
    if (existing) {
      return {
        appid,
        job_id: existing.id,
        status: existing.status,
        type: "gc",
        mode: execute ? "execute" : "dry-run",
        execute,
        deduplicated: true,
      };
    }

    const job = {
      id: nextJobId++,
      appid,
      type: "gc",
      status: "queued",
      created_at: new Date().toISOString(),
      started_at: null,
      finished_at: null,
      updated: null,
      up_to_date: null,
      summary_parse_ok: null,
      gc_execute: execute,
      paused_at: null,
      stop_request: null,
      log_excerpt: `[vault-api] GC for app ${appid}: ${execute ? "EXECUTE" : "DRY RUN"}.`,
      _demoTicksLeft: 1,
    };
    jobs.unshift(job);
    job.status = "running";
    job.started_at = job.created_at;

    return {
      appid,
      job_id: job.id,
      status: job.status,
      type: "gc",
      mode: execute ? "execute" : "dry-run",
      execute,
      deduplicated: false,
    };
  }

  if (method === "GET" && path === "/v1/cache/summary") {
    const allDepots = new Map();
    for (const g of games) for (const d of g.depots) allDepots.set(d.depotid, d.size_bytes ?? 0);
    const totalBytes = [...allDepots.values()].reduce((a, b) => a + b, 0);
    const topConsumers = games
      .map((g) => ({ appid: g.appid, name: g.name, size_bytes: appSizeBytes(g.depots) ?? 0 }))
      .sort((a, b) => b.size_bytes - a.size_bytes)
      .slice(0, 10);
    return {
      total_bytes: totalBytes,
      top_consumers: topConsumers,
      unmapped_depots: { count: 0, size_bytes: 0 },
      free_disk_bytes: 500_000_000_000,
    };
  }

  if (method === "GET" && path === "/v1/clients") {
    return clients.map((c) => ({ ...c }));
  }

  if (method === "GET" && path === "/v1/settings") {
    return handleGetSettings();
  }
  if (method === "PATCH" && path === "/v1/settings") {
    return handlePatchSettings(body);
  }

  if (method === "GET" && path === "/v1/steam/key") {
    return handleGetSteamKey();
  }
  if (method === "PUT" && path === "/v1/steam/key") {
    return handlePutSteamKey(body);
  }
  if (method === "DELETE" && path === "/v1/steam/key") {
    return handleDeleteSteamKey();
  }
  if (method === "GET" && path === "/v1/steam/owned-games") {
    requireSteamConfigured();
    requireValidSteamId(params?.steamid);
    return { configured: true, game_count: DEMO_OWNED_GAMES.length, games: DEMO_OWNED_GAMES.map((g) => ({ ...g })) };
  }
  if (method === "GET" && path === "/v1/steam/player-summaries") {
    requireSteamConfigured();
    const steamid = requireValidSteamId(params?.steamid);
    return { configured: true, players: [demoPlayerSummary(steamid)] };
  }

  throw new ApiError(ERROR_KINDS.NOT_FOUND, `Demo mode has no route for ${method} ${path}`, {
    status: 404,
    detail: `${method} ${path}`,
  });
}
