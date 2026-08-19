/**
 * Decision-support statements for the Phase 4h suggestions panel (WP 4h.2).
 *
 * `docs/PROJECT_PLAN.md`'s Phase 4h section names the statement families this
 * project is allowed to make ("decision support about the cache, not
 * taste") and explicitly rejects the Steam-storefront kind ("what should I
 * play next" via tags/reviews/similar titles) — this module implements only
 * the accepted families, each one traceable to a plan bullet:
 *
 *   - `PLAYABLE_NOW` -> plan: "43 GB cached, 0 minutes played" / "these games
 *     are fully cached and you have never started them — playable now, no
 *     wait." Requires playtime data (gated server-side by ADR-0010 — see
 *     below) AND a real, non-fabricated zero, never an absent value.
 *   - `STALE_CONFIRMATION` -> plan: "Not confirmed current for 40 days" —
 *     `apps.last_manifest_check`'s age, already relayed with no privacy gate
 *     at all (this is a cache-management fact, not personal data).
 *   - `CHANGED_RECENTLY` / `STABLE` -> plan: "Changes every three days" vs
 *     "unchanged for two years", corrected to WP 4h.1's own honesty rule:
 *     `depot_manifests` never stores a change HISTORY, only the latest
 *     manifest per depot, so a rate/cadence claim is unsupportable — worded
 *     here as a single past-tense fact ("last changed N days ago" /
 *     "unchanged for the N days we've watched it"), matching
 *     `manifest_change_frequency`'s own "changed" (has-changed-at-least-once)
 *     semantics, never "changes every N days".
 *
 * **Deliberately NOT implemented here** (plan-acceptable in principle, no
 * buildable data source in THIS package's footprint — named rather than
 * silently dropped):
 *   - "Installed on your PC, not in the cache" needs a local Steam install
 *     list, which only the Android agent has; the web frontend has no
 *     comparable source.
 *   - "Deleting frees 12 of 43 GB; the other 31 sits in shared depots" needs
 *     each candidate's full depot list (`GET /v1/games/{appid}`, not the
 *     `GET /v1/games` list this module is fed from) — an extra per-card
 *     fetch this WP does not add without measuring its cost first (the same
 *     "no new poll loop without measurement" rule the brief applies to
 *     store.js resources applies to on-demand fetches too).
 *
 * **The privacy gate (ADR-0010, binding).** `playtime_forever`/
 * `rtime_last_played` leave vault-api's Steam relay at all only when the
 * operator has explicitly turned on the corresponding env-only setting, and
 * ship OFF by default. This module never fetches that data itself (it takes
 * whatever `playtimeByAppid` the caller already has); its OWN honesty rule
 * mirrors WP 4h.1's absent-vs-zero pin: `playtime_forever === 0` is a real,
 * printable claim ("never started"), `undefined`/`null`/negative/non-finite
 * is UNKNOWN and never degrades into a fabricated zero. `rtime_last_played`
 * is accepted in the same map (future-proofing the shape against
 * `OwnedGameOut`) but is NEVER read by any statement-producing code path in
 * this file — see the negative pin in web/tests/decision-support.test.js:
 * "when did this person last play" is the sharper, more sensitive of the two
 * facts (ADR-0010's own wording) and the plan's accepted statement families
 * never need it, so the safest structural guarantee is to never wire it to
 * a string at all, not to trust a wording review to keep catching it.
 *
 * **The negative privacy pin, stated as a rule this module's shape
 * enforces rather than a style guide sentence:** no statement produced here
 * ever names a NON-zero playtime number, a last-played date, or an
 * inactivity duration ("haven't played in N days") — the plan's binding
 * stance is "no number held up to someone else in the living room", and
 * `PLAYABLE_NOW`'s only use of playtime is the boolean fact "exactly zero",
 * which is the one example the plan itself blesses by name.
 *
 * Pure — no DOM, no fetch, no timers, no `Date.now()` default hidden inside
 * (every function taking "now" takes it as an explicit, defaulted parameter
 * so a test can pin exact day-boundary arithmetic). See
 * web/tests/decision-support.test.js for the degrade-tier and ranking pins.
 */

const MS_PER_DAY = 86_400_000;

export const SUGGESTION_KIND = Object.freeze({
  PLAYABLE_NOW: "playable_now",
  STALE_CONFIRMATION: "stale_confirmation",
  CHANGED_RECENTLY: "changed_recently",
  STABLE: "stable",
});

// Rank, ascending (lower = shown first) — see buildSuggestions' header for
// why this order (roughly: "actionable now" before "informational only").
const KIND_PRIORITY = Object.freeze({
  [SUGGESTION_KIND.PLAYABLE_NOW]: 0,
  [SUGGESTION_KIND.STALE_CONFIRMATION]: 1,
  [SUGGESTION_KIND.CHANGED_RECENTLY]: 2,
  [SUGGESTION_KIND.STABLE]: 3,
});

// A cache-management threshold, not a privacy one — how many days without a
// confirming run before it is worth mentioning at all. Chosen independently
// of VAULT_GC_GRACE_DAYS/the 14-day manifest-observation window (both
// different concerns: those are server-side data-sufficiency rules, this is
// purely "is this old enough to be worth a line in a short suggestions
// list").
const STALE_CONFIRMATION_THRESHOLD_DAYS = 30;

/**
 * Whole days between `iso` and `now` (both real dates), or `null` if `iso`
 * cannot be parsed. Floors rather than rounds — "40 days" means at least 40
 * full days have actually elapsed, never a rounded-up 39.6.
 * @param {string} iso
 * @param {number} nowMs
 * @returns {number | null}
 */
function daysSince(iso, nowMs) {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const deltaMs = nowMs - t;
  if (deltaMs < 0) return null; // a clock-skewed "future" timestamp is not honestly "N days ago"
  return Math.floor(deltaMs / MS_PER_DAY);
}

/**
 * A real, printable playtime-forever value, or `null` for "unknown" — never
 * a fabricated zero. Mirrors `api/vault_api/steam_relay.py`'s own
 * `_coerce_nonneg_int` contract client-side: absent, non-numeric, negative
 * or non-finite are all "we don't actually know", not "zero".
 * @param {unknown} value
 * @returns {number | null}
 */
function realPlaytimeForeverOrNull(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

/**
 * A real, whole, non-negative day count, or `null` — the same honesty
 * contract as {@link realPlaytimeForeverOrNull}, applied to
 * `manifest_observation_days`/`manifest_days_since_last_change` (Opus
 * review should-fix S4, WP 4h.2 fix round). These two fields lacked ANY
 * numeric sanitation before this fix — only `typeof === "number"`, which is
 * true for `NaN`/`Infinity`/negatives/fractions too. Probed and confirmed
 * rendering before this guard existed: `NaN` -> "Last changed NaN days
 * ago.", `-5` -> "Last changed -5 days ago.", `3.7` -> "...3.7 days ago.",
 * `Infinity` -> "...Infinity days ago.". Today's server-side contract
 * (`depot_manifests.py`) already only ever produces a real, clamped
 * integer, so this is defense in depth against a future regression or a
 * malformed demo fixture, not a response to a currently-reachable server
 * value — but a client-side statement generator should not silently trust
 * that boundary. `Number.isSafeInteger` (not merely `Number.isInteger`)
 * additionally rejects a pathologically large-but-integral value like
 * `1e+21` (also probed) — a value so large it cannot be a real day count
 * derived from any realistic timestamp, and one `Number.isInteger` alone
 * would still accept.
 * @param {unknown} value
 * @returns {number | null}
 */
function realNonNegativeIntOrNull(value) {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

/**
 * The single best statement for one game, or `null` when there is honestly
 * nothing to say (matches WP 4h.1's own rule: a game with no manifest data
 * and no known-zero playtime gets silence, not a fabricated claim).
 *
 * @param {{
 *   appid: number, name?: string|null, status?: string, size_bytes?: number|null,
 *   last_manifest_check?: string|null, manifest_change_frequency?: string|null,
 *   manifest_observation_days?: number|null, manifest_days_since_last_change?: number|null,
 * }} game a `GameSummary`-shaped row (`GET /v1/games`).
 * @param {{playtimeForever?: unknown, nowMs?: number}} [opts]
 * @returns {{kind: string, text: string} | null}
 */
export function statementForGame(game, { playtimeForever, nowMs = Date.now() } = {}) {
  if (!game) return null;

  // PLAYABLE_NOW — highest priority: an honest, non-judgemental positive
  // framing of "cached and never started", the plan's own headline example.
  // Gated on a REAL cached size (never claim "playable now" for an app with
  // nothing on disk) and a REAL, non-fabricated zero playtime.
  const realPlaytime = realPlaytimeForeverOrNull(playtimeForever);
  const sizeBytes = typeof game.size_bytes === "number" && game.size_bytes > 0 ? game.size_bytes : null;
  if (realPlaytime === 0 && sizeBytes !== null) {
    return { kind: SUGGESTION_KIND.PLAYABLE_NOW, text: "Fully cached and never started — playable right now." };
  }

  // STALE_CONFIRMATION — no privacy gate at all (this is a fact about the
  // CACHE's own confirmed-current state, not about the person using it).
  if (game.last_manifest_check) {
    const days = daysSince(game.last_manifest_check, nowMs);
    if (days !== null && days >= STALE_CONFIRMATION_THRESHOLD_DAYS) {
      return { kind: SUGGESTION_KIND.STALE_CONFIRMATION, text: `Not confirmed current for ${days} days.` };
    }
  }

  // CHANGED_RECENTLY / STABLE — WP 4h.1's own four-state field, `null` and
  // "insufficient_data" both silently produce no statement here (neither is
  // "changed" or "stable"): "we never looked" and "we looked, but not
  // enough" are honestly nothing to suggest ON, not a fourth statement kind.
  if (game.manifest_change_frequency === "changed") {
    const n = realNonNegativeIntOrNull(game.manifest_days_since_last_change);
    if (n !== null) {
      return {
        kind: SUGGESTION_KIND.CHANGED_RECENTLY,
        text: `Last changed ${n} day${n === 1 ? "" : "s"} ago.`,
      };
    }
  }
  if (game.manifest_change_frequency === "stable") {
    const n = realNonNegativeIntOrNull(game.manifest_observation_days);
    if (n !== null) {
      return {
        kind: SUGGESTION_KIND.STABLE,
        text: `Unchanged for the ${n} day${n === 1 ? "" : "s"} we've been watching.`,
      };
    }
  }

  return null;
}

/**
 * The panel's full, ranked suggestion list plus an overall degrade tier.
 *
 * Tier ladder (brief's own language): `"full"` — at least one statement used
 * REAL playtime data; `"frequency"` — at least one statement exists, none of
 * them needed playtime; `"insufficient_data"` — nothing qualified at all
 * (WP 4h.1's "no data yet is a first-class, designed state, not an error and
 * not an empty box" rule, applied here at the whole-panel level: a fresh
 * vault legitimately produces zero statements for ~14 days by 4h.1's own
 * window, and the caller renders a friendly, honest message for that case
 * rather than nothing).
 *
 * @param {object[] | null | undefined} games `GET /v1/games` snapshot
 *   (`GameSummary[]`).
 * @param {{
 *   playtimeByAppid?: Map<number, unknown>,
 *   limit?: number,
 *   nowMs?: number,
 * }} [opts] `playtimeByAppid` maps appid -> `playtime_forever` (a plain
 *   number, or the raw `OwnedGameOut` field value — only `playtime_forever`
 *   is ever read, see this module's header for why `rtime_last_played` is
 *   deliberately never consulted). Absent/omitted means "no playtime data at
 *   all" (the ADR-0010 default: both relay keys ship off), never "everyone
 *   has zero playtime".
 * @returns {{tier: "full"|"frequency"|"insufficient_data", items: Array<{appid: number, name: string, kind: string, text: string}>}}
 */
export function buildSuggestions(games, { playtimeByAppid, limit = 5, nowMs = Date.now() } = {}) {
  const list = Array.isArray(games) ? games : [];
  const ptMap = playtimeByAppid instanceof Map ? playtimeByAppid : new Map();

  const candidates = [];
  for (const game of list) {
    if (!game || typeof game.appid !== "number") continue;
    const statement = statementForGame(game, { playtimeForever: ptMap.get(game.appid), nowMs });
    if (!statement) continue;
    candidates.push({
      appid: game.appid,
      name: typeof game.name === "string" && game.name ? game.name : `App ${game.appid}`,
      kind: statement.kind,
      text: statement.text,
    });
  }

  candidates.sort((a, b) => {
    const pa = KIND_PRIORITY[a.kind] ?? 99;
    const pb = KIND_PRIORITY[b.kind] ?? 99;
    if (pa !== pb) return pa - pb;
    return a.appid - b.appid; // deterministic tiebreak, matches GameSummary's own appid ordering
  });

  const items = candidates.slice(0, Math.max(0, limit));
  const tier = items.some((i) => i.kind === SUGGESTION_KIND.PLAYABLE_NOW)
    ? "full"
    : items.length > 0
      ? "frequency"
      : "insufficient_data";

  return { tier, items };
}
